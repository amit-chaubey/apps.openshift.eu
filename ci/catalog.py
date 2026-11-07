#!/usr/bin/env python3
"""catalog.json of the chart website: type, category, vendor, bundle and server per chart.

Read from Chart.yaml on main, so a catalog annotation shows on apps.openshift.eu without a
chart release. The website merges it with index.yaml, which lists the released charts.

  type      mcp-server when the annotation mcp.openshift.eu/server or the keyword
            mcp-server is set; mcp-bundle for mcp-bundle-* and mcp-all, or the keyword
            mcp-bundle; app for every other chart
  category  apps.openshift.eu/category, else mcp.openshift.eu/category
  vendor    apps.openshift.eu/vendor: the organisation behind the software

A library chart (type: library) is part of other charts and is left out.

usage: ci/catalog.py [--check] [output file]
  --check  fail when a chart lacks a vendor or a category, writes nothing
"""
import json
import pathlib
import re
import sys

import yaml

CHARTS = pathlib.Path(__file__).resolve().parent.parent / "charts"
CATEGORY = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def kind_of(chart: dict) -> str:
    annotations = chart.get("annotations") or {}
    keywords = chart.get("keywords") or []
    name = chart["name"]
    if annotations.get("mcp.openshift.eu/server") or "mcp-server" in keywords:
        return "mcp-server"
    if name.startswith("mcp-bundle-") or name == "mcp-all" or "mcp-bundle" in keywords:
        return "mcp-bundle"
    return "app"


def entry(chart_dir: pathlib.Path) -> dict:
    chart = yaml.safe_load((chart_dir / "Chart.yaml").read_text())
    annotations = chart.get("annotations") or {}
    doc = {
        "type": kind_of(chart),
        "category": str(annotations.get("apps.openshift.eu/category")
                        or annotations.get("mcp.openshift.eu/category") or ""),
        "vendor": str(annotations.get("apps.openshift.eu/vendor") or ""),
    }
    for key in ("bundle", "server"):
        if annotations.get(f"mcp.openshift.eu/{key}"):
            doc[key] = str(annotations[f"mcp.openshift.eu/{key}"])
    return doc


def is_library(chart_dir: pathlib.Path) -> bool:
    return (yaml.safe_load((chart_dir / "Chart.yaml").read_text()) or {}).get("type") == "library"


def catalog(charts: pathlib.Path = CHARTS) -> dict:
    return {d.name: entry(d) for d in sorted(charts.iterdir())
            if (d / "Chart.yaml").is_file() and not is_library(d)}


def problems(cat: dict) -> list:
    out = []
    for name, doc in cat.items():
        if not doc["vendor"]:
            out.append(f"{name}: Chart.yaml lacks annotation apps.openshift.eu/vendor")
        if not doc["category"]:
            out.append(f"{name}: Chart.yaml lacks annotation apps.openshift.eu/category")
        elif not CATEGORY.match(doc["category"]):
            out.append(f"{name}: category {doc['category']!r} is not lowercase-with-hyphens")
    return out


def main(argv: list) -> int:
    cat = catalog()
    if "--check" in argv:
        found = problems(cat)
        for p in found:
            print(f"FAIL {p}")
        return 1 if found else 0
    text = json.dumps({"charts": cat}, indent=1, sort_keys=True) + "\n"
    args = [a for a in argv if not a.startswith("--")]
    if args:
        pathlib.Path(args[0]).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
