"""Unit tests for charts/opencode-agent/files/ui-proxy.py."""
import base64
import importlib.util
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location(
    "ui_proxy", os.path.join(HERE, "..", "charts", "opencode-agent", "files", "ui-proxy.py")
)
ui_proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui_proxy)


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Upstream.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                              "host": self.headers.get("Host")})
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(3):
                self.wfile.write(f"data: {i}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            self.close_connection = True
            return
        if self.headers.get("Upgrade") == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            data = self.connection.recv(64)
            self.connection.sendall(b"echo:" + data)
            self.close_connection = True
            return
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class UiProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.pwfile = tempfile.NamedTemporaryFile("w", delete=False)
        cls.pwfile.write("s3cret\n")
        cls.pwfile.close()
        cls.port = free_port()
        cls.proxy = ui_proxy.build_server(f"http://127.0.0.1:{cls.up.server_address[1]}", cls.pwfile.name, cls.port)
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.up.shutdown()
        os.unlink(cls.pwfile.name)

    def setUp(self):
        Upstream.seen.clear()

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()

    def test_replaces_authorization_with_opencode_user(self):
        status, body = self.get("/api/x", {"Authorization": "Basic " + base64.b64encode(b"sam:other").decode()})
        self.assertEqual((status, body), (200, b"hello"))
        expected = "Basic " + base64.b64encode(b"opencode:s3cret").decode()
        self.assertEqual(Upstream.seen[0]["auth"], expected)

    def test_keeps_the_host_header(self):
        self.get("/", {"Host": "agent.example.com"})
        self.assertEqual(Upstream.seen[0]["host"], "agent.example.com")

    def test_streams_sse(self):
        status, body = self.get("/stream")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"data: 0\n\ndata: 1\n\ndata: 2\n\n")

    def test_tunnels_websocket_upgrades(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(b"GET /pty HTTP/1.1\r\nHost: h\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            head = s.recv(1024)
            self.assertIn(b"101", head.split(b"\r\n")[0])
            s.sendall(b"ping")
            data = s.recv(64)
            self.assertIn(b"echo:ping", head + data)
        self.assertEqual(Upstream.seen[0]["auth"], "Basic " + base64.b64encode(b"opencode:s3cret").decode())

    def test_missing_password_file_is_a_502(self):
        server = ui_proxy.build_server("http://127.0.0.1:1", "/nonexistent", free_port())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/", timeout=5)
            self.assertEqual(ctx.exception.code, 502)
        finally:
            server.shutdown()

    def test_healthz_checks_the_password(self):
        self.assertEqual(self.get("/healthz"), (200, b"ok"))


if __name__ == "__main__":
    unittest.main()
