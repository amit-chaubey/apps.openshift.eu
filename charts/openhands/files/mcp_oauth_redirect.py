"""Send MCP OAuth redirects to the agent's public URL instead of localhost.

fastmcp registers http://localhost:<random port>/callback as the redirect URI and
listens there, which a browser of a hosted agent never reaches. With
MCP_OAUTH_REDIRECT_URL (https://<route>/mcp-oauth/callback) and
MCP_OAUTH_CALLBACK_PORT set, the agent-server registers that URL instead and its
callback server listens on that path and fixed port; the static server routes the
path to it behind the login proxy. One OAuth login runs at a time.

Google issues a refresh token only to an authorization request with
access_type=offline, and again after the first consent only with prompt=consent.
Without it the login expires after an hour, so every authorization URL on
accounts.google.com gets both. The agent-server loads this module with
--import-modules from /opt/agent-canvas/tools.
"""
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

URL = os.environ.get("MCP_OAUTH_REDIRECT_URL", "")
PORT = int(os.environ.get("MCP_OAUTH_CALLBACK_PORT") or 0)
OFFLINE_HOSTS = {"accounts.google.com"}
OFFLINE_PARAMS = (("access_type", "offline"), ("prompt", "consent"))


def offline(authorization_url: str) -> str:
    """The authorization URL, asking for a refresh token where the host needs that."""
    parts = urlsplit(authorization_url)
    if parts.hostname not in OFFLINE_HOSTS:
        return authorization_url
    names = {name for name, _ in OFFLINE_PARAMS}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in names]
    return urlunsplit(parts._replace(query=urlencode(query + list(OFFLINE_PARAMS))))


def _patch_offline() -> None:
    import fastmcp.client.auth.oauth as oauth

    bind = oauth.OAuth._bind

    def _bind(self, mcp_url: str) -> None:
        if self._bound:
            return
        bind(self, mcp_url)
        handler = self.context.redirect_handler

        async def redirect_handler(authorization_url: str) -> None:
            await handler(offline(authorization_url))

        self.context.redirect_handler = redirect_handler

    oauth.OAuth._bind = _bind


def _patch_redirect() -> None:
    from urllib.parse import urlparse

    import fastmcp.client.auth.oauth as oauth
    from pydantic import AnyHttpUrl

    path = urlparse(URL).path
    bind = oauth.OAuth._bind
    create_server = oauth.create_oauth_callback_server

    def _bind(self, mcp_url: str) -> None:
        if self._bound:
            return
        # the static server proxies the route to 127.0.0.1
        self._callback_host = "127.0.0.1"
        self._callback_port = PORT
        bind(self, mcp_url)
        self.context.client_metadata.redirect_uris = [AnyHttpUrl(URL)]
        if self._static_client_info is not None:
            self._static_client_info.redirect_uris = [AnyHttpUrl(URL)]

    def create_oauth_callback_server(*args, **kwargs):
        kwargs.setdefault("callback_path", path)
        return create_server(*args, **kwargs)

    oauth.OAuth._bind = _bind
    oauth.create_oauth_callback_server = create_oauth_callback_server


try:
    _patch_offline()
except Exception as exc:  # noqa: BLE001
    print(f"mcp_oauth_redirect: offline access not applied: {exc}", file=sys.stderr)

if URL and PORT:
    try:
        _patch_redirect()
    except Exception as exc:  # noqa: BLE001
        print(f"mcp_oauth_redirect: not applied: {exc}", file=sys.stderr)
