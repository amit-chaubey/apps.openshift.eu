#!/usr/bin/env python3
"""Fail when an object from a chart's own templates lacks a required label, or a
workload lacks a console icon.

Usage: helm template test <chart> ... | check_labels.py <chart name>
Objects of subcharts (# Source: <chart>/charts/...) are not checked.
"""
import re
import sys

import yaml

REQUIRED = [
    "app",
    "app.kubernetes.io/name",
    "app.kubernetes.io/component",
    "app.kubernetes.io/part-of",
    "app.kubernetes.io/version",
]

WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet"}


def main() -> int:
    chart = sys.argv[1]
    own = re.compile(rf"^# Source: {re.escape(chart)}/templates/", re.M)
    bad = 0
    for chunk in re.split(r"^---\s*$", sys.stdin.read(), flags=re.M):
        if not own.search(chunk):
            continue
        for doc in yaml.safe_load_all(chunk):
            if not isinstance(doc, dict) or not doc.get("kind"):
                continue
            labels = (doc.get("metadata") or {}).get("labels") or {}
            annotations = (doc.get("metadata") or {}).get("annotations") or {}
            missing = [key for key in REQUIRED if key not in labels]
            if doc["kind"] in WORKLOADS and not (
                "app.openshift.io/runtime" in labels
                or annotations.get("app.openshift.io/custom-icon")
            ):
                missing.append("app.openshift.io/runtime or app.openshift.io/custom-icon")
            if missing:
                bad += 1
                print(f"{doc['kind']}/{doc['metadata'].get('name')}: missing {', '.join(missing)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
