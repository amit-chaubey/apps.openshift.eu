#!/usr/bin/env python3
"""A2A entry of the pod: checks the caller's bearer JWT, then runs the message as a goose turn.

goose serves the Agent Client Protocol (ACP) over HTTP on loopback. This bridge keeps one
ACP connection to it and maps every A2A contextId to its own goose session, so a caller
continues the conversation it started; the map lives on the data volume and a session is
reloaded after a restart. Methods: message/send, message/stream (SSE), tasks/get,
tasks/cancel, and their A2A v1.0 names; answers use the v0.3 shape that kagent speaks.

A caller sends its own bearer JWT: a ServiceAccount token from a namespace <tenant> or
<tenant>-*, or an OIDC token whose groups claim holds <tenant> or <tenant>-*. Signatures are
RS256, RS384 or RS512, checked against the issuer's JWKS. Agent cards are public. Standard
library only.
"""
import base64
import hashlib
import json
import os
import queue
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8090"))
GOOSE = os.environ.get("GOOSE_URL", "http://127.0.0.1:3284").rstrip("/")
SECRET = os.environ.get("GOOSE_SECRET", "")
TENANT = os.environ["TENANT"]
ISSUERS = json.loads(os.environ.get("JWT_ISSUERS", "[]") or "[]")
NAME = os.environ.get("A2A_NAME", "goose")
DESCRIPTION = os.environ.get("A2A_DESCRIPTION", "Goose agent")
PUBLIC_URL = os.environ.get("A2A_PUBLIC_URL", f"http://127.0.0.1:{PORT}/")
CWD = os.environ.get("A2A_WORKDIR", "/home/goose")
STATE = Path(os.environ.get("STATE_FILE", "/home/goose/.local/share/goose/a2a-contexts.json"))
TURN_TIMEOUT = int(os.environ.get("TURN_TIMEOUT", "3600"))
VERSION = os.environ.get("GOOSE_VERSION", "")
CARDS = {"/.well-known/agent-card.json", "/.well-known/agent.json"}
DIGEST = {
    "RS256": (hashlib.sha256, bytes.fromhex("3031300d060960864801650304020105000420")),
    "RS384": (hashlib.sha384, bytes.fromhex("3041300d060960864801650304020205000430")),
    "RS512": (hashlib.sha512, bytes.fromhex("3051300d060960864801650304020305000440")),
}


def log(text: str) -> None:
    sys.stderr.write(f"a2a-bridge: {text}\n")
    sys.stderr.flush()


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --- caller authentication --------------------------------------------------------------

def b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class Issuer:
    def __init__(self, entry: dict):
        self.issuer = entry["issuer"]
        self.kind = entry.get("kind", "oidc")
        self.audiences = tuple(entry.get("audiences") or ())
        self.uri = entry.get("jwks_uri") or self.issuer.rstrip("/") + (
            "/openid/v1/jwks" if self.kind == "kubernetes" else "/protocol/openid-connect/certs")
        self.token_file = entry.get("token_file")
        self.context = ssl.create_default_context(cafile=entry["ca_file"]) if entry.get("ca_file") else None
        self.keys: dict = {}
        self.fetched = 0.0
        self.lock = threading.Lock()

    def key(self, kid: str | None):
        with self.lock:
            if time.time() - self.fetched > 300 or (kid and kid not in self.keys):
                if time.time() - self.fetched > 10:
                    self._fetch()
            if kid:
                return self.keys.get(kid)
            return next(iter(self.keys.values()), None) if len(self.keys) == 1 else None

    def _fetch(self) -> None:
        headers = {}
        if self.token_file:
            headers["Authorization"] = "Bearer " + Path(self.token_file).read_text().strip()
        try:
            req = urllib.request.Request(self.uri, headers=headers)
            with urllib.request.urlopen(req, timeout=10, context=self.context) as resp:
                jwks = json.load(resp)
        except (OSError, ValueError) as exc:
            log(f"JWKS {self.uri}: {exc}")
            return
        self.keys = {k.get("kid"): (int.from_bytes(b64(k["n"]), "big"), int.from_bytes(b64(k["e"]), "big"))
                     for k in jwks.get("keys", []) if k.get("kty") == "RSA"}
        self.fetched = time.time()


TRUSTED = {i.issuer: i for i in (Issuer(e) for e in ISSUERS)}


def verify(token: str) -> dict | None:
    """Claims of a valid RS-signed JWT of a trusted issuer, else None."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        head, claims, sig = json.loads(b64(head_b64)), json.loads(b64(body_b64)), b64(sig_b64)
    except (ValueError, TypeError):
        return None
    issuer = TRUSTED.get(claims.get("iss"))
    if not issuer or head.get("alg") not in DIGEST:
        return None
    key = issuer.key(head.get("kid"))
    if not key:
        return None
    n, e = key
    size = (n.bit_length() + 7) // 8
    if len(sig) != size:
        return None
    hasher, prefix = DIGEST[head["alg"]]
    digest = prefix + hasher(f"{head_b64}.{body_b64}".encode()).digest()
    expected = b"\x00\x01" + b"\xff" * (size - 3 - len(digest)) + b"\x00" + digest
    if pow(int.from_bytes(sig, "big"), e, n).to_bytes(size, "big") != expected:
        return None
    leeway = 60
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] + leeway < time.time():
        return None
    if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] - leeway > time.time():
        return None
    if issuer.audiences:
        aud = claims.get("aud")
        aud = [aud] if isinstance(aud, str) else aud or []
        if not set(aud) & set(issuer.audiences):
            return None
    claims["_kind"] = issuer.kind
    return claims


def member(value: str) -> bool:
    return value == TENANT or value.startswith(TENANT + "-")


def identity(header: str) -> str | None:
    if not header.lower().startswith("bearer "):
        return None
    claims = verify(header[7:].strip())
    if not claims:
        return None
    if claims["_kind"] == "kubernetes":
        k8s = claims.get("kubernetes.io") or {}
        ns = k8s.get("namespace") or ""
        sa = (k8s.get("serviceaccount") or {}).get("name")
        return f"system:serviceaccount:{ns}:{sa}" if member(ns) else None
    groups = claims.get("groups") or []
    groups = [groups] if isinstance(groups, str) else groups
    if any(member(str(g).lstrip("/")) for g in groups):
        return str(claims.get("email") or claims.get("preferred_username") or claims.get("sub"))
    return None


# --- ACP client of goose serve ----------------------------------------------------------

class AcpError(Exception):
    pass


class Session:
    def __init__(self, sid: str):
        self.sid = sid
        self.updates: queue.Queue = queue.Queue()
        self.lock = threading.Lock()


class Goose:
    """One ACP connection; responses and session updates arrive on SSE streams."""

    def __init__(self):
        self.lock = threading.Lock()
        self.conn: str | None = None
        self.next_id = 0
        self.waiters: dict[int, queue.Queue] = {}
        self.open: dict[str, Session] = {}

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"X-Secret-Key": SECRET}
        headers.update(extra or {})
        return headers

    def _post(self, msg: dict, session: str | None = None) -> tuple[int, dict, bytes]:
        headers = self._headers({"Content-Type": "application/json"})
        if self.conn:
            headers["Acp-Connection-Id"] = self.conn
        if session:
            headers["Acp-Session-Id"] = session
        req = urllib.request.Request(GOOSE + "/acp", json.dumps(msg).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.conn = None
            raise AcpError(f"goose answered {exc.code}: {exc.read()[:200]!r}") from exc
        except OSError as exc:
            raise AcpError(f"goose unreachable: {exc}") from exc

    def _stream(self, conn: str, session: Session | None) -> None:
        headers = self._headers({"Accept": "text/event-stream", "Acp-Connection-Id": conn})
        if session:
            headers["Acp-Session-Id"] = session.sid
        try:
            req = urllib.request.Request(GOOSE + "/acp", headers=headers)
            with urllib.request.urlopen(req, timeout=None) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:") and line[5:].strip():
                        self._dispatch(json.loads(line[5:]), session)
        except (OSError, ValueError) as exc:
            log(f"stream {'session ' + session.sid if session else 'connection'} closed: {exc}")
        with self.lock:
            if self.conn == conn:
                if session:
                    self.open.pop(session.sid, None)
                else:
                    self.conn = None
                    self.open.clear()
            for waiter, sid in list(self.waiters.values()):
                if session is None or sid == session.sid:
                    waiter.put({"error": {"message": "goose connection closed"}})
            if session:
                session.updates.put(None)

    def _dispatch(self, msg: dict, session: Session | None) -> None:
        if "method" in msg and "id" in msg:
            # a request of the agent: no file system or terminal of the client is offered,
            # and a permission question is declined (the session runs in mode auto)
            if msg["method"] == "session/request_permission":
                answer = {"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": {"outcome": "cancelled"}}}
            else:
                answer = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}}
            threading.Thread(target=self._reply, args=(answer, session), daemon=True).start()
            return
        if "method" in msg:
            if session and msg["method"] == "session/update":
                session.updates.put(msg["params"]["update"])
            return
        waiter = self.waiters.pop(msg.get("id"), None)
        if waiter:
            waiter[0].put(msg)

    def _reply(self, answer: dict, session: Session | None) -> None:
        try:
            self._post(answer, session.sid if session else None)
        except AcpError as exc:
            log(f"reply: {exc}")

    def _connect(self) -> None:
        if self.conn:
            return
        self.conn = None
        status, headers, body = self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": 1, "clientCapabilities": {},
            "clientInfo": {"name": "a2a-bridge", "version": "1"}}})
        conn = {k.lower(): v for k, v in headers.items()}.get("acp-connection-id")
        if status != 200 or not conn:
            raise AcpError(f"initialize failed: {body[:200]!r}")
        self.conn = conn
        threading.Thread(target=self._stream, args=(conn, None), daemon=True).start()
        time.sleep(0.2)

    def call(self, method: str, params: dict, session: str | None = None, timeout: float = 60) -> dict:
        with self.lock:
            self._connect()
            self.next_id += 1
            rid = self.next_id
            waiter: queue.Queue = queue.Queue()
            self.waiters[rid] = (waiter, session)
        try:
            self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, session)
            msg = waiter.get(timeout=timeout)
        except queue.Empty as exc:
            raise AcpError(f"{method}: no answer within {timeout:.0f}s") from exc
        finally:
            self.waiters.pop(rid, None)
        if "error" in msg:
            raise AcpError(msg["error"].get("message") or json.dumps(msg["error"]))
        return msg.get("result") or {}

    def notify(self, method: str, params: dict, session: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params}, session)

    def _subscribe(self, sid: str) -> Session:
        session = Session(sid)
        with self.lock:
            self.open[sid] = session
            conn = self.conn
        threading.Thread(target=self._stream, args=(conn, session), daemon=True).start()
        time.sleep(0.2)
        return session

    def session(self, sid: str | None) -> Session:
        """An open session: sid reloaded after a restart, or a new one."""
        with self.lock:
            self._connect()
            if sid and sid in self.open:
                return self.open[sid]
        params = {"cwd": CWD, "mcpServers": []}
        if sid:
            # the answer and the replayed history arrive on the session's own stream
            session = self._subscribe(sid)
            try:
                self.call("session/load", {**params, "sessionId": sid}, session=sid, timeout=120)
            except AcpError as exc:
                log(f"session {sid} not loaded, starting a new one: {exc}")
                with self.lock:
                    self.open.pop(sid, None)
                sid = None
            else:
                time.sleep(0.2)
                while not session.updates.empty():
                    session.updates.get_nowait()
                return session
        sid = self.call("session/new", params)["sessionId"]
        return self._subscribe(sid)


GOOSE_CLIENT = Goose()


# --- A2A ---------------------------------------------------------------------------------

class Contexts:
    """contextId -> goose session id, kept on the data volume."""

    def __init__(self):
        self.lock = threading.Lock()
        try:
            self.map = json.loads(STATE.read_text())
        except (OSError, ValueError):
            self.map = {}

    def get(self, ctx: str) -> str | None:
        with self.lock:
            return self.map.get(ctx)

    def set(self, ctx: str, sid: str) -> None:
        with self.lock:
            if self.map.get(ctx) == sid:
                return
            self.map[ctx] = sid
            try:
                STATE.parent.mkdir(parents=True, exist_ok=True)
                tmp = STATE.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.map))
                tmp.replace(STATE)
            except OSError as exc:
                log(f"state {STATE}: {exc}")


CONTEXTS = Contexts()
TASKS: dict[str, dict] = {}
LOCKS: dict[str, threading.Lock] = {}
LOCKS_LOCK = threading.Lock()

METHODS = {"SendMessage": "message/send", "SendStreamingMessage": "message/stream",
           "GetTask": "tasks/get", "CancelTask": "tasks/cancel"}


def text_of(message: dict) -> str:
    parts = []
    for part in message.get("parts") or []:
        if "text" in part:
            parts.append(part["text"])
        elif "data" in part:
            parts.append(json.dumps(part["data"]))
        elif isinstance(part.get("file"), dict) and part["file"].get("uri"):
            parts.append(f"[file {part['file'].get('name') or ''}: {part['file']['uri']}]")
    return "\n".join(p for p in parts if p)


def v03_message(message: dict) -> dict:
    role = {"ROLE_USER": "user", "ROLE_AGENT": "agent"}.get(message.get("role"), message.get("role") or "user")
    parts = [{"kind": "text" if "text" in p else "data" if "data" in p else "file", **p} if "kind" not in p else p
             for p in message.get("parts") or []]
    return {**message, "kind": "message", "role": role, "parts": parts}


def agent_message(ctx: str, task_id: str, text: str) -> dict:
    return {"kind": "message", "role": "agent", "messageId": str(uuid.uuid4()), "contextId": ctx,
            "taskId": task_id, "parts": [{"kind": "text", "text": text}]}


def new_task(ctx: str, message: dict) -> dict:
    task = {"kind": "task", "id": str(uuid.uuid4()), "contextId": ctx,
            "status": {"state": "submitted", "timestamp": now()},
            "history": [{**message, "contextId": ctx}], "artifacts": []}
    TASKS[task["id"]] = task
    while len(TASKS) > 500:
        TASKS.pop(next(iter(TASKS)))
    return task


def run_turn(task: dict, text: str, emit=None) -> dict:
    """Runs one goose turn for the task; emit gets every A2A stream event."""
    ctx = task["contextId"]
    with LOCKS_LOCK:
        lock = LOCKS.setdefault(ctx, threading.Lock())
    artifact_id = str(uuid.uuid4())
    chunks: list[str] = []

    def status(state: str, message: str | None = None, final: bool = False) -> None:
        task["status"] = {"state": state, "timestamp": now()}
        if message is not None:
            task["status"]["message"] = agent_message(ctx, task["id"], message)
        if emit:
            emit({"kind": "status-update", "taskId": task["id"], "contextId": ctx,
                  "status": task["status"], "final": final})

    with lock:
        try:
            session = GOOSE_CLIENT.session(CONTEXTS.get(ctx))
            CONTEXTS.set(ctx, session.sid)
            task["_session"] = session.sid
            status("working")
            result: dict = {}
            done = threading.Event()

            def prompt() -> None:
                try:
                    result.update(GOOSE_CLIENT.call("session/prompt", {
                        "sessionId": session.sid, "prompt": [{"type": "text", "text": text}]},
                        session=session.sid, timeout=TURN_TIMEOUT))
                except AcpError as exc:
                    result["_error"] = str(exc)
                done.set()
                session.updates.put(None)

            threading.Thread(target=prompt, daemon=True).start()
            while True:
                update = session.updates.get()
                if update is None:
                    if done.is_set():
                        break
                    continue
                if update.get("sessionUpdate") == "agent_message_chunk":
                    content = update.get("content") or {}
                    if content.get("type") == "text" and content.get("text"):
                        chunks.append(content["text"])
                        if emit:
                            emit({"kind": "artifact-update", "taskId": task["id"], "contextId": ctx,
                                  "append": len(chunks) > 1, "lastChunk": False,
                                  "artifact": {"artifactId": artifact_id, "name": "answer",
                                               "parts": [{"kind": "text", "text": content["text"]}]}})
            answer = "".join(chunks)
            if answer:
                task["artifacts"] = [{"artifactId": artifact_id, "name": "answer",
                                      "parts": [{"kind": "text", "text": answer}]}]
            if "_error" in result:
                status("failed", result["_error"], final=True)
            elif result.get("stopReason") == "cancelled":
                status("canceled", answer or None, final=True)
            else:
                status("completed", answer or None, final=True)
        except AcpError as exc:
            log(f"task {task['id']}: {exc}")
            status("failed", str(exc), final=True)
    return task


def public(task: dict) -> dict:
    return {k: v for k, v in task.items() if not k.startswith("_")}


def card() -> dict:
    return {
        "protocolVersion": "0.3.0", "name": NAME, "description": DESCRIPTION, "url": PUBLIC_URL,
        "preferredTransport": "JSONRPC", "version": VERSION or "1",
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
        "skills": [{"id": "goose", "name": "goose", "description": DESCRIPTION,
                    "tags": ["agent", "coding"], "examples": []}],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code: int, body, extra: dict | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._json(200, {"status": "ok"})
        if path in CARDS:
            return self._json(200, card())
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        who = identity(self.headers.get("Authorization", ""))
        if not who:
            return self._json(401, {"error": "a bearer token of the tenant is required"},
                              {"WWW-Authenticate": "Bearer"})
        try:
            req = json.loads(raw)
            rid, method, params = req.get("id"), METHODS.get(req.get("method"), req.get("method")), req.get("params") or {}
        except (ValueError, AttributeError):
            return self._json(200, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        log(f"{method} by {who}")

        def reply(result=None, error=None):
            body = {"jsonrpc": "2.0", "id": rid}
            body.update({"error": error} if error else {"result": result})
            return self._json(200, body)

        if method in ("message/send", "message/stream"):
            message = v03_message(params.get("message") or {})
            text = text_of(message)
            if not text:
                return reply(error={"code": -32602, "message": "the message has no text"})
            ctx = message.get("contextId") or str(uuid.uuid4())
            task = new_task(ctx, message)
            if method == "message/send":
                return reply(public(run_turn(task, text)))
            return self._stream(rid, task, text)
        if method == "tasks/get":
            task = TASKS.get(params.get("id") or params.get("name", "").removeprefix("tasks/"))
            return reply(public(task)) if task else reply(error={"code": -32001, "message": "task not found"})
        if method == "tasks/cancel":
            task = TASKS.get(params.get("id") or params.get("name", "").removeprefix("tasks/"))
            if not task:
                return reply(error={"code": -32001, "message": "task not found"})
            if task["status"]["state"] in ("submitted", "working") and task.get("_session"):
                try:
                    GOOSE_CLIENT.notify("session/cancel", {"sessionId": task["_session"]}, task["_session"])
                except AcpError as exc:
                    return reply(error={"code": -32603, "message": str(exc)})
            return reply(public(task))
        return reply(error={"code": -32601, "message": f"method {method} not supported"})

    def _stream(self, rid, task: dict, text: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        lock = threading.Lock()

        def emit(result: dict) -> None:
            data = b"data: " + json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode() + b"\n\n"
            with lock:
                try:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                    self.wfile.flush()
                except OSError:
                    pass

        emit(public(task))
        run_turn(task, text, emit)
        try:
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    if not SECRET:
        sys.exit("GOOSE_SECRET is required")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
