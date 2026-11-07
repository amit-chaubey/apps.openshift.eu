#!/usr/bin/env python3
"""git credential helper that answers from the agent-server secrets store.

git runs it as `git-credential.py <user name> <name>... <operation>` for github.com, the
hosts of git.providers and git.host. A name `env:<VAR>` is read from the environment, any
other name from the secrets store. On "get" it prints the user name and the first value
found as password; when none is set it prints nothing, so git asks the next helper. The
token never appears in a command line or a log.
"""
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "http://127.0.0.1:18000"


def secret(name: str) -> str:
    req = urllib.request.Request(
        f"{API}/api/settings/secrets/{urllib.parse.quote(name, safe='')}",
        headers={"X-Session-API-Key": os.environ.get("LOCAL_BACKEND_API_KEY", "")})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode().strip()
    except (OSError, urllib.error.URLError, UnicodeDecodeError):
        return ""


def main() -> None:
    if len(sys.argv) < 4 or sys.argv[-1] != "get":
        return
    user, names = sys.argv[1], sys.argv[2:-1]
    sys.stdin.read()
    for name in names:
        token = os.environ.get(name[4:], "").strip() if name.startswith("env:") else secret(name)
        if token:
            sys.stdout.write(f"username={user}\npassword={token}\n")
            return


if __name__ == "__main__":
    main()
