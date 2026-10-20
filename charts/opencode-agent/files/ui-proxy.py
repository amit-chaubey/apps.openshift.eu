#!/usr/bin/env python3
"""Forward the UI from oauth-proxy to OpenCode with OpenCode's own credential.

OpenCode 2.x accepts HTTP Basic only for the user name "opencode", while
oauth-proxy sends the signed-in person as the Basic user. This proxy listens on
127.0.0.1:PORT, replaces any incoming Authorization header with
"opencode:<server password>", keeps the Host header (OpenCode compares it with
Origin for terminals), streams responses as they arrive (SSE) and tunnels
WebSocket upgrades. The password is read from PASSWORD_FILE on every request.
"""
from __future__ import annotations

import base64
import http.client
import logging
import os
import select
import socket
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

UPSTREAM_TIMEOUT = 3600
CHUNK_SIZE = 8192
HOP_BY_HOP = frozenset({
    "connection", "transfer-encoding", "keep-alive", "te", "trailer", "upgrade",
    "proxy-authenticate", "proxy-authorization", "proxy-connection",
})


class PasswordError(Exception):
    """The password file is missing, unreadable or empty."""


def read_password(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
    except (OSError, ValueError) as err:
        raise PasswordError(f"password file unreadable: {type(err).__name__}") from err
    if not value:
        raise PasswordError("password file is empty")
    return value


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ui-proxy"
    sys_version = ""
    upstream: urllib.parse.SplitResult
    password_file: str
    user: str

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.debug("%s", fmt % args)

    def _error(self, status: int, message: str) -> None:
        payload = message.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _auth(self) -> str | None:
        try:
            return basic(self.user, read_password(self.password_file))
        except PasswordError as err:
            logging.error("%s", err)
            self._error(502, str(err))
            return None

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

    def _is_upgrade(self) -> bool:
        return "upgrade" in self.headers.get("Connection", "").lower() and bool(self.headers.get("Upgrade"))

    def _forward(self) -> None:
        if self.path == "/healthz":
            if self._auth():
                self._error(200, "ok")
            return
        auth = self._auth()
        if auth is None:
            return
        if self._is_upgrade():
            self._tunnel(auth)
            return
        body = self._read_body()
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP | {"authorization", "content-length"}
        }
        headers["Authorization"] = auth
        if body or self.command in ("POST", "PUT", "PATCH"):
            headers["Content-Length"] = str(len(body))
        conn = http.client.HTTPConnection(self.upstream.hostname, self.upstream.port, timeout=UPSTREAM_TIMEOUT)
        try:
            conn.request(self.command, self.path, body=body or None, headers=headers)
            self._relay(conn.getresponse())
        except (OSError, ValueError, http.client.HTTPException) as err:
            logging.error("upstream request failed: %s", type(err).__name__)
            if not getattr(self, "_started", False):
                self._error(502, "upstream unreachable")
            self.close_connection = True
        finally:
            conn.close()

    def _relay(self, resp: http.client.HTTPResponse) -> None:
        self._started = True
        self.send_response(resp.status)
        has_length = False
        for name, value in resp.getheaders():
            lowered = name.lower()
            if lowered in HOP_BY_HOP:
                continue
            has_length = has_length or lowered == "content-length"
            self.send_header(name, value)
        chunked = not has_length and self.request_version == "HTTP/1.1" and self.command != "HEAD"
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
        elif not has_length:
            self.close_connection = True
        self.end_headers()
        if self.command == "HEAD":
            return
        while True:
            data = resp.read1(CHUNK_SIZE)
            if not data:
                break
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data) if chunked else data)
            self.wfile.flush()
        if chunked:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def _tunnel(self, auth: str) -> None:
        """Replays the upgrade request upstream, then copies bytes both ways."""
        try:
            upstream = socket.create_connection((self.upstream.hostname, self.upstream.port), timeout=30)
        except OSError as err:
            logging.error("upstream connect failed: %s", type(err).__name__)
            self._error(502, "upstream unreachable")
            return
        lines = [f"{self.command} {self.path} HTTP/1.1"]
        lines += [f"{n}: {v}" for n, v in self.headers.items() if n.lower() != "authorization"]
        lines.append(f"Authorization: {auth}")
        upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        upstream.settimeout(None)
        client = self.connection
        self.close_connection = True
        try:
            sockets = [client, upstream]
            while True:
                readable, _, broken = select.select(sockets, [], sockets, UPSTREAM_TIMEOUT)
                if broken or not readable:
                    return
                for source in readable:
                    data = source.recv(CHUNK_SIZE)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)
        except OSError:
            return
        finally:
            upstream.close()

    def handle_one_request(self) -> None:
        self._started = False
        super().handle_one_request()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _forward


def build_server(upstream: str, password_file: str, port: int, user: str = "opencode") -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {
        "upstream": urllib.parse.urlsplit(upstream),
        "password_file": password_file,
        "user": user,
    })
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(levelname)s %(message)s", stream=sys.stderr)
    upstream = os.environ.get("UPSTREAM", "http://127.0.0.1:4096").strip().rstrip("/")
    password_file = os.environ.get("PASSWORD_FILE", "").strip()
    if not password_file:
        logging.error("PASSWORD_FILE unset")
        return 2
    port = int(os.environ.get("PORT", "4097"))
    server = build_server(upstream, password_file, port, os.environ.get("OPENCODE_USER", "opencode"))
    logging.info("ui-proxy listening on 127.0.0.1:%d, upstream %s", port, upstream)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
