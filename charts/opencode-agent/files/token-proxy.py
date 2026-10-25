#!/usr/bin/env python3
"""Forward every request to the gateway with this pod's ServiceAccount token.

OpenCode talks to 127.0.0.1:PORT for models (/v1) and A2A calls (/a2a/...).
The proxy reads the projected ServiceAccount token on every request, so the
kubelet's renewal is picked up without a restart, and replaces any incoming
Authorization header with it. Response bodies are relayed as they arrive so
model streams (SSE) stay live.

With AGENT_TOKEN_URL set, requests under AGENT_TOKEN_PATHS (default /mcp)
carry the agent's own agent token instead: a client_credentials grant
authenticated with a second projected ServiceAccount token whose audience
is the realm (federated client authentication), kept until shortly before
it expires.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_TOKEN_FILE = "/var/run/secrets/api-token/token"
DEFAULT_ASSERTION_FILE = "/var/run/secrets/agent-token/token"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
# renew this many seconds before the agent token expires
RENEW_MARGIN = 15
UPSTREAM_TIMEOUT = 600
CHUNK_SIZE = 8192
HOP_BY_HOP = frozenset({
    "connection", "transfer-encoding", "keep-alive", "te", "trailer", "upgrade",
    "proxy-authenticate", "proxy-authorization", "proxy-connection",
})


class ConfigError(Exception):
    """Startup configuration is unusable."""


class TokenError(Exception):
    """The projected token file is missing, unreadable or empty."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logging.warning("ignoring unparsable %s=%r, using %d", name, raw, default)
        return default


@dataclass(frozen=True)
class Settings:
    token_file: str
    upstream: str
    port: int
    agent_token_url: str = ""
    agent_token_assertion_file: str = DEFAULT_ASSERTION_FILE
    agent_token_paths: tuple[str, ...] = ("/mcp",)

    def validate(self) -> None:
        missing = [
            name
            for name, value in (("TOKEN_FILE", self.token_file), ("UPSTREAM", self.upstream))
            if not value
        ]
        if missing:
            raise ConfigError(f"{', '.join(missing)} unset")

    @staticmethod
    def from_env() -> Settings:
        paths = tuple(p.strip() for p in os.environ.get("AGENT_TOKEN_PATHS", "/mcp").split(",") if p.strip())
        return Settings(
            token_file=os.environ.get("TOKEN_FILE", DEFAULT_TOKEN_FILE).strip(),
            upstream=os.environ.get("UPSTREAM", "").strip().rstrip("/"),
            port=_env_int("PORT", 8099),
            agent_token_url=os.environ.get("AGENT_TOKEN_URL", "").strip(),
            agent_token_assertion_file=os.environ.get("AGENT_TOKEN_ASSERTION_FILE", DEFAULT_ASSERTION_FILE).strip(),
            agent_token_paths=paths,
        )


def _connect(url: urllib.parse.SplitResult, timeout: float) -> http.client.HTTPConnection:
    port = url.port
    if url.scheme == "https":
        return http.client.HTTPSConnection(url.hostname, port, timeout=timeout)
    return http.client.HTTPConnection(url.hostname, port, timeout=timeout)


class TokenSource:
    """The projected ServiceAccount token, read from disk at each call."""

    def __init__(self, settings: Settings) -> None:
        self._path = settings.token_file

    def token(self) -> str:
        try:
            with open(self._path, encoding="utf-8") as handle:
                token = handle.read().strip()
        except (OSError, ValueError) as err:
            raise TokenError(f"token file unreadable: {type(err).__name__}") from err
        if not token:
            raise TokenError("token file is empty")
        return token


class AgentTokenSource:
    """The agent's agent token, from a federated client_credentials grant, cached."""

    def __init__(self, settings: Settings) -> None:
        self._url = settings.agent_token_url
        self._assertion = TokenSource(Settings(settings.agent_token_assertion_file, "", 0))
        self._lock = threading.Lock()
        self._token = ""
        self._expires = 0.0

    def token(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            form = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_assertion_type": ASSERTION_TYPE,
                "client_assertion": self._assertion.token(),
            }).encode()
            request = urllib.request.Request(self._url, data=form, method="POST",
                                             headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(request, timeout=30) as resp:
                    answer = json.loads(resp.read())
            except (OSError, ValueError) as err:
                raise TokenError(f"agent token request failed: {type(err).__name__}") from err
            token = answer.get("access_token") if isinstance(answer, dict) else None
            if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
                raise TokenError("token endpoint answered without a usable access token")
            lifetime = answer.get("expires_in")
            lifetime = lifetime if isinstance(lifetime, (int, float)) else 60
            self._token = token
            self._expires = time.monotonic() + max(lifetime - RENEW_MARGIN, 0)
            return token


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "token-proxy"
    sys_version = ""
    settings: Settings
    tokens: TokenSource
    agent_token: AgentTokenSource | None

    def _source(self) -> TokenSource | AgentTokenSource:
        path = self.path.split("?", 1)[0]
        if self.agent_token and any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.settings.agent_token_paths):
            return self.agent_token
        return self.tokens

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("%s", fmt % args)

    def _send(self, status: int, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, message: str) -> None:
        self._send(status, json.dumps({"error": message}).encode())

    def _read_body(self) -> bytes:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            body = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline().strip():
                        pass
                    return body
                body += self.rfile.read(size)
                self.rfile.readline()
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _forward(self) -> None:
        if self.path == "/healthz":
            try:
                self.tokens.token()
            except TokenError as err:
                self._error(503, str(err))
                return
            self._send(200, b"ok", "text/plain")
            return
        body = self._read_body()
        try:
            token = self._source().token()
        except TokenError as err:
            logging.error("%s", err)
            self._error(502, str(err))
            return
        upstream = urllib.parse.urlsplit(self.settings.upstream)
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP | {"host", "authorization", "content-length"}
        }
        headers["Host"] = upstream.netloc
        headers["Authorization"] = f"Bearer {token}"
        if body or self.command in ("POST", "PUT", "PATCH"):
            headers["Content-Length"] = str(len(body))
        conn = _connect(upstream, UPSTREAM_TIMEOUT)
        try:
            conn.request(self.command, (upstream.path.rstrip("/") + self.path), body=body or None, headers=headers)
            resp = conn.getresponse()
            self._relay(resp)
        except (OSError, ValueError, http.client.HTTPException) as err:
            logging.error("upstream request failed: %s", type(err).__name__)
            if not self.wfile.closed and not getattr(self, "_started", False):
                self._error(502, "upstream unreachable")
            self.close_connection = True
        finally:
            conn.close()

    def _relay(self, resp: http.client.HTTPResponse) -> None:
        self._started = True
        self.send_response(resp.status)
        has_length = False
        declared = 0
        for name, value in resp.getheaders():
            lowered = name.lower()
            if lowered in HOP_BY_HOP:
                continue
            if lowered == "content-length":
                has_length = True
                declared = int(value) if value.isdigit() else 0
            self.send_header(name, value)
        # 1xx, 204, 304 and HEAD responses have no body: no chunked framing (RFC 9112 6.3)
        bodyless = self.command == "HEAD" or resp.status < 200 or resp.status in (204, 304)
        chunked = not has_length and not bodyless and self.request_version == "HTTP/1.1"
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
        elif not has_length and not bodyless:
            self.close_connection = True
        self.end_headers()
        if bodyless:
            return
        relayed = 0
        while True:
            data = resp.read1(CHUNK_SIZE)
            if not data:
                break
            relayed += len(data)
            if chunked:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            else:
                self.wfile.write(data)
            self.wfile.flush()
        # read1 returns b"" on a truncated Content-Length body without raising.
        if has_length and self.command != "HEAD" and relayed < declared:
            self.close_connection = True
        if chunked:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def handle_one_request(self) -> None:
        self._started = False
        super().handle_one_request()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _forward


def build_server(settings: Settings, port: int | None = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {
        "settings": settings,
        "tokens": TokenSource(settings),
        "agent_token": AgentTokenSource(settings) if settings.agent_token_url else None,
    })
    server = ThreadingHTTPServer(("127.0.0.1", settings.port if port is None else port), handler)
    server.daemon_threads = True
    return server


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    settings = Settings.from_env()
    try:
        settings.validate()
    except ConfigError as err:
        logging.error("%s", err)
        return 2
    server = build_server(settings)
    logging.info("token-proxy listening on 127.0.0.1:%d, upstream %s", settings.port, settings.upstream)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
