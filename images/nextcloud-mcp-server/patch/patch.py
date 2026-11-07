"""Bearer passthrough for nextcloud-mcp-server v0.201.2 (multi_user_basic mode)."""
import os, sys
root = sys.argv[1]

def edit(rel, old, new, count=1):
    p = os.path.join(root, rel)
    s = open(p).read()
    if s.count(old) != count:
        sys.exit(f"{rel}: expected {count} match(es), found {s.count(old)}")
    open(p, "w").write(s.replace(old, new))

edit("nextcloud_mcp_server/api/passwords.py",
"""    *,
    invalid_credential_error: str = "Invalid app password",
) -> tuple[str | None, JSONResponse | None]:""",
"""    *,
    invalid_credential_error: str = "Invalid app password",
    bearer_token: str | None = None,
) -> tuple[str | None, JSONResponse | None]:""")
edit("nextcloud_mcp_server/api/passwords.py",
"""                f"{nextcloud_host}/ocs/v2.php/cloud/user",
                auth=(login_name, password),
                params={"format": "json"},""",
"""                f"{nextcloud_host}/ocs/v2.php/cloud/user",
                # a Bearer token of the instance's OpenID Connect provider (user_oidc)
                auth=None if bearer_token else (login_name, password),
                headers={"Authorization": f"Bearer {bearer_token}"} if bearer_token else None,
                params={"format": "json"},""")

edit("nextcloud_mcp_server/app.py",
"""                # Store in request state
                scope.setdefault("state", {})
                scope["state"]["basic_auth"] = {
                    "username": username,
                    "password": password,
                }
                logger.debug("BasicAuth credentials extracted for user: %s", username)
""",
"""                # Store in request state
                scope.setdefault("state", {})
                scope["state"]["basic_auth"] = {
                    "username": username,
                    "password": password,
                }
                logger.debug("BasicAuth credentials extracted for user: %s", username)
            elif auth_header.startswith(b"Bearer ") and _BEARER_PASSTHROUGH:
                # an OpenID Connect token Nextcloud accepts itself (user_oidc check-bearer)
                token = auth_header[7:].decode("latin-1").strip()
                username, error = await self._verify_bearer(auth_header, token)
                if error is not None:
                    error.headers["WWW-Authenticate"] = 'Bearer realm="nextcloud"'
                    await error(scope, receive, send)
                    return
                scope.setdefault("state", {})
                scope["state"]["basic_auth"] = {"username": username, "bearer": token}
                logger.debug("Bearer token passed through for user: %s", username)
            elif _BEARER_PASSTHROUGH and scope.get("path", "").startswith("/mcp"):
                # fail closed: without a credential nothing on /mcp, not even the tool list
                error = JSONResponse({"success": False, "error": "Credentials required"}, status_code=401)
                error.headers["WWW-Authenticate"] = 'Bearer realm="nextcloud"'
                await error(scope, receive, send)
                return
""")
edit("nextcloud_mcp_server/app.py",
"""    async def __call__(
        self, scope: StarletteScope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] == "http":
            # Extract Authorization header""",
"""    async def _verify_bearer(
        self, auth_header: bytes, token: str
    ) -> tuple[str | None, JSONResponse | None]:
        \"\"\"Return ``(uid, None)`` if Nextcloud accepts the token, else ``(None, error)``.\"\"\"
        key = hashlib.sha256(auth_header).digest()
        now = time.monotonic()
        cached = self._bearer_uid.get(key)
        if cached and cached[1] > now:
            return cached[0], None
        uid, error = await _validate_nextcloud_credentials(
            get_settings().nextcloud_host or "",
            "",
            "",
            invalid_credential_error="Invalid token",
            bearer_token=token,
        )
        if error is not None:
            return None, error
        if len(self._bearer_uid) >= self._PRUNE_AT:
            self._bearer_uid = {k: v for k, v in self._bearer_uid.items() if v[1] > now}
        self._bearer_uid[key] = (uid, now + self._VALID_TTL)
        return uid, None

    async def __call__(
        self, scope: StarletteScope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] == "http":
            # Extract Authorization header""")
edit("nextcloud_mcp_server/app.py",
"""        self.app = app
        self._validated: dict[bytes, float] = {}
""",
"""        self.app = app
        self._validated: dict[bytes, float] = {}
        self._bearer_uid: dict[bytes, tuple[str | None, float]] = {}
""")
edit("nextcloud_mcp_server/app.py",
"""class BasicAuthMiddleware:""",
"""# multi_user_basic also passes an incoming Bearer token through to Nextcloud
_BEARER_PASSTHROUGH = os.environ.get("NEXTCLOUD_MCP_BEARER_PASSTHROUGH", "").lower() in ("1", "true", "yes")


class BasicAuthMiddleware:""")

edit("nextcloud_mcp_server/context.py",
"""    username = basic_auth.get("username")
    password = basic_auth.get("password")

    if not username or not password:""",
"""    username = basic_auth.get("username")
    password = basic_auth.get("password")

    if basic_auth.get("bearer") and username:
        return NextcloudClient.from_token(settings.nextcloud_host, basic_auth["bearer"], username)

    if not username or not password:""")
print("patched")
