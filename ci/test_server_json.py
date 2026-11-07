"""server.json of the MCP server charts: shape, and the registry schema when reachable."""
import json
import pathlib
import sys
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import server_json  # noqa: E402

CHARTS = pathlib.Path(__file__).parent.parent / "charts"


class ServerJsonTest(unittest.TestCase):
    def test_every_server_chart(self):
        found = 0
        for chart in sorted(CHARTS.iterdir()):
            doc = server_json.server_json(chart)
            if doc is None:
                continue
            found += 1
            self.assertEqual(doc["name"], f"eu.openshift.apps/{chart.name}")
            self.assertLessEqual(len(doc["description"]), 100)
            self.assertTrue(doc["remotes"][0]["url"].startswith("https://"))
            meta = doc["_meta"]["io.modelcontextprotocol.registry/publisher-provided"]
            self.assertIn("mcp.openshift.eu/server", meta["labels"])
        self.assertGreater(found, 0)

    def test_bundle_is_not_a_server(self):
        self.assertIsNone(server_json.server_json(CHARTS / "mcp-bundle-google-workspace"))

    def test_registry_schema(self):
        try:
            import jsonschema
            with urllib.request.urlopen(server_json.SCHEMA, timeout=20) as r:
                schema = json.load(r)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"schema or jsonschema unavailable: {exc}")
        for chart in sorted(CHARTS.iterdir()):
            doc = server_json.server_json(chart)
            if doc is not None:
                jsonschema.validate(doc, schema)


if __name__ == "__main__":
    unittest.main()
