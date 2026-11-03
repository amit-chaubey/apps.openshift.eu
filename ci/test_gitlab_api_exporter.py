import importlib.util
import pathlib
import unittest
import urllib.error
from datetime import UTC, datetime

SPEC = importlib.util.spec_from_file_location(
    "exporter", pathlib.Path(__file__).parent.parent / "charts/gitlab/files/gitlab-api-exporter.py")
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
PROJECT = {"id": 20, "path_with_namespace": "group/project", "default_branch": "main"}


class FakeApi:
    def __init__(self, routes, broken=()):
        self.routes, self.broken = routes, broken

    def get(self, path, **query):
        if path in self.broken:
            raise urllib.error.URLError("down")
        return self.routes.get(path, []), None

    def pages(self, path, **query):
        if path == "/projects/20/merge_requests":
            return self.routes.get(path + ":" + query["state"], [])
        return self.get(path)[0]


def mr(iid, author, merger, merged_at="2026-10-31T10:00:00Z"):
    return {"iid": iid, "target_branch": "main", "merged_at": merged_at, "author": {"username": author},
            "merged_by": {"username": merger}}


def routes(**extra):
    base = {
        "/projects": [PROJECT],
        "/projects/20/merge_requests:merged": [mr(1, "a", "a"), mr(2, "a", "b"), mr(3, "a", "b", "2026-09-01T00:00:00Z")],
        "/projects/20/protected_branches": [{"name": "main", "allow_force_push": False,
                                             "push_access_levels": [{"access_level": 0}]}],
        "/projects/20/pipelines": [{"status": "running", "source": "push", "updated_at": "2026-11-01T11:00:00Z"},
                                   {"status": "failed", "source": "schedule", "updated_at": "2026-11-01T10:00:00Z"},
                                   {"status": "success", "source": "push", "updated_at": "2026-11-01T09:00:00Z"}],
        "/projects/20/pipeline_schedules": [{"id": 1, "description": "Confidence gate scan", "ref": "main", "active": True}],
        "/projects/20/pipeline_schedules/1/pipelines": [{"status": "running"}, {"status": "failed"}, {"status": "failed"},
                                                        {"status": "success"}],
    }  # fmt: skip
    base.update(extra)
    return base


def lines(text, name):
    return [ln for ln in text.splitlines() if ln.startswith(name + "{")]


class Collect(unittest.TestCase):
    def test_every_merge_in_the_window_carries_author_merger_and_self_merged(self):
        text = exporter.collect(FakeApi(routes()), "gitlab.example.org", 30, NOW)
        merges = lines(text, "gitlab_merges")
        self.assertEqual(len(merges), 2)
        self.assertIn('author="a",branch="main",gitlab_instance="gitlab.example.org",iid="2",merger="b",'
                      'repository="group/project",self_merged="false",tenant="group"', merges[1])  # fmt: skip
        self.assertIn('self_merged="true"', merges[0])

    def test_protection_and_the_default_branch_pipeline(self):
        text = exporter.collect(FakeApi(routes()), "gitlab.example.org", 30, NOW)
        self.assertTrue(lines(text, "gitlab_protected_branch_push_access_level")[0].endswith(" 0"))
        self.assertTrue(lines(text, "gitlab_default_branch_protected")[0].endswith(" 1"))
        # the running and the scheduled pipeline do not count
        self.assertTrue(lines(text, "gitlab_default_branch_pipeline_failed")[0].endswith(" 0"))

    def test_consecutive_failed_schedule_runs_are_counted(self):
        text = exporter.collect(FakeApi(routes()), "gitlab.example.org", 30, NOW)
        self.assertTrue(lines(text, "gitlab_pipeline_schedule_failed_in_row")[0].endswith(" 2"))

    def test_a_merge_request_with_both_verdicts_and_no_gate_status_is_unscored(self):
        open_mr = {"iid": 9, "sha": "s1", "updated_at": "2026-11-01T08:00:00Z"}
        statuses = [{"name": n, "status": "success", "finished_at": "2026-11-01T08:00:00Z"} for n in exporter.AI_CHECKS]
        api = FakeApi(routes(**{"/projects/20/merge_requests:opened": [open_mr],
                                "/projects/20/repository/commits/s1/statuses": statuses}))  # fmt: skip
        text = exporter.collect(api, "gitlab.example.org", 30, NOW)
        self.assertTrue(lines(text, "gitlab_merge_requests_unscored")[0].endswith(" 1"))
        scored = statuses + [{"name": "confidence-gate", "status": "failed"}]
        api.routes["/projects/20/repository/commits/s1/statuses"] = scored
        self.assertTrue(lines(exporter.collect(api, "gitlab.example.org", 30, NOW), "gitlab_merge_requests_unscored")[0].endswith(" 0"))

    def test_a_broken_project_is_counted_and_the_rest_still_served(self):
        other = {"id": 21, "path_with_namespace": "group/other", "default_branch": "main"}
        api = FakeApi(routes(**{"/projects": [other, PROJECT]}), broken=("/projects/21/pipelines",))
        text = exporter.collect(api, "gitlab.example.org", 30, NOW)
        self.assertTrue(lines(text, "gitlab_api_exporter_project_errors")[0].endswith(" 1"))
        self.assertEqual(len(lines(text, "gitlab_merges")), 2)

    def test_only_http_urls_are_read(self):
        with self.assertRaises(ValueError):
            exporter.Api("file:///etc/passwd", "t")
        self.assertEqual(exporter.Api("http://gitlab:8181/", "t").url, "http://gitlab:8181/api/v4")

    def test_label_values_are_escaped(self):
        m = exporter.Metrics()
        m.add("x", "h", {"description": 'a "b"\\c'}, 1)
        self.assertIn('description="a \\"b\\"\\\\c"', m.text())


if __name__ == "__main__":
    unittest.main()
