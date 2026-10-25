"""Send MCP OAuth redirects to the agent's public URL instead of localhost.

fastmcp registers http://localhost:<random port>/callback as the redirect URI and
listens there, which a browser of a hosted agent never reaches. With
MCP_OAUTH_REDIRECT_URL (https://<route>/mcp-oauth/callback) and
MCP_OAUTH_CALLBACK_PORT set, the agent-server registers that URL instead and its
callback server listens on that path and fixed port; the static server routes the
path to it behind the login proxy. One OAuth login runs at a time. The agent-server
loads it with --import-modules from /opt/agent-canvas/tools.
"""
import os
import sys

URL = os.environ.get("MCP_OAUTH_REDIRECT_URL", "")
PORT = int(os.environ.get("MCP_OAUTH_CALLBACK_PORT") or 0)


def _patch() -> None:
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


if URL and PORT:
    try:
        _patch()
    except Exception as exc:  # noqa: BLE001
        print(f"mcp_oauth_redirect: not applied: {exc}", file=sys.stderr)
