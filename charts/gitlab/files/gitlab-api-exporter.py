"""Prometheus exporter for GitLab API data: merges, branch protection, pipelines.

Reads the GitLab API with a read-only token (Reporter, read_api) and serves the
metrics of its last collection on /metrics. Python standard library only.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TIMEOUT = 30
AI_CHECKS = ("ai-review/claude", "ai-review/codex")
GATE = "confidence-gate"
# the gate scores a head within minutes of both AI verdicts; dormant merge requests are left alone
UNSCORED_AFTER = timedelta(hours=2)
DORMANT = timedelta(days=14)
FINAL = ("success", "failed", "canceled")


def parse_time(value):
    return datetime.fromisoformat(value) if value else None


class Api:
    def __init__(self, url, token):
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise ValueError(f"GITLAB_URL must be http or https: {url}")
        self.url = url.rstrip("/") + "/api/v4"
        self.token = token

    def get(self, path, **query):
        url = self.url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": self.token})
        # the scheme is checked in __init__
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # nosec B310
            return json.loads(response.read()), response.headers.get("X-Next-Page")

    def pages(self, path, **query):
        page, items = "1", []
        while page:
            body, page = self.get(path, per_page=100, page=page, **query)
            items += body
        return items


def _escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Metrics:
    """Prometheus text format, one HELP and TYPE per metric."""

    def __init__(self):
        self.series = {}

    def add(self, name, help_text, labels, value):
        self.series.setdefault(name, (help_text, []))[1].append((labels, value))

    def text(self):
        out = []
        for name, (help_text, rows) in sorted(self.series.items()):
            out += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
            for labels, value in rows:
                pairs = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
                out.append(f"{name}{{{pairs}}} {value}")
        return "\n".join(out) + "\n"


def merges(api, pid, labels, metrics, now, merge_days):
    cutoff = now - timedelta(days=merge_days)
    for mr in api.pages(f"/projects/{pid}/merge_requests", state="merged", updated_after=cutoff.isoformat()):
        merged_at = parse_time(mr.get("merged_at"))
        if merged_at is None or merged_at < cutoff:
            continue
        author = (mr.get("author") or {}).get("username", "")
        merger = (mr.get("merged_by") or mr.get("merge_user") or {}).get("username", "")
        metrics.add("gitlab_merges", "Merge time of a merge request merged in the lookback window, by author and merger",
                    dict(labels, branch=mr.get("target_branch", ""), iid=mr["iid"], author=author, merger=merger,
                         self_merged=str(author == merger).lower()), int(merged_at.timestamp()))


def protection(api, pid, labels, metrics, default):
    protected = api.pages(f"/projects/{pid}/protected_branches")
    for branch in protected:
        push = [a.get("access_level", 0) for a in branch.get("push_access_levels") or []]
        b = dict(labels, branch=branch["name"])
        metrics.add("gitlab_protected_branch_allow_force_push", "1 when force push is allowed on a protected branch",
                    b, int(bool(branch.get("allow_force_push"))))
        metrics.add("gitlab_protected_branch_push_access_level",
                    "Lowest role allowed to push to a protected branch: 0 no one, 30 Developer, 40 Maintainer",
                    b, min(push) if push else 0)
    if default:
        metrics.add("gitlab_default_branch_protected", "1 when the default branch is protected",
                    dict(labels, branch=default), int(any(b["name"] == default for b in protected)))


def default_branch_pipeline(api, pid, labels, metrics, default):
    pipelines, _ = api.get(f"/projects/{pid}/pipelines", ref=default, per_page=20)
    finished = [p for p in pipelines if p.get("status") in FINAL and p.get("source") != "schedule"]
    if not finished:
        return
    last, b = finished[0], dict(labels, branch=default)
    metrics.add("gitlab_default_branch_pipeline_failed", "1 when the latest finished pipeline of the default branch failed",
                b, int(last["status"] == "failed"))
    metrics.add("gitlab_default_branch_pipeline_timestamp_seconds", "Update time of that pipeline",
                b, int(parse_time(last["updated_at"]).timestamp()))


def failed_in_row(runs):
    failed = 0
    for run in (r for r in runs if r.get("status") in FINAL):
        if run["status"] != "failed":
            break
        failed += 1
    return failed


def schedules(api, pid, labels, metrics):
    for schedule in api.pages(f"/projects/{pid}/pipeline_schedules"):
        s = dict(labels, schedule=schedule["id"], description=schedule.get("description", ""), ref=schedule.get("ref", ""))
        metrics.add("gitlab_pipeline_schedule_active", "1 when a pipeline schedule is active", s, int(bool(schedule.get("active"))))
        runs, _ = api.get(f"/projects/{pid}/pipeline_schedules/{schedule['id']}/pipelines", sort="desc", per_page=10)
        metrics.add("gitlab_pipeline_schedule_failed_in_row", "Consecutive failed pipelines of a schedule, newest first",
                    s, failed_in_row(runs))


def waits_for_gate(statuses, now):
    names = {s["name"]: s for s in statuses}
    ai = [names[n] for n in AI_CHECKS if n in names and names[n].get("status") in FINAL]
    if len(ai) < len(AI_CHECKS) or GATE in names:
        return False
    done = max(parse_time(s.get("finished_at") or s.get("created_at")) for s in ai)
    return now - done > UNSCORED_AFTER


def unscored(api, pid, labels, metrics, now):
    count = 0
    for mr in api.pages(f"/projects/{pid}/merge_requests", state="opened", wip="no"):
        if now - (parse_time(mr.get("updated_at")) or now) > DORMANT:
            continue
        statuses, _ = api.get(f"/projects/{pid}/repository/commits/{mr['sha']}/statuses", all="true", per_page=100)
        count += int(waits_for_gate(statuses, now))
    metrics.add("gitlab_merge_requests_unscored",
                "Open merge requests with both AI verdicts for over 2 hours and no confidence-gate status", labels, count)


def collect_project(api, project, base, metrics, now, merge_days):
    pid, repository = project["id"], project["path_with_namespace"]
    labels = dict(base, tenant=repository.split("/", 1)[0], repository=repository)
    default = project.get("default_branch")
    merges(api, pid, labels, metrics, now, merge_days)
    protection(api, pid, labels, metrics, default)
    if default:
        default_branch_pipeline(api, pid, labels, metrics, default)
    schedules(api, pid, labels, metrics)
    unscored(api, pid, labels, metrics, now)


def collect(api, instance, merge_days, now=None):
    now = now or datetime.now(UTC)
    metrics, base, failed = Metrics(), {"gitlab_instance": instance}, 0
    projects = api.pages("/projects", membership="true", archived="false", simple="true")
    for project in projects:
        try:
            collect_project(api, project, base, metrics, now, merge_days)
        # URLError and HTTPError are OSError
        except (OSError, ValueError, KeyError) as error:
            # one project must not hide the others
            failed += 1
            print(f"{project.get('path_with_namespace')}: {error}", flush=True)
    metrics.add("gitlab_api_exporter_projects", "Projects the token reads", base, len(projects))
    metrics.add("gitlab_api_exporter_project_errors", "Projects whose collection failed in the last run", base, failed)
    metrics.add("gitlab_api_exporter_last_success_timestamp_seconds", "End of the last completed collection",
                base, int(time.time()))  # fmt: skip
    return metrics.text()


class State:
    text = "# no collection yet\n"


def serve(port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = State.text.encode() if self.path == "/metrics" else b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            # every scrape would print a line; the collection logs its own errors
            return

    ThreadingHTTPServer(("", port), Handler).serve_forever()


def main():
    url = os.environ.get("GITLAB_URL", "https://code.grn.cloud")
    api = Api(url, os.environ["GITLAB_TOKEN"])
    instance = os.environ.get("GITLAB_INSTANCE") or urllib.parse.urlsplit(url).hostname
    interval, days = int(os.environ.get("INTERVAL_SECONDS", "300")), int(os.environ.get("MERGE_DAYS", "30"))
    threading.Thread(target=serve, args=(int(os.environ.get("PORT", "9169")),), daemon=True).start()
    while True:
        started = time.monotonic()
        try:
            State.text = collect(api, instance, days)
        except (OSError, ValueError) as error:
            # the last good collection stays served; its timestamp shows the age
            print(f"collection failed: {error}", flush=True)
        time.sleep(max(30, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
