"""Behaviour suite for token-proxy.py, against in-process fake servers."""
import http.client, importlib.util, json, os, socket, sys, tempfile, threading, time, unittest, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("TOKEN_PROXY_SRC") or os.path.join(HERE, "token-proxy.py")

spec = importlib.util.spec_from_file_location("token_proxy", SRC)
tp = importlib.util.module_from_spec(spec)
sys.modules["token_proxy"] = tp
spec.loader.exec_module(tp)


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeUpstream:
    """Records every request; replies with a body, or streams SSE on /stream."""

    def __init__(self):
        self.requests = []
        self.first_chunk_read = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _record(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append((self.command, self.path, dict(self.headers), body))

            def do_GET(self):
                self._record()
                if self.path == "/stream":
                    self._stream()
                    return
                if self.path == "/short":
                    self.send_response(200)
                    self.send_header("Content-Length", "100")
                    self.end_headers()
                    self.wfile.write(b"x" * 10)
                    self.wfile.flush()
                    self.close_connection = True
                    return
                payload = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_POST = do_GET

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for index in (1, 2, 3):
                    data = f"data: {index}\n\n".encode()
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                    self.wfile.flush()
                    if index == 1:
                        # chunk 2 is only sent once the client has read chunk 1
                        outer.first_chunk_read.wait(timeout=10)
                self.wfile.write(b"0\r\n\r\n")

        self.server = serve(Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"


class ProxyCase(unittest.TestCase):
    def setUp(self):
        self.upstream = FakeUpstream()
        self.token_file = os.path.join(tempfile.mkdtemp(prefix="token-proxy-tests-"), "token")
        self.write_token("projected-1\n")
        self.start()

    def write_token(self, value):
        with open(self.token_file, "w") as handle:
            handle.write(value)

    def start(self, token_file=None):
        self.settings = tp.Settings(
            token_file=token_file or self.token_file,
            upstream=self.upstream.url,
            port=0,
        )
        self.proxy = tp.build_server(self.settings, port=0)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.addCleanup(self.proxy.shutdown)
        self.addCleanup(self.proxy.server_close)
        self.port = self.proxy.server_port

    def get(self, path="/v1/models", headers=None, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        self.addCleanup(conn.close)
        conn.request(method, path, body=body, headers=headers or {})
        return conn, conn.getresponse()

    def missing(self):
        return os.path.join(os.path.dirname(self.token_file), "absent")


class TokenFileTest(ProxyCase):
    def test_reads_token_file_per_request(self):
        _, resp = self.get()
        resp.read()
        self.write_token("projected-2")
        _, resp = self.get()
        resp.read()
        self.assertEqual(self.upstream.requests[0][2]["Authorization"], "Bearer projected-1")
        self.assertEqual(self.upstream.requests[1][2]["Authorization"], "Bearer projected-2")

    def test_missing_token_file_returns_502_and_never_forwards(self):
        self.start(token_file=self.missing())
        _, resp = self.get()
        resp.read()
        self.assertEqual(resp.status, 502)
        self.assertEqual(self.upstream.requests, [])

    def test_token_with_newline_returns_502_without_leaking_it(self):
        self.write_token("bad\nX-Injected: 1")
        with self.assertLogs(level="ERROR") as logs:
            _, resp = self.get()
            body = resp.read().decode()
        self.assertEqual(resp.status, 502)
        self.assertEqual(self.upstream.requests, [])
        self.assertNotIn("bad", body + "".join(logs.output))

    def test_empty_token_file_returns_502(self):
        self.write_token("\n")
        _, resp = self.get()
        resp.read()
        self.assertEqual(resp.status, 502)
        self.assertEqual(self.upstream.requests, [])


class ForwardTest(ProxyCase):
    def test_replaces_incoming_authorization(self):
        _, resp = self.get(headers={"Authorization": "Bearer client-supplied"})
        self.assertEqual(resp.read(), b'{"ok":true}')
        sent = self.upstream.requests[0][2]
        self.assertEqual(sent["Authorization"], "Bearer projected-1")
        self.assertNotIn("client-supplied", json.dumps(sent))

    def test_forwards_method_path_body_and_sets_host(self):
        _, resp = self.get("/a2a/x?y=1", method="POST", body=b'{"a":1}',
                           headers={"Content-Type": "application/json", "Keep-Alive": "5"})
        resp.read()
        method, path, headers, body = self.upstream.requests[0]
        self.assertEqual((method, path, body), ("POST", "/a2a/x?y=1", b'{"a":1}'))
        self.assertEqual(headers["Host"], f"127.0.0.1:{self.upstream.server.server_port}")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("Keep-Alive", headers)

    def test_streams_chunks_unbuffered(self):
        _, resp = self.get("/stream")
        self.assertEqual(resp.status, 200)
        # upstream withholds chunks 2 and 3 until this read has returned
        self.assertIn(b"data: 1", resp.read1(1024))
        self.upstream.first_chunk_read.set()
        self.assertIn(b"data: 3", resp.read())

    def test_short_upstream_body_does_not_hang_client(self):
        started = time.monotonic()
        _, resp = self.get("/short")
        with self.assertRaises(http.client.IncompleteRead):
            resp.read()
        self.assertLess(time.monotonic() - started, 5)

    def test_healthz_is_local(self):
        _, resp = self.get("/healthz")
        self.assertEqual(resp.status, 200)
        resp.read()
        self.assertEqual(self.upstream.requests, [])

    def test_healthz_fails_without_token(self):
        self.start(token_file=self.missing())
        _, resp = self.get("/healthz")
        resp.read()
        self.assertEqual(resp.status, 503)


class SettingsTest(unittest.TestCase):
    def test_upstream_has_no_default(self):
        saved = {k: os.environ.pop(k, None) for k in ("UPSTREAM", "TOKEN_FILE")}
        try:
            with self.assertRaisesRegex(tp.ConfigError, "UPSTREAM"):
                tp.Settings.from_env().validate()
            self.assertEqual(tp.Settings.from_env().token_file,
                             "/var/run/secrets/api-token/token")
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value


class FakeTokenEndpoint:
    """Token endpoint: records each grant form, answers with a numbered access token."""

    def __init__(self, status=200):
        self.forms = []
        self.status = status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.forms.append(dict(urllib.parse.parse_qsl(self.rfile.read(length).decode())))
                payload = json.dumps({"access_token": f"kc-{len(outer.forms)}", "expires_in": 60}).encode()
                if outer.status != 200:
                    payload = b'{"error":"invalid_client"}'
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = serve(Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/realms/r/protocol/openid-connect/token"


class AgentTokenTest(ProxyCase):
    def start_token_endpoint(self, status=200):
        self.token_endpoint = FakeTokenEndpoint(status)
        self.assertion_file = os.path.join(os.path.dirname(self.token_file), "keycloak-token")
        with open(self.assertion_file, "w") as handle:
            handle.write("sa-for-the-realm\n")
        self.settings = tp.Settings(
            token_file=self.token_file, upstream=self.upstream.url, port=0,
            agent_token_url=self.token_endpoint.url, agent_token_assertion_file=self.assertion_file,
            agent_token_paths=("/mcp",))
        self.proxy = tp.build_server(self.settings, port=0)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.addCleanup(self.proxy.shutdown)
        self.addCleanup(self.proxy.server_close)
        self.port = self.proxy.server_port

    def test_mcp_path_carries_the_agent_token(self):
        self.start_token_endpoint()
        _, resp = self.get("/mcp/hoolia-aib/nextcloud")
        resp.read()
        self.assertEqual(self.upstream.requests[0][2]["Authorization"], "Bearer kc-1")
        form = self.token_endpoint.forms[0]
        self.assertEqual(form["grant_type"], "client_credentials")
        self.assertEqual(form["client_assertion_type"], "urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
        self.assertEqual(form["client_assertion"], "sa-for-the-realm")

    def test_model_path_keeps_the_serviceaccount_token(self):
        self.start_token_endpoint()
        _, resp = self.get("/v1/models")
        resp.read()
        self.assertEqual(self.upstream.requests[0][2]["Authorization"], "Bearer projected-1")
        self.assertEqual(self.token_endpoint.forms, [])

    def test_agent_token_is_reused_until_it_expires(self):
        self.start_token_endpoint()
        for _ in range(3):
            _, resp = self.get("/mcp/public")
            resp.read()
        self.assertEqual(len(self.token_endpoint.forms), 1)
        self.assertEqual([r[2]["Authorization"] for r in self.upstream.requests], ["Bearer kc-1"] * 3)

    def test_token_endpoint_refusal_returns_502_and_never_forwards(self):
        self.start_token_endpoint(status=401)
        with self.assertLogs(level="ERROR") as logs:
            _, resp = self.get("/mcp/public")
            body = resp.read().decode()
        self.assertEqual(resp.status, 502)
        self.assertEqual(self.upstream.requests, [])
        self.assertNotIn("sa-for-the-realm", body + "".join(logs.output))

    def test_without_agent_token_settings_mcp_keeps_the_serviceaccount_token(self):
        _, resp = self.get("/mcp/public")
        resp.read()
        self.assertEqual(self.upstream.requests[0][2]["Authorization"], "Bearer projected-1")

    def test_settings_from_env_read_the_agent_token_variables(self):
        keys = ("UPSTREAM", "AGENT_TOKEN_URL", "AGENT_TOKEN_ASSERTION_FILE", "AGENT_TOKEN_PATHS")
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            os.environ.update({"UPSTREAM": "http://x", "AGENT_TOKEN_URL": "http://k/token",
                               "AGENT_TOKEN_PATHS": "/mcp, /a2a"})
            s = tp.Settings.from_env()
            self.assertEqual(s.agent_token_paths, ("/mcp", "/a2a"))
            self.assertEqual(s.agent_token_assertion_file, "/var/run/secrets/agent-token/token")
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
