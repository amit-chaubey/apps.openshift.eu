#!/usr/bin/env python3
"""A2A server in front of the OpenHands agent-server, one agent per conversation.

The instance is the base agent at /: every A2A contextId becomes its own conversation,
tagged a2acontext=<contextId> so the mapping survives restarts. Every titled top-level
conversation is also a sub-agent "<instance>/<title>" at /agents/<slug>/, which continues
that conversation; GET /agents lists them. Agent cards are public; JSON-RPC calls need a
bearer token: the static credential, a ServiceAccount token from a namespace of the tenant,
or an OIDC token whose groups claim names the tenant. Methods: message/send,
message/stream (SSE), tasks/get, tasks/cancel. Runs on the OpenHands image, which ships
starlette, uvicorn, httpx and PyJWT.
"""
import asyncio
import json
import os
import re
import ssl
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import jwt
import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

API = os.environ.get("AGENT_SERVER", "http://127.0.0.1:18000")
KEY = os.environ.get("LOCAL_BACKEND_API_KEY", "")
NAME = os.environ.get("A2A_NAME", "openhands")
DESCRIPTION = os.environ.get("A2A_DESCRIPTION", "OpenHands coding agent")
PUBLIC_URL = os.environ.get("A2A_PUBLIC_URL", "http://127.0.0.1:8080").rstrip("/")
PORT = int(os.environ.get("A2A_PORT", "8080"))
STATIC = os.environ.get("A2A_STATIC_TOKEN", "")
TENANT = os.environ.get("A2A_TENANT", "")
ISSUERS = json.loads(os.environ.get("A2A_JWT_ISSUERS", "[]") or "[]")
WORKSPACE = os.environ.get("A2A_WORKSPACE", "/home/openhands/workspace/a2a")
TAG = "a2acontext"
DONE = {"finished", "idle", "error", "stuck", "paused", "waiting_for_confirmation"}
STATE = {"finished": "completed", "idle": "completed", "error": "failed", "stuck": "failed",
         "paused": "canceled", "waiting_for_confirmation": "input-required"}
ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]
SA = "/var/run/secrets/kubernetes.io/serviceaccount"

http = httpx.AsyncClient(base_url=API, headers={"X-Session-API-Key": KEY}, timeout=60)
tasks: dict[str, dict] = {}
contexts: dict[str, str] = {}


# --- auth: the tenant JWT rules of opencode-a2a (jwt_auth.py) -------------------------

class _TokenJWKClient(jwt.PyJWKClient):
    """Sends the pod's ServiceAccount token, re-read on every fetch since it rotates."""

    def __init__(self, uri, token_file, **kwargs):
        super().__init__(uri, **kwargs)
        self._token_file = token_file

    def fetch_data(self):
        self.headers = {"Authorization": "Bearer " + Path(self._token_file).read_text().strip()}
        return super().fetch_data()


def _issuers():
    out = []
    for e in ISSUERS:
        kind = e.get("kind", "oidc")
        uri = e.get("jwks_uri") or e["issuer"].rstrip("/") + (
            "/openid/v1/jwks" if kind == "kubernetes" else "/protocol/openid-connect/certs")
        kw = {"cache_jwk_set": True, "lifespan": 300}
        if e.get("ca_file"):
            kw["ssl_context"] = ssl.create_default_context(cafile=e["ca_file"])
        client = (_TokenJWKClient(uri, e["token_file"], **kw) if e.get("token_file")
                  else jwt.PyJWKClient(uri, **kw))
        out.append((e["issuer"], kind, tuple(e.get("audiences") or ()), client))
    return out


TRUSTED = _issuers() if TENANT else []


def _member(value: str) -> bool:
    return value == TENANT or value.startswith(TENANT + "-")


def tenant_identity(token: str) -> str | None:
    if not TRUSTED or token.count(".") != 2:
        return None
    try:
        iss = jwt.decode(token, options={"verify_signature": False}).get("iss")
    except jwt.PyJWTError:
        return None
    for issuer, kind, audiences, client in TRUSTED:
        if issuer != iss:
            continue
        try:
            claims = jwt.decode(token, client.get_signing_key_from_jwt(token).key, algorithms=ALGORITHMS,
                                issuer=issuer, audience=list(audiences) or None,
                                options={"verify_aud": bool(audiences), "require": ["exp", "iss"]})
        except jwt.PyJWTError:
            return None
        if kind == "kubernetes":
            k8s = claims.get("kubernetes.io") or {}
            ns = k8s.get("namespace") or ""
            return f"system:serviceaccount:{ns}:{(k8s.get('serviceaccount') or {}).get('name')}" if _member(ns) else None
        groups = claims.get("groups") or []
        groups = [groups] if isinstance(groups, str) else groups
        if any(_member(str(g).lstrip("/")) for g in groups):
            return str(claims.get("email") or claims.get("preferred_username") or claims.get("sub"))
        return None
    return None


async def caller(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if STATIC and token == STATIC:
        return "static"
    return await run_in_threadpool(tenant_identity, token)


# --- agent-server ----------------------------------------------------------------------

def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "conversation"


async def conversations() -> list[dict]:
    out, page = [], None
    for _ in range(20):
        r = await http.get("/api/conversations/search", params={"limit": 100, **({"page_id": page} if page else {})})
        r.raise_for_status()
        body = r.json()
        out += [c for c in body.get("items", []) if c]
        page = body.get("next_page_id")
        if not page:
            break
    return out


async def sub_agents() -> dict[str, dict]:
    """slug -> conversation, for titled top-level conversations not started over A2A."""
    out = {}
    for c in await conversations():
        if not c.get("title") or c.get("parent_conversation_id") or TAG in (c.get("tags") or {}):
            continue
        s = slug(c["title"])
        out[s if s not in out else f"{s}-{c['id'][:6]}"] = c
    return out


async def context_conversation(context_id: str) -> str | None:
    if context_id in contexts:
        return contexts[context_id]
    for c in await conversations():
        if (c.get("tags") or {}).get(TAG) == context_id:
            contexts[context_id] = c["id"]
            return c["id"]
    return None


def text_of(message: dict) -> str:
    return "\n".join(p.get("text", "") for p in message.get("parts", []) if p.get("kind", "text") == "text").strip()


async def send(conversation_id: str | None, context_id: str, text: str) -> str:
    content = [{"type": "text", "text": text}]
    if conversation_id:
        r = await http.post(f"/api/conversations/{conversation_id}/events",
                            json={"role": "user", "content": content, "run": True})
        r.raise_for_status()
        return conversation_id
    body = {
        "workspace": {"kind": "LocalWorkspace", "working_dir": f"{WORKSPACE}/{slug(context_id)}"},
        "initial_message": {"role": "user", "content": content, "run": True},
        "tags": {TAG: context_id, "clientsource": "a2a"},
        "autotitle": True,
    }
    # the persisted settings (model, key, MCP servers) the chart seeds and the UI edits;
    # plaintext because redacted keys would reach the model as asterisks
    settings = await http.get("/api/settings", headers={"X-Expose-Secrets": "plaintext"})
    agent_settings = settings.json().get("agent_settings") if settings.status_code == 200 else None
    if agent_settings:
        body["agent_settings"] = agent_settings
    else:
        profiles = await http.get("/api/agent-profiles")
        profiles.raise_for_status()
        body["agent_profile_id"] = profiles.json().get("active_agent_profile_id")
    r = await http.post("/api/conversations", json=body)
    r.raise_for_status()
    contexts[context_id] = r.json()["id"]
    return contexts[context_id]


async def agent_messages(conversation_id: str, since: str) -> list[dict]:
    r = await http.get(f"/api/conversations/{conversation_id}/events/search",
                       params={"kind": "MessageEvent", "source": "agent", "timestamp__gte": since, "limit": 100})
    r.raise_for_status()
    body = r.json()
    return body.get("items", []) if isinstance(body, dict) else body


def event_text(event: dict) -> str:
    return "\n".join(c.get("text", "") for c in (event.get("llm_message") or {}).get("content", [])
                     if c.get("type") == "text").strip()


async def status(conversation_id: str) -> str:
    r = await http.get(f"/api/conversations/{conversation_id}")
    r.raise_for_status()
    return r.json().get("execution_status") or "idle"


# --- A2A -------------------------------------------------------------------------------

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def agent_message(text: str, task: dict) -> dict:
    return {"kind": "message", "role": "agent", "messageId": str(uuid.uuid4()), "parts": [{"kind": "text", "text": text}],
            "taskId": task["id"], "contextId": task["contextId"]}


def status_event(task: dict, final: bool, text: str | None = None) -> dict:
    st = {"state": task["status"]["state"], "timestamp": now()}
    if text:
        st["message"] = agent_message(text, task)
    return {"kind": "status-update", "taskId": task["id"], "contextId": task["contextId"], "status": st, "final": final}


async def run(task: dict, conversation_id: str | None, text: str):
    """Yields A2A events until the conversation stops."""
    since = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    task["conversation"] = await send(conversation_id, task["contextId"], text)
    task["status"] = {"state": "working", "timestamp": now()}
    yield status_event(task, False)
    seen, started, deadline = set(), time.monotonic(), time.monotonic() + 3600
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        for e in await agent_messages(task["conversation"], since):
            if e["id"] not in seen and event_text(e):
                seen.add(e["id"])
                yield status_event(task, False, event_text(e))
        state = await status(task["conversation"])
        # the run starts asynchronously; idle right after sending means not started yet
        if state in DONE and (state != "idle" or seen or time.monotonic() - started > 20):
            break
    else:
        state = "stuck"
    final = ""
    r = await http.get(f"/api/conversations/{task['conversation']}/agent_final_response")
    if r.status_code == 200:
        final = r.json().get("response", "")
    task["status"] = {"state": STATE.get(state, "completed"), "timestamp": now()}
    if final:
        task["artifacts"] = [{"artifactId": str(uuid.uuid4()), "name": "response", "parts": [{"kind": "text", "text": final}]}]
        yield {"kind": "artifact-update", "taskId": task["id"], "contextId": task["contextId"],
               "artifact": task["artifacts"][0], "lastChunk": True}
    yield status_event(task, True, final or None)


def card(name: str, description: str, url: str, skills: list[dict]) -> dict:
    return {
        "protocolVersion": "0.3.0", "name": name, "description": description, "url": url,
        "preferredTransport": "JSONRPC", "version": "1.0.0",
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}, "security": [{"bearer": []}],
        "skills": skills or [{"id": "code", "name": "coding", "description": description, "tags": ["coding"]}],
    }


async def base_card(request: Request):
    subs = await sub_agents()
    skills = [{"id": s, "name": f"{NAME}/{c['title']}", "description": f"Continue the conversation {c['title']}",
               "tags": ["conversation"]} for s, c in subs.items()]
    return JSONResponse(card(NAME, DESCRIPTION, PUBLIC_URL + "/", [{"id": "code", "name": "coding",
                             "description": DESCRIPTION, "tags": ["coding"]}] + skills))


async def sub_card(request: Request):
    s = request.path_params["slug"]
    c = (await sub_agents()).get(s)
    if not c:
        return JSONResponse({"error": "no such agent"}, status_code=404)
    return JSONResponse(card(f"{NAME}/{c['title']}", f"{DESCRIPTION}: conversation {c['title']}",
                             f"{PUBLIC_URL}/agents/{s}/", []))


async def listing(request: Request):
    return JSONResponse([{"name": f"{NAME}/{c['title']}", "url": f"{PUBLIC_URL}/agents/{s}/",
                          "conversationId": c["id"], "status": c.get("execution_status")}
                         for s, c in (await sub_agents()).items()])


def rpc_error(rid, code: int, message: str, status_code: int = 200):
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}, status_code=status_code)


async def jsonrpc(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return rpc_error(None, -32700, "parse error")
    rid, method, params = body.get("id"), body.get("method"), body.get("params") or {}
    if await caller(request) is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conversation_id = None
    if "slug" in request.path_params:
        c = (await sub_agents()).get(request.path_params["slug"])
        if not c:
            return rpc_error(rid, -32001, "no such agent")
        conversation_id = c["id"]
    if method in ("message/send", "message/stream"):
        message = params.get("message") or {}
        text = text_of(message)
        if not text:
            return rpc_error(rid, -32602, "message has no text part")
        context_id = message.get("contextId") or str(uuid.uuid4())
        if conversation_id is None:
            conversation_id = await context_conversation(context_id)
        task = {"kind": "task", "id": str(uuid.uuid4()), "contextId": context_id,
                "status": {"state": "submitted", "timestamp": now()}, "history": [message]}
        tasks[task["id"]] = task
        if method == "message/send":
            async for _ in run(task, conversation_id, text):
                pass
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": public(task)})

        async def stream():
            yield sse(rid, public(task))
            try:
                async for event in run(task, conversation_id, text):
                    yield sse(rid, event)
            except Exception as exc:  # noqa: BLE001
                task["status"] = {"state": "failed", "timestamp": now()}
                yield sse(rid, status_event(task, True, f"agent-server error: {exc}"))
        return StreamingResponse(stream(), media_type="text/event-stream")
    if method == "tasks/get":
        task = tasks.get(params.get("id", ""))
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": public(task)}) if task else rpc_error(rid, -32001, "task not found")
    if method == "tasks/cancel":
        task = tasks.get(params.get("id", ""))
        if not task:
            return rpc_error(rid, -32001, "task not found")
        if task.get("conversation"):
            await http.post(f"/api/conversations/{task['conversation']}/pause")
        task["status"] = {"state": "canceled", "timestamp": now()}
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": public(task)})
    return rpc_error(rid, -32601, f"method {method} not found")


def public(task: dict) -> dict:
    return {k: v for k, v in task.items() if k != "conversation"}


def sse(rid, result: dict) -> str:
    return "data: " + json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n\n"


async def health(request: Request):
    return JSONResponse({"status": "ok"})


app = Starlette(routes=[
    Route("/healthz", health),
    Route("/.well-known/agent-card.json", base_card),
    Route("/.well-known/agent.json", base_card),
    Route("/agents", listing),
    Route("/agents/{slug}/.well-known/agent-card.json", sub_card),
    Route("/agents/{slug}/.well-known/agent.json", sub_card),
    Route("/agents/{slug}/", jsonrpc, methods=["POST"]),
    Route("/agents/{slug}", jsonrpc, methods=["POST"]),
    Route("/", jsonrpc, methods=["POST"]),
])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
