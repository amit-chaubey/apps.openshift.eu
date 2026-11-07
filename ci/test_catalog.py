"""catalog.json of the chart website: every chart has a type, a category and a vendor."""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import catalog  # noqa: E402


def chart(root: pathlib.Path, name: str, text: str) -> None:
    (root / name).mkdir()
    (root / name / "Chart.yaml").write_text(f"apiVersion: v2\nname: {name}\nversion: 0.1.0\n{text}")


class CatalogTest(unittest.TestCase):
    def test_every_chart_on_main(self):
        cat = catalog.catalog()
        self.assertGreater(len(cat), 0)
        self.assertEqual(catalog.problems(cat), [])
        self.assertEqual(cat["nextcloud"]["type"], "app")
        self.assertEqual(cat["mcp-atlassian"]["type"], "mcp-server")
        self.assertNotIn("mcp-auth", cat)

    def test_types_and_fallbacks(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            chart(root, "app", "annotations:\n  apps.openshift.eu/category: collaboration\n"
                               "  apps.openshift.eu/vendor: Nextcloud GmbH\n")
            chart(root, "mcp-gmail", "annotations:\n  mcp.openshift.eu/server: gmail\n"
                                     "  mcp.openshift.eu/bundle: google-workspace\n"
                                     "  mcp.openshift.eu/category: productivity\n"
                                     "  apps.openshift.eu/vendor: Google\n")
            chart(root, "mcp-bundle-google-workspace",
                  "annotations:\n  mcp.openshift.eu/bundle: google-workspace\n")
            chart(root, "mcp-all", "keywords:\n- mcp\n")
            cat = catalog.catalog(root)
            self.assertEqual(cat["app"], {"type": "app", "category": "collaboration",
                                          "vendor": "Nextcloud GmbH"})
            self.assertEqual(cat["mcp-gmail"]["type"], "mcp-server")
            self.assertEqual(cat["mcp-gmail"]["category"], "productivity")
            self.assertEqual(cat["mcp-gmail"]["bundle"], "google-workspace")
            self.assertEqual(cat["mcp-bundle-google-workspace"]["type"], "mcp-bundle")
            self.assertEqual(cat["mcp-all"]["type"], "mcp-bundle")
            found = catalog.problems(cat)
            self.assertIn("mcp-all: Chart.yaml lacks annotation apps.openshift.eu/vendor", found)
            self.assertIn("mcp-bundle-google-workspace: Chart.yaml lacks annotation "
                          "apps.openshift.eu/category", found)

    def test_category_format(self):
        found = catalog.problems({"x": {"type": "app", "category": "AI Agent", "vendor": "v"}})
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
