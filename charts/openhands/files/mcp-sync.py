#!/usr/bin/env python3
"""Keep the agent-server MCP servers in step with the namespace's RemoteMCPServers.

Every interval it lists the kagent RemoteMCPServers of the pod's namespace and writes
one MCP server per object: a URL on gateway.upstream goes through the token proxy, any
other URL is used as is, except when the object needs headers from a Secret. Entries
it wrote carry DESCRIPTION_PREFIX; those whose object is gone are removed, a same-named
entry the user added wins, and the user's enabled switch is kept. Without the kagent
CRDs it writes nothing. It also writes GIT_SECRET_NAME into the secrets store, which
turns on the UI's git provider features. Never exits on an error.

With PUBLISH it also works the other way: every remote MCP server the user added in the
UI becomes an AgentgatewayBackend of the namespace, with the headers OpenHands would send
in a Secret it owns, so the platform publishes it to the team as a RemoteMCPServer.
Servers that log in with OAuth or run in the pod stay personal. A backend it published
is labelled with PUBLISHED_LABEL=AGENT and removed when the user removes the server.
The entry's description tells the user the outcome.
"""
import base64
import hashlib
import json
import re
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
# icon URL of a server: this annotation on the RemoteMCPServer, else the logo of a known name
ICON_ANNOTATION = "openhands.dev/icon"
SIMPLE_ICONS = "https://cdn.simpleicons.org/"
KNOWN_ICONS = {
    **{n: SIMPLE_ICONS + n for n in (
        "atlassian", "bitwarden", "confluence", "github", "gitlab", "grafana", "helm", "jira",
        "kubernetes", "n8n", "nextcloud", "notion", "odoo", "opnsense", "prometheus", "qdrant",
        "sentry", "vaultwarden", "wordpress")},
    "kagent-tools": "https://raw.githubusercontent.com/kagent-dev/kagent/main/img/icon-light.svg",
    "librenms": "https://raw.githubusercontent.com/librenms/librenms/master/html/images/librenms_logo_light.svg",
    "memory": SIMPLE_ICONS + "qdrant",
    "netbox": "https://raw.githubusercontent.com/netbox-community/netbox/main/netbox/project-static/img/netbox_icon.svg",
    "warden": SIMPLE_ICONS + "vaultwarden",
    "wispr-flow": "https://cdn.prod.website-files.com/682f84b3838c89f8ff7667db/68d27d1a8a10f417b5644527_flow-wc-v2.png",
}
GIT_SECRET_NAME = os.environ.get("GIT_SECRET_NAME", "")
PUBLISH = os.environ.get("PUBLISH", "false") == "true"
AGENT = os.environ.get("AGENT", "")
# entries the chart writes itself are never published
MANAGED = set(filter(None, os.environ.get("MANAGED_SERVERS", "").split(",")))
# credentials are read in plaintext only on a change or every RESYNC seconds
RESYNC = int(os.environ.get("RESYNC", "600"))
PUBLISHED_LABEL = "openhands.dev/published-by"
NAME_ANNOTATION = "openhands.dev/mcp-server"
HASH_ANNOTATION = "openhands.dev/config-hash"
NOTE_PUBLISHED = "Published to the team as AgentgatewayBackend "
NOTE_SKIPPED = "Not published: "
BACKENDS = "/apis/agentgateway.dev/v1alpha1/namespaces/{}/agentgatewaybackends"
SECRETS = "/api/v1/namespaces/{}/secrets"
FIELD_MANAGER = "openhands-mcp-publish"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
# backend names this agent published; discovery leaves their RemoteMCPServers out
OWN: set[str] = set()


def log(msg: str) -> None:
    print(f"mcp-sync: {msg}", file=sys.stderr, flush=True)


def agent(method: str, path: str, body: dict | None = None,
          plaintext: bool = False) -> tuple[int, dict]:
    headers = {"X-Session-API-Key": os.environ.get("LOCAL_BACKEND_API_KEY", ""),
               "Content-Type": "application/json"}
    if plaintext:
        headers["X-Expose-Secrets"] = "plaintext"
    req = urllib.request.Request(
        API + path, method=method,
        data=None if body is None else json.dumps(body).encode(), headers=headers)
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


def kube(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """A Kubernetes API call; a body is sent as a server-side apply."""
    with open(f"{SA}/token") as token:
        headers = {"Authorization": "Bearer " + token.read().strip()}
    url = KUBE + path
    if body is not None:
        headers["Content-Type"] = "application/apply-patch+yaml"
        url += f"?fieldManager={FIELD_MANAGER}&force=true"
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.load(err)
        except ValueError:
            return err.code, {}
    except (OSError, ValueError) as err:
        log(f"{method} {path}: {err}")
        return 0, {}


def send_headers(server: dict) -> dict[str, str] | None:
    """The headers OpenHands sends to this server; None for an OAuth login."""
    headers = dict(server.get("headers") or {})
    auth = server.get("auth") or {}
    strategy = auth.get("strategy", "none") if isinstance(auth, dict) else "token"
    if strategy in ("api_key", "bearer") and auth.get("value"):
        name = auth.get("header_name") if strategy == "api_key" else None
        headers[name or "Authorization"] = auth["value"] if name else "Bearer " + auth["value"]
    elif strategy == "basic" and auth.get("password") is not None:
        pair = f"{auth.get('username', '')}:{auth['password']}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
    elif strategy == "header":
        headers.update(auth.get("headers") or {})
    elif strategy not in ("none", "api_key", "bearer", "basic"):
        return None
    return {k: str(v) for k, v in headers.items()}


def backend_name(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")[:63].strip("-")


def publishable(name: str, server: dict) -> str:
    """Why a UI entry stays personal; empty when it can be published."""
    url = urllib.parse.urlparse(server.get("url") or "")
    if not url.hostname:
        return "it runs inside this agent"
    if url.hostname in LOOPBACK:
        return "its URL is local to this agent"
    if url.scheme not in ("http", "https"):
        return f"scheme {url.scheme} is not supported"
    if send_headers(server) is None:
        return "it logs in with OAuth, which stays personal"
    if not backend_name(name):
        return "its name has no letters or digits"
    if len(send_headers(server)) > 8:
        return "it sends more than 8 credential headers"
    return ""


def to_backend(namespace: str, name: str, server: dict, headers: dict[str, str]) -> dict:
    url = urllib.parse.urlparse(server["url"])
    tls = url.scheme == "https"
    target = {
        "host": url.hostname,
        "port": url.port or (443 if tls else 80),
        "path": (url.path or "/") + (f"?{url.query}" if url.query else ""),
        "protocol": "SSE" if server.get("transport") == "sse" else "StreamableHTTP",
    }
    policies = {}
    if tls:
        policies["tls"] = {"sni": url.hostname}
    secret = f"mcp-ui-{backend_name(name)}"[:63].rstrip("-")
    if headers:
        policies["auth"] = {"credentials": [
            {"location": {"header": {"name": h}}, "secretRef": {"name": secret, "key": f"h{i}"}}
            for i, h in enumerate(sorted(headers))]}
    if policies:
        target["policies"] = policies
    return {
        "apiVersion": "agentgateway.dev/v1alpha1",
        "kind": "AgentgatewayBackend",
        "metadata": {
            "name": backend_name(name),
            "namespace": namespace,
            "labels": {PUBLISHED_LABEL: AGENT},
            "annotations": {NAME_ANNOTATION: name},
        },
        "spec": {"mcp": {"targets": [{"name": backend_name(name), "static": target}]}},
    }


def note(name: str, server: dict, text: str) -> None:
    if server.get("description") != text:
        agent("PATCH", f"/api/settings/mcp/{name}", {"description": text})


def publish_one(namespace: str, name: str, server: dict, existing: dict | None) -> str:
    """Applies the backend and its Secret; returns the note for the entry."""
    headers = send_headers(server) or {}
    backend = to_backend(namespace, name, server, headers)
    bname = backend["metadata"]["name"]
    if existing is not None and existing["metadata"].get("labels", {}).get(PUBLISHED_LABEL) != AGENT:
        return NOTE_SKIPPED + f"the namespace already has an AgentgatewayBackend {bname}"
    digest = hashlib.sha256(json.dumps([backend["spec"], headers], sort_keys=True).encode()).hexdigest()[:16]
    backend["metadata"]["annotations"][HASH_ANNOTATION] = digest
    if existing is not None and existing["metadata"].get("annotations", {}).get(HASH_ANNOTATION) == digest:
        return NOTE_PUBLISHED + bname
    status, applied = kube("PATCH", BACKENDS.format(namespace) + "/" + bname, backend)
    if status not in (200, 201):
        reason = applied.get("message") or f"HTTP {status}"
        return NOTE_SKIPPED + reason.split(": ", 1)[-1][:300]
    if headers:
        secret = {
            "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {
                "name": backend["spec"]["mcp"]["targets"][0]["static"]["policies"]["auth"]["credentials"][0]["secretRef"]["name"],
                "namespace": namespace,
                "labels": {PUBLISHED_LABEL: AGENT},
                # deleted with its backend
                "ownerReferences": [{"apiVersion": backend["apiVersion"], "kind": backend["kind"],
                                     "name": bname, "uid": applied["metadata"]["uid"]}],
            },
            "stringData": {f"h{i}": headers[h] for i, h in enumerate(sorted(headers))},
        }
        status, _ = kube("PATCH", SECRETS.format(namespace) + "/" + secret["metadata"]["name"], secret)
        if status not in (200, 201):
            return NOTE_SKIPPED + f"its credential Secret could not be written (HTTP {status})"
    log(f"published {name} as {bname}")
    return NOTE_PUBLISHED + bname


def publish(namespace: str, current: dict, full: bool) -> None:
    status, listed = kube("GET", BACKENDS.format(namespace))
    if status != 200:
        if status != 404:
            log(f"listing AgentgatewayBackends: HTTP {status}")
        return
    backends = {b["metadata"]["name"]: b for b in listed.get("items", [])}
    ours = {b["metadata"].get("annotations", {}).get(NAME_ANNOTATION): b for b in backends.values()
            if b["metadata"].get("labels", {}).get(PUBLISHED_LABEL) == AGENT}
    user = {n: s for n, s in current.items()
            if n not in MANAGED and not str(s.get("description", "")).startswith(DESCRIPTION_PREFIX)}
    OWN.clear()
    OWN.update(b["metadata"]["name"] for b in ours.values())
    secrets = None
    for name, server in user.items():
        reason = publishable(name, server)
        if reason:
            note(name, server, NOTE_SKIPPED + reason)
            continue
        existing = backends.get(backend_name(name))
        description = str(server.get("description", ""))
        # a new entry now; edits and refused entries on the full pass
        new = existing is None and not description.startswith(NOTE_SKIPPED)
        lost = existing is not None and description.startswith(NOTE_PUBLISHED) and backend_name(name) not in OWN
        if not (full or new or lost):
            continue
        if secrets is None:
            # the stored credentials, read only when something may have changed
            status, settings = agent("GET", "/api/settings", plaintext=True)
            if status != 200:
                return
            secrets = settings.get("agent_settings", {}).get("mcp_config", {}) or {}
        result = publish_one(namespace, name, secrets.get(name, server), existing)
        if result.startswith(NOTE_PUBLISHED):
            OWN.add(backend_name(name))
        note(name, server, result)
    for name, backend in ours.items():
        if name not in user:
            if kube("DELETE", BACKENDS.format(namespace) + "/" + backend["metadata"]["name"])[0] in (200, 202):
                log(f"unpublished {name}")


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
    server = {
        "url": url,
        "transport": "sse" if spec.get("protocol") == "SSE" else "streamable-http",
        "description": f"{DESCRIPTION_PREFIX}{meta['namespace']}/{meta['name']}",
        "timeout": 60,
    }
    icon = (meta.get("annotations") or {}).get(ICON_ANNOTATION)
    if not icon:
        icon = KNOWN_ICONS.get(meta["name"])
    if icon:
        server["icon"] = icon
    return server


def sync(namespace: str, current: dict) -> None:
    objects = remote_mcp_servers(namespace)
    if objects is None:
        return
    wanted = {}
    for obj in objects:
        if obj["metadata"]["name"] in OWN:
            # this agent's own UI entry already reaches it directly
            continue
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
    last_full = 0.0
    while True:
        try:
            git_seeded = git_seeded or seed_git_secret()
            status, settings = agent("GET", "/api/settings")
            if status == 200:
                current = settings.get("agent_settings", {}).get("mcp_config", {}) or {}
                if PUBLISH:
                    full = time.monotonic() - last_full >= RESYNC
                    publish(namespace, current, full)
                    if full:
                        last_full = time.monotonic()
                if DISCOVERY:
                    sync(namespace, current)
        except Exception as exc:  # noqa: BLE001
            log(str(exc))
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
