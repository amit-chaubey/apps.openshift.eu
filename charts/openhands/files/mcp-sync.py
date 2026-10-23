#!/usr/bin/env python3
"""Keep the agent-server MCP servers in step with the namespace's RemoteMCPServers.

Every interval it lists the kagent RemoteMCPServers of the pod's namespace and writes
one MCP server per object: a URL on gateway.upstream goes through the token proxy, any
other URL is used as is, except when the object needs headers from a Secret. Entries
it wrote carry DESCRIPTION_PREFIX; those whose object is gone are removed, entries the
user added are never touched, and the user's enabled switch is kept. Without the kagent
CRDs it writes nothing. It also writes GIT_SECRET_NAME into the secrets store, which
turns on the UI's git provider features. Never exits on an error.
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://127.0.0.1:18000"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
KUBE = "https://{}:{}".format(os.environ.get("KUBERNETES_SERVICE_HOST", ""),
                              os.environ.get("KUBERNETES_SERVICE_PORT", "443"))
UPSTREAM = os.environ.get("UPSTREAM", "").rstrip("/")
PROXY = "http://127.0.0.1:{}".format(os.environ.get("TOKEN_PROXY_PORT", "8099"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))
SELECTOR = os.environ.get("LABEL_SELECTOR", "")
DISCOVERY = os.environ.get("DISCOVERY", "true") == "true"
DESCRIPTION_PREFIX = "RemoteMCPServer "
GIT_SECRET_NAME = os.environ.get("GIT_SECRET_NAME", "")


def log(msg: str) -> None:
    print(f"mcp-sync: {msg}", file=sys.stderr, flush=True)


def agent(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        API + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-Session-API-Key": os.environ.get("LOCAL_BACKEND_API_KEY", ""),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        return err.code, {}
    except (OSError, ValueError):
        return 0, {}


def remote_mcp_servers(namespace: str) -> list[dict] | None:
    """The namespace's RemoteMCPServers; None when they cannot be listed."""
    query = "?labelSelector=" + urllib.parse.quote(SELECTOR) if SELECTOR else ""
    url = f"{KUBE}/apis/kagent.dev/v1alpha2/namespaces/{namespace}/remotemcpservers{query}"
    with open(f"{SA}/token") as token:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token.read().strip()})
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return json.load(resp).get("items", [])
    except urllib.error.HTTPError as err:
        if err.code != 404:
            log(f"listing RemoteMCPServers: HTTP {err.code}")
        return None
    except (OSError, ValueError) as err:
        log(f"listing RemoteMCPServers: {err}")
        return None


def to_server(obj: dict) -> dict | None:
    spec = obj.get("spec", {})
    url = spec.get("url", "")
    if UPSTREAM and url.startswith(UPSTREAM + "/"):
        # the token proxy adds the pod's own ServiceAccount token
        url = PROXY + url[len(UPSTREAM):]
    elif spec.get("headersFrom"):
        return None
    if not url:
        return None
    meta = obj["metadata"]
    return {
        "url": url,
        "transport": "sse" if spec.get("protocol") == "SSE" else "streamable-http",
        "description": f"{DESCRIPTION_PREFIX}{meta['namespace']}/{meta['name']}",
        "timeout": 60,
    }


def sync(namespace: str) -> None:
    objects = remote_mcp_servers(namespace)
    if objects is None:
        return
    status, settings = agent("GET", "/api/settings")
    if status != 200:
        return
    current = settings.get("agent_settings", {}).get("mcp_config", {}) or {}
    wanted = {}
    for obj in objects:
        server = to_server(obj)
        if server is None:
            log(f"skipped {obj['metadata']['name']}: needs headers from a Secret")
        else:
            wanted[obj["metadata"]["name"]] = server
    for name, server in wanted.items():
        have = current.get(name)
        if have is None:
            if agent("POST", f"/api/settings/mcp/{name}", {**server, "enabled": True})[0] not in (200, 201):
                log(f"could not add {name}")
            else:
                log(f"added {name}")
        elif str(have.get("description", "")).startswith(DESCRIPTION_PREFIX):
            if any(have.get(k) != v for k, v in server.items()):
                if agent("PATCH", f"/api/settings/mcp/{name}", server)[0] not in (200, 201):
                    log(f"could not update {name}")
        # a same-named entry the user made stays as it is
    for name, have in current.items():
        if name not in wanted and str(have.get("description", "")).startswith(DESCRIPTION_PREFIX):
            if agent("DELETE", f"/api/settings/mcp/{name}")[0] in (200, 204):
                log(f"removed {name}")


def seed_git_secret() -> bool:
    value = os.environ.get("GIT_TOKEN", "")
    if not (GIT_SECRET_NAME and value):
        return True
    return agent("PUT", "/api/settings/secrets", {"name": GIT_SECRET_NAME, "value": value,
                                                  "description": "set by the chart"})[0] in (200, 201)


def main() -> None:
    with open(f"{SA}/namespace") as f:
        namespace = f.read().strip()
    while agent("GET", "/alive")[0] != 200:
        time.sleep(5)
    # the token comes from the pod environment, so once per start is enough
    git_seeded = False
    while True:
        try:
            git_seeded = git_seeded or seed_git_secret()
            if DISCOVERY:
                sync(namespace)
        except Exception as exc:  # noqa: BLE001
            log(str(exc))
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
