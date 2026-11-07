"""Agent token routing of charts/openhands/files/mcp-sync.py, against in-process fake servers."""
import importlib.util
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISSUER = "https://login.example.com/realms/public"
TMP = tempfile.mkdtemp(prefix="mcp-sync-tests-")
os.environ.update({
    "UPSTREAM": "https://gateway.example.com",
    "TOKEN_PROXY_PORT": "8099",
    "LABEL_DOMAIN": "mcp.openshift.eu",
    "AGENT_TOKEN_URL": ISSUER + "/protocol/openid-connect/token",
    "TARGETS_FILE": os.path.join(TMP, "targets.json"),
})
spec = importlib.util.spec_from_file_location("mcp_sync", os.path.join(ROOT, "charts/openhands/files/mcp-sync.py"))
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)


def remote(name, url, annotations=None, namespace="acme-agents"):
    return {"metadata": {"name": name, "namespace": namespace, "annotations": annotations or {}},
            "spec": {"url": url, "protocol": "STREAMABLE_HTTP"}}


class MetadataServer:
    """HTTPS server with protected resource metadata; records every request's headers."""

    def __init__(self, servers, resource_path="/mcp/officeagent", where="rfc"):
        self.requests = []
        outer = self
        self.doc = None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self):
                outer.requests.append((self.command, self.path, dict(self.headers)))
                wanted = {"rfc": "/.well-known/oauth-protected-resource" + resource_path,
                          "appended": resource_path + "/.well-known/oauth-protected-resource"}[where]
                if self.command == "GET" and self.path == wanted:
                    body = json.dumps(outer.doc).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(401 if self.command == "POST" else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _answer

        cert, key = os.path.join(TMP, "cert.pem"), os.path.join(TMP, "key.pem")
        if not os.path.exists(cert):
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                            "-keyout", key, "-out", cert], check=True, capture_output=True)
        os.environ["SSL_CERT_FILE"] = cert
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"https://127.0.0.1:{self.server.server_port}"
        self.url = self.base + resource_path
        self.doc = {"resource": self.url, "authorization_servers": servers}


class AnnotationTest(unittest.TestCase):
    def setUp(self):
        ms.TARGETS.clear()
        ms.PRM_CACHE.clear()

    def test_same_issuer_goes_through_the_token_proxy(self):
        url = "https://office.example.com/mcp/officeagent"
        server, reason = ms.to_server(remote("officeagent", url, {
            "mcp.openshift.eu/auth": "oidc", "mcp.openshift.eu/oidc-issuer": ISSUER + "/"}))
        self.assertEqual(reason, "")
        self.assertEqual(server["url"], "http://127.0.0.1:8099/_target/acme-agents.officeagent")
        self.assertEqual(ms.TARGETS, {"acme-agents.officeagent": url})

    def test_other_issuer_is_skipped_with_a_reason(self):
        server, reason = ms.to_server(remote("x", "https://x.example.com/mcp", {
            "mcp.openshift.eu/auth": "oidc", "mcp.openshift.eu/oidc-issuer": "https://other.example.com/realms/a"}))
        self.assertIsNone(server)
        self.assertIn("not of the agent's issuer", reason)
        self.assertEqual(ms.TARGETS, {})

    def test_plain_http_outside_the_cluster_is_skipped(self):
        server, reason = ms.to_server(remote("x", "http://x.example.com/mcp", {
            "mcp.openshift.eu/auth": "oidc", "mcp.openshift.eu/oidc-issuer": ISSUER}))
        self.assertIsNone(server)
        self.assertIn("neither https nor a cluster Service", reason)

    def test_cluster_service_over_http_is_allowed(self):
        url = "http://officeagent.acme-nextcloud.svc:13080/"
        server, _ = ms.to_server(remote("officeagent", url, {
            "mcp.openshift.eu/auth": "oidc", "mcp.openshift.eu/oidc-issuer": ISSUER}))
        self.assertTrue(server["url"].endswith("/_target/acme-agents.officeagent"))

    def test_human_oauth_login_is_unchanged(self):
        ms.oauth_client = lambda obj: ({"type": "oauth2"}, "")
        server, _ = ms.to_server(remote("gmail", "https://gmailmcp.googleapis.com/mcp/v1", {
            "openhands.dev/auth": "oauth2", "mcp.openshift.eu/auth": "oidc",
            "mcp.openshift.eu/oidc-issuer": ISSUER}))
        self.assertEqual(server["url"], "https://gmailmcp.googleapis.com/mcp/v1")
        self.assertEqual(server["auth"]["strategy"], "oauth2")
        self.assertEqual(ms.TARGETS, {})

    def test_gateway_url_keeps_the_serviceaccount_path(self):
        server, _ = ms.to_server(remote("slack", "https://gateway.example.com/mcp/acme-agents/slack"))
        self.assertEqual(server["url"], "http://127.0.0.1:8099/mcp/acme-agents/slack")

    def test_targets_file_is_written_for_the_proxy(self):
        ms.to_server(remote("officeagent", "https://office.example.com/mcp/officeagent", {
            "mcp.openshift.eu/auth": "oidc", "mcp.openshift.eu/oidc-issuer": ISSUER}))
        ms.write_targets()
        with open(os.environ["TARGETS_FILE"]) as f:
            self.assertEqual(json.load(f), {"acme-agents.officeagent": "https://office.example.com/mcp/officeagent"})


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        ms.TARGETS.clear()
        ms.PRM_CACHE.clear()

    def test_metadata_at_the_rfc_address_names_the_agent_issuer(self):
        fake = MetadataServer([ISSUER])
        server, _ = ms.to_server(remote("officeagent", fake.url))
        self.assertTrue(server["url"].endswith("/_target/acme-agents.officeagent"))
        self.assertEqual(ms.TARGETS["acme-agents.officeagent"], fake.url)
        # probing never sends a credential
        self.assertTrue(all("Authorization" not in h for _, _, h in fake.requests))

    def test_metadata_appended_to_the_server_path(self):
        fake = MetadataServer([ISSUER], where="appended")
        server, _ = ms.to_server(remote("officeagent", fake.url))
        self.assertTrue(server["url"].endswith("/_target/acme-agents.officeagent"))

    def test_metadata_of_another_issuer_leaves_the_url_as_is(self):
        fake = MetadataServer(["https://other.example.com/realms/a"])
        server, _ = ms.to_server(remote("x", fake.url))
        self.assertEqual(server["url"], fake.url)
        self.assertEqual(ms.TARGETS, {})

    def test_metadata_for_another_resource_is_ignored(self):
        fake = MetadataServer([ISSUER])
        fake.doc["resource"] = "https://elsewhere.example.com/mcp"
        server, _ = ms.to_server(remote("x", fake.url))
        self.assertEqual(server["url"], fake.url)

    def test_results_are_cached(self):
        fake = MetadataServer([ISSUER])
        ms.to_server(remote("officeagent", fake.url))
        seen = len(fake.requests)
        ms.to_server(remote("officeagent", fake.url))
        self.assertEqual(len(fake.requests), seen)


class CredentialTest(unittest.TestCase):
    """The agent's own credential from a mounted vault secret (chart value mcp.servers auth)."""

    def setUp(self):
        ms.TARGETS.clear()
        ms.CRED_SENT.clear()
        self.dir = tempfile.mkdtemp(dir=TMP)
        self.write("JIRA_USERNAME", "agent@example.com")
        self.write("JIRA_API_TOKEN", "token-1\n")
        self.spec = {"dir": self.dir, "header": "X-Atlassian-Authorization", "format": "basic",
                     "user": "JIRA_USERNAME", "token": "JIRA_API_TOKEN"}
        ms.SERVER_SETTINGS.clear()
        ms.SERVER_SETTINGS["atlassian"] = {"auth": self.spec}
        self.calls = []
        self.settings = {}
        self.saved = (ms.agent, ms.remote_mcp_servers, ms.visible_servers)

        def agent(method, path, body=None, plaintext=False):
            self.calls.append((method, path, body))
            if method == "GET":
                return 200, {"agent_settings": {"mcp_config": self.settings}}
            return 200, {}
        ms.agent = agent
        ms.remote_mcp_servers = lambda ns: [remote("atlassian", "https://gateway.example.com/mcp/acme/atlassian", namespace=ns)]
        ms.visible_servers = lambda ns: []

    def tearDown(self):
        ms.agent, ms.remote_mcp_servers, ms.visible_servers = self.saved
        ms.SERVER_SETTINGS.clear()

    def write(self, key, value):
        with open(os.path.join(self.dir, key), "w") as f:
            f.write(value)

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def test_formats(self):
        import base64
        self.assertEqual(ms.credential(self.spec), ("X-Atlassian-Authorization",
                         "Basic " + base64.b64encode(b"agent@example.com:token-1").decode()))
        self.assertEqual(ms.credential({**self.spec, "format": "bearer"})[1], "Bearer token-1")
        self.assertEqual(ms.credential({**self.spec, "format": "raw", "header": ""}), ("Authorization", "token-1"))

    def test_missing_file_gives_no_credential(self):
        os.remove(os.path.join(self.dir, "JIRA_USERNAME"))
        self.assertIsNone(ms.credential(self.spec))

    def test_entry_under_the_proxy_gets_the_header_as_a_secret(self):
        server = ms.with_credential("atlassian", "acme", {"url": "http://127.0.0.1:8099/mcp/acme/atlassian", "description": "RemoteMCPServer acme/atlassian"})
        self.assertEqual(server["auth"]["strategy"], "api_key")
        self.assertEqual(server["auth"]["header_name"], "X-Atlassian-Authorization")
        self.assertTrue(server["description"].endswith(ms.CREDENTIAL_NOTE))

    def test_authorization_under_the_proxy_is_refused(self):
        self.spec["header"] = "Authorization"
        server = ms.with_credential("atlassian", "acme", {"url": "http://127.0.0.1:8099/mcp/acme/atlassian", "description": "d"})
        self.assertNotIn("auth", server)

    def test_plain_http_outside_the_cluster_gets_no_credential(self):
        server = ms.with_credential("atlassian", "acme", {"url": "http://mcp.example.com/mcp", "description": "d"})
        self.assertNotIn("auth", server)

    def test_namespace_pins_the_server(self):
        self.spec["namespace"] = "other"
        server = ms.with_credential("atlassian", "acme", {"url": "https://mcp.example.com/mcp", "description": "d"})
        self.assertNotIn("auth", server)

    def test_sync_writes_once_and_again_on_rotation(self):
        ms.sync("acme", {})
        added = [c for c in self.writes() if c[0] == "POST"]
        self.assertEqual(len(added), 1)
        body = added[0][2]
        self.assertEqual(body["auth"]["header_name"], "X-Atlassian-Authorization")
        self.assertTrue(body["enabled"])
        # the agent-server shows the value redacted
        have = {**body, "auth": {**body["auth"], "value": "**********"}}
        self.calls.clear()
        ms.sync("acme", {"atlassian": have})
        self.assertEqual(self.writes(), [])
        self.write("JIRA_API_TOKEN", "token-2")
        ms.sync("acme", {"atlassian": have})
        patched = self.writes()
        self.assertEqual(len(patched), 1)
        self.assertEqual(patched[0][1], "/api/settings")
        self.assertIn("auth", patched[0][2]["agent_settings_diff"]["mcp_config"]["atlassian"])

    def test_after_a_start_the_stored_value_is_compared_once(self):
        ms.sync("acme", {})
        body = [c for c in self.writes() if c[0] == "POST"][0][2]
        self.settings = {"atlassian": body}
        ms.CRED_SENT.clear()
        self.calls.clear()
        ms.sync("acme", {"atlassian": {**body, "auth": {**body["auth"], "value": "**********"}}})
        self.assertEqual(self.writes(), [])

    def test_credential_taken_out_of_the_values_is_removed(self):
        ms.sync("acme", {})
        body = [c for c in self.writes() if c[0] == "POST"][0][2]
        ms.SERVER_SETTINGS.clear()
        self.calls.clear()
        ms.sync("acme", {"atlassian": body})
        patched = self.writes()
        self.assertEqual(len(patched), 1)
        self.assertIsNone(patched[0][2]["agent_settings_diff"]["mcp_config"]["atlassian"]["auth"])

    def test_server_by_url(self):
        ms.SERVER_SETTINGS["extra"] = {"url": "https://mcp.example.com/mcp", "auth": {**self.spec, "format": "bearer", "header": "Authorization"}}
        ms.sync("acme", {})
        added = {c[1].rsplit("/", 1)[-1]: c[2] for c in self.writes() if c[0] == "POST"}
        self.assertTrue(added["extra"]["description"].startswith(ms.VALUE_PREFIX))
        self.assertEqual(added["extra"]["auth"]["header_name"], "Authorization")
        self.assertEqual(added["extra"]["url"], "https://mcp.example.com/mcp")


if __name__ == "__main__":
    unittest.main()
