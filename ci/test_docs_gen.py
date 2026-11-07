import importlib.util
import pathlib
import unittest

import yaml

SPEC = importlib.util.spec_from_file_location("docs_gen", pathlib.Path(__file__).parent / "docs_gen.py")
docs_gen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs_gen)

VALUES = """
# the release's team
team: ""

image:
  # upstream image
  repository: example/app
  tag: "1.0"

tools:
- name: a
  # not a value of its own
  args: x

# extra labels
labels: {}
mcp:
  servers: []
"""

SCHEMA = {"properties": {"image": {"properties": {"tag": {"enum": ["1.0", "2.0"]}}}}}


class ValuesRows(unittest.TestCase):
    def rows(self):
        return {r[0]: r for r in docs_gen.values_rows(VALUES, yaml.safe_load(VALUES), SCHEMA)}

    def test_comment_above_key_is_description(self):
        self.assertEqual(self.rows()["team"][3], "the release's team")
        self.assertEqual(self.rows()["image.repository"][3], "upstream image")

    def test_keys_inside_lists_are_not_values(self):
        self.assertNotIn("tools.args", self.rows())
        self.assertIn("tools", self.rows())

    def test_schema_enum_and_types(self):
        self.assertIn("One of: `1.0`, `2.0`.", self.rows()["image.tag"][3])
        self.assertEqual(self.rows()["labels"][1], "object")
        self.assertEqual(self.rows()["mcp.servers"][2], "`[]`")

    def test_map_without_comment_is_listed_by_keys(self):
        self.assertNotIn("image", self.rows())
        self.assertNotIn("mcp", self.rows())


class Kinds(unittest.TestCase):
    def test_kind_from_annotations_and_keywords(self):
        server = {"_name": "mcp-gmail", "annotations": {"mcp.openshift.eu/server": "gmail"}}
        bundle = {"_name": "mcp-bundle-x", "keywords": ["mcp", "mcp-bundle"]}
        agent = {"_name": "kagent", "keywords": ["mcp", "agents"]}
        self.assertEqual(docs_gen.kind_of(server), "MCP server")
        self.assertEqual(docs_gen.kind_of(bundle), "MCP bundle")
        self.assertEqual(docs_gen.kind_of(agent), "Application")


if __name__ == "__main__":
    unittest.main()
