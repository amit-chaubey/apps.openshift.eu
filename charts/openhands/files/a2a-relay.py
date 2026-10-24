#!/usr/bin/env python3
"""Streams every request on :8080 to UPSTREAM unchanged, responses chunk by chunk.

kagent runs a BYO agent as its own Deployment on port 8080; the OpenHands agent-server
listens on loopback only, so its A2A shim is a sidecar of the OpenHands pod and this
relay is the kagent-managed pod in front of it. Standard library only.
"""
import http.client
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = urllib.parse.urlsplit(os.environ["UPSTREAM"])
HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade", "host"}


class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _relay(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        conn = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port or 80, timeout=3700)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            self.send_error(502, f"upstream: {exc}")
            return
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in HOP and k.lower() != "content-length":
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        while True:
            chunk = resp.read1(65536)
            if not chunk:
                break
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _relay

    def log_message(self, fmt, *args):
        sys.stderr.write("a2a-relay: " + fmt % args + "\n")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Relay).serve_forever()
