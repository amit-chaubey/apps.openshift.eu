"""Profile defaults of charts/openhands/files/seed-settings.py."""
import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["HOME"] = tempfile.mkdtemp(prefix="seed-settings-tests-")
spec = importlib.util.spec_from_file_location("seed_settings", os.path.join(ROOT, "charts/openhands/files/seed-settings.py"))
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

CONN = {"display_name": "Gateway", "provider": "openai"}


class FakeApi:
    def __init__(self, profiles):
        self.profiles = profiles
        self.posted = {}

    def request(self, method, path, body=None):
        if path == "/api/llm/provider-connections":
            return 200, [{"id": "c1", "display_name": "Gateway"}]
        if path == "/api/profiles":
            return 200, {"profiles": [{"name": n, "provider_connection_id": "c1"} for n in self.profiles]}
        if method == "GET" and path.startswith("/api/profiles/"):
            return 200, {"config": dict(self.profiles[path.rsplit("/", 1)[-1]])}
        if method == "POST" and path.startswith("/api/profiles/"):
            name = path.rsplit("/", 1)[-1]
            self.posted[name] = body["llm"]
            self.profiles[name] = {k: v for k, v in body["llm"].items() if k != "provider_connection_id"}
            return 200, None
        return 404, None


class ProfileDefaults(unittest.TestCase):
    def setUp(self):
        ss.STATE = tempfile.mkdtemp(prefix="state-")
        ss.DEFAULTS.clear()
        ss.MODEL_DEFAULTS.clear()
        ss.DEFAULTS.update({"stream": True})
        self.api = FakeApi({
            "glm-5.3-flash": {"model": "openai/vllm/glm-5.3-flash", "reasoning_effort": "high", "stream": True},
            "claude-opus-5-5": {"model": "openai/claude-proxy/claude-opus-5-5", "reasoning_effort": "high", "stream": True},
        })
        ss.request = self.api.request
        ss.call = lambda method, path, body=None: self.api.request(method, path, body)[0]

    def test_model_defaults_reach_only_that_model(self):
        ss.MODEL_DEFAULTS.update({"glm-5.3-flash": {"reasoning_effort": "low"}})
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["glm-5.3-flash"]["reasoning_effort"], "low")
        self.assertEqual(self.api.profiles["claude-opus-5-5"]["reasoning_effort"], "high")
        self.assertNotIn("claude-opus-5-5", self.api.posted)

    def test_once_per_value(self):
        ss.MODEL_DEFAULTS.update({"glm-5.3-flash": {"reasoning_effort": "low"}})
        ss.seed_profile_defaults(CONN)
        # an edit in the UI stays until the value changes
        self.api.profiles["glm-5.3-flash"]["reasoning_effort"] = "high"
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["glm-5.3-flash"]["reasoning_effort"], "high")
        ss.MODEL_DEFAULTS["glm-5.3-flash"] = {"reasoning_effort": "low", "max_output_tokens": 32000}
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["glm-5.3-flash"]["reasoning_effort"], "low")

    def test_model_value_wins_over_profile_defaults(self):
        ss.DEFAULTS["reasoning_effort"] = "high"
        ss.MODEL_DEFAULTS.update({"glm-5.3-flash": {"reasoning_effort": "low"}})
        self.assertEqual(ss.defaults_for("openai/vllm/glm-5.3-flash"),
                         {"stream": True, "reasoning_effort": "low"})
        self.assertEqual(ss.defaults_for("openai/claude-proxy/claude-opus-5-5"),
                         {"stream": True, "reasoning_effort": "high"})
        self.assertEqual(ss.defaults_for(None), {"stream": True, "reasoning_effort": "high"})

    def test_marker_unchanged_without_model_defaults(self):
        # releases before modelProfileDefaults wrote this marker; it must still count
        import hashlib
        import json
        old = hashlib.sha256(json.dumps({"stream": True}, sort_keys=True).encode()).hexdigest()[:8]
        open(os.path.join(ss.STATE, ".seeded-profile-defaults-" + old), "w").close()
        self.api.profiles["glm-5.3-flash"]["stream"] = False
        ss.seed_profile_defaults(CONN)
        self.assertFalse(self.api.profiles["glm-5.3-flash"]["stream"])


LISTING = [
    {"id": "claude/claude-opus-5-5", "context_length": 1000000, "max_input_tokens": 1000000},
    {"id": "claude/claude-haiku-4-5", "context_length": 200000, "max_input_tokens": 200000},
    {"id": "z-ai/glm-5.3-flash", "context_length": 262144, "max_input_tokens": None},
    {"id": "qwen/qwen3-4b", "context_length": 16384},
    {"id": "other/claude-opus-5-5", "context_length": 200000},
    {"id": "no-window"},
]


class ContextLimits(unittest.TestCase):
    def setUp(self):
        ss.STATE = tempfile.mkdtemp(prefix="state-")
        ss.DEFAULTS.clear()
        ss.MODEL_DEFAULTS.clear()
        ss.CONTEXT.clear()
        self.api = FakeApi({
            "glm-5.3-flash": {"model": "openai/vllm/glm-5.3-flash"},
            "claude-opus-5-5": {"model": "openai/claude-proxy/claude-opus-5-5"},
            "claude-haiku-4-5": {"model": "openai/claude-proxy/claude-haiku-4-5"},
        })
        ss.request = self.api.request
        ss.call = lambda method, path, body=None: self.api.request(method, path, body)[0]

    def test_share_of_window_above_minimum(self):
        # haiku (120k) and qwen3-4b (9k) stay below the minimum; the first entry of a
        # name wins; context_length stands in for a missing max_input_tokens
        self.assertEqual(ss.gateway_context(LISTING, 0.6, 150000),
                         {"claude-opus-5-5": 600000, "glm-5.3-flash": 157000})

    def test_default_minimum_keeps_only_1m_windows(self):
        # the chart default: a 262k window (157k) is below the tool definitions
        self.assertEqual(ss.gateway_context(LISTING, 0.6, 400000), {"claude-opus-5-5": 600000})

    def test_limits_reach_profiles_once(self):
        ss.CONTEXT.update(ss.gateway_context(LISTING, 0.6, 150000))
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["claude-opus-5-5"]["max_input_tokens"], 600000)
        self.assertEqual(self.api.profiles["glm-5.3-flash"]["max_input_tokens"], 157000)
        self.assertNotIn("max_input_tokens", self.api.profiles["claude-haiku-4-5"])
        # a UI edit stays until a limit changes
        self.api.profiles["claude-opus-5-5"]["max_input_tokens"] = 800000
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["claude-opus-5-5"]["max_input_tokens"], 800000)

    def test_explicit_values_win(self):
        ss.CONTEXT.update({"claude-opus-5-5": 600000, "glm-5.3-flash": 157000})
        ss.DEFAULTS["max_input_tokens"] = 300000
        ss.MODEL_DEFAULTS["glm-5.3-flash"] = {"max_input_tokens": 100000}
        self.assertEqual(ss.defaults_for("openai/claude-proxy/claude-opus-5-5")["max_input_tokens"], 300000)
        self.assertEqual(ss.defaults_for("openai/vllm/glm-5.3-flash")["max_input_tokens"], 100000)

    def test_new_profile_gets_limit(self):
        ss.CONTEXT.update({"claude-opus-5-5": 600000})
        self.assertEqual(ss.defaults_for("openai/claude-proxy/claude-opus-5-5"), {"max_input_tokens": 600000})
        self.assertEqual(ss.defaults_for("openai/claude-proxy/claude-haiku-4-5"), {})


class GatewayStart(unittest.TestCase):
    """The token proxy in front of the gateway may answer only after a few tries."""

    def setUp(self):
        ss.STATE = tempfile.mkdtemp(prefix="state-")
        ss.DEFAULTS.clear()
        ss.MODEL_DEFAULTS.clear()
        ss.CONTEXT.clear()
        ss.CONTEXT_MISSING = False
        ss.GATEWAY_DELAY = 0
        self.api = FakeApi({"claude-opus-5-5": {"model": "openai/claude-proxy/claude-opus-5-5"}})
        ss.request = self.api.request
        ss.call = lambda method, path, body=None: self.api.request(method, path, body)[0]
        self.urlopen = ss.urllib.request.urlopen
        self.addCleanup(setattr, ss.urllib.request, "urlopen", self.urlopen)
        self.addCleanup(ss.CONTEXT.clear)
        self.addCleanup(setattr, ss, "CONTEXT_MISSING", False)

    def gateway(self, failures):
        """urlopen that refuses failures times, then lists LISTING."""
        import io
        import json
        calls = []

        def urlopen(req, timeout=None):
            calls.append(req.full_url)
            if len(calls) <= failures:
                raise ConnectionRefusedError("token proxy not listening yet")
            return io.BytesIO(json.dumps({"data": LISTING}).encode())
        ss.urllib.request.urlopen = urlopen
        return calls

    def test_listing_after_retries(self):
        calls = self.gateway(failures=3)
        listing = ss.gateway_listing({"base_url": "http://127.0.0.1:8099/v1"})
        self.assertEqual(listing, LISTING)
        self.assertEqual(len(calls), 4)

    def test_no_listing_writes_no_marker(self):
        self.gateway(failures=ss.GATEWAY_TRIES)
        self.assertIsNone(ss.gateway_listing({"base_url": "http://127.0.0.1:8099/v1"}))
        # an older marker form must not count either: without the windows nothing is done
        ss.DEFAULTS.update({"stream": True})
        ss.CONTEXT_MISSING = True
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.posted, {})
        self.assertEqual([f for f in os.listdir(ss.STATE) if f.startswith(".seeded-profile-defaults-")], [])
        # the next start, with the gateway up, writes the limit and the marker
        ss.CONTEXT_MISSING = False
        ss.CONTEXT.update(ss.gateway_context(LISTING, 0.6, 400000))
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["claude-opus-5-5"]["max_input_tokens"], 600000)
        self.assertEqual(len([f for f in os.listdir(ss.STATE) if f.startswith(".seeded-profile-defaults-")]), 1)


class FakeAgentApi:
    def __init__(self, llm_profiles, active, agents):
        self.llm_profiles, self.active, self.agents = llm_profiles, active, agents
        self.posted = []

    def request(self, method, path, body=None):
        if path == "/api/profiles":
            return 200, {"profiles": [{"name": n} for n in self.llm_profiles], "active_profile": self.active}
        if path == "/api/agent-profiles":
            return 200, {"profiles": [dict(a) for a in self.agents.values()]}
        name = path.rsplit("/", 1)[-1]
        if method == "GET" and path.startswith("/api/agent-profiles/"):
            return 200, {"name": name, "profile": dict(self.agents[name])}
        if method == "POST" and path.startswith("/api/agent-profiles/"):
            self.posted.append(name)
            self.agents[name] = dict(body)
            return 200, None
        return 404, None


class AgentLlmRefs(unittest.TestCase):
    def use(self, api):
        ss.request = api.request
        ss.call = lambda method, path, body=None: api.request(method, path, body)[0]

    def test_dangling_ref_moves_to_active(self):
        api = FakeAgentApi(["glm-5.3-flash"], "glm-5.3-flash", {
            "default": {"name": "default", "agent_kind": "openhands", "llm_profile_ref": "default", "revision": 1},
            "claude-code": {"name": "claude-code", "agent_kind": "acp", "llm_profile_ref": None},
        })
        self.use(api)
        ss.repair_agent_llm_refs()
        self.assertEqual(api.agents["default"]["llm_profile_ref"], "glm-5.3-flash")
        self.assertEqual(api.agents["default"]["revision"], 1)
        self.assertEqual(api.posted, ["default"])

    def test_valid_ref_stays(self):
        api = FakeAgentApi(["glm-5.3-flash", "claude-opus-5-5"], "claude-opus-5-5", {
            "default": {"name": "default", "agent_kind": "openhands", "llm_profile_ref": "glm-5.3-flash"},
        })
        self.use(api)
        ss.repair_agent_llm_refs()
        self.assertEqual(api.agents["default"]["llm_profile_ref"], "glm-5.3-flash")
        self.assertEqual(api.posted, [])

    def test_no_active_profile_changes_nothing(self):
        api = FakeAgentApi([], None, {
            "default": {"name": "default", "agent_kind": "openhands", "llm_profile_ref": "default"},
        })
        self.use(api)
        ss.repair_agent_llm_refs()
        self.assertEqual(api.posted, [])


if __name__ == "__main__":
    unittest.main()
