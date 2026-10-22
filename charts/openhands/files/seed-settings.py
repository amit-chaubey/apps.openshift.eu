#!/usr/bin/env python3
"""Write the chart's model and MCP defaults into the agent-server settings.

Runs as the postStart hook once per change of those values (SEED_HASH); a later
edit in the UI is kept until the values change again. SEED_LLM is a JSON llm
diff, SEED_MCP a JSON map of server name to MCP server settings, where passEnv
names pod environment variables to hand to a stdio server. Always exits 0
so a failed seed never kills the container.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:18000"
MARKER = os.path.join(os.environ.get("HOME", "/tmp"), ".openhands", "agent-canvas",
                      ".seeded-" + os.environ.get("SEED_HASH", "none"))


def call(method: str, path: str, body: dict | None = None) -> int:
    req = urllib.request.Request(
        API + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-Session-API-Key": os.environ.get("LOCAL_BACKEND_API_KEY", ""),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code
    except OSError:
        return 0


def main() -> None:
    if os.path.exists(MARKER):
        return
    for _ in range(120):
        if call("GET", "/alive") == 200:
            break
        time.sleep(2)
    else:
        return
    ok = True
    llm = json.loads(os.environ.get("SEED_LLM") or "{}")
    if llm and call("PATCH", "/api/settings", {"agent_settings_diff": {"llm": llm}}) != 200:
        ok = False
    for name, server in json.loads(os.environ.get("SEED_MCP") or "{}").items():
        # the agent-server starts stdio servers with a reduced environment
        pass_env = server.pop("passEnv", [])
        if pass_env:
            server["env"] = {k: os.environ[k] for k in pass_env if k in os.environ}
        status = call("POST", f"/api/settings/mcp/{name}", server)
        if status not in (200, 201):
            status = call("PATCH", f"/api/settings/mcp/{name}", server)
        if status not in (200, 201):
            print(f"seed: MCP server {name} not written ({status})", file=sys.stderr)
            ok = False
    # environment of the ACP agents (Claude Code, Codex, Gemini CLI): the UI asks for
    # these names until the secrets store holds them
    for name, value in json.loads(os.environ.get("SEED_SECRETS") or "{}").items():
        if call("PUT", "/api/settings/secrets", {"name": name, "value": value,
                                                 "description": "set by the chart"}) not in (200, 201):
            print(f"seed: secret {name} not written", file=sys.stderr)
            ok = False
    # selectable ACP agent profiles; the agent-server starts each CLI with its default command
    for name, prof in json.loads(os.environ.get("SEED_AGENT_PROFILES") or "{}").items():
        body = {"name": name, "agent_kind": "acp", "acp_command": None, "acp_args": None, **prof}
        if call("POST", f"/api/agent-profiles/{name}", body) not in (200, 201):
            print(f"seed: agent profile {name} not written", file=sys.stderr)
            ok = False
    if ok:
        os.makedirs(os.path.dirname(MARKER), exist_ok=True)
        open(MARKER, "w").close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"seed: {exc}", file=sys.stderr)
