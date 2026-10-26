#!/usr/bin/env python3
"""MCP Registry server.json of one MCP server chart release.

A chart is an MCP server chart when Chart.yaml carries the annotation
mcp.openshift.eu/server. The file follows the server.json format of
github.com/modelcontextprotocol/registry: the hosted endpoint as a remote (url in
values.yaml), or for a chart that runs the server itself, a remote whose host is the
variable route_host. The chart, its Helm repository, labels and OAuth scopes go into
_meta under io.modelcontextprotocol.registry/publisher-provided. Prints nothing and
writes nothing for any other chart.

usage: ci/server_json.py <chart dir> <output file>
"""
import json
import pathlib
import sys

import yaml

SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
NAMESPACE = "eu.openshift.apps"
REPO = "https://github.com/hoolia/apps.openshift.eu"
HELM_REPO = "https://apps.openshift.eu"
DOMAIN = "mcp.openshift.eu"


def short(text: str, limit: int = 100) -> str:
    """The registry allows 100 characters: the first clause, else whole words."""
    first = text.split(";")[0].strip()
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(" ", 1)[0].rstrip(",(")


def server_json(chart_dir: pathlib.Path) -> dict | None:
    chart = yaml.safe_load((chart_dir / "Chart.yaml").read_text())
    annotations = chart.get("annotations") or {}
    server = annotations.get(f"{DOMAIN}/server")
    if not server:
        return None
    values = yaml.safe_load((chart_dir / "values.yaml").read_text()) or {}
    if values.get("url"):
        remote = {"type": "streamable-http", "url": values["url"]}
    else:
        remote = {"type": "streamable-http", "url": "https://{route_host}/mcp",
                  "variables": {"route_host": {
                      "description": "route.host of the chart's release", "isRequired": True}}}
    scopes = values.get("scopes") or []
    meta = {
        "helm": {"repository": HELM_REPO, "chart": chart["name"], "version": chart["version"]},
        "labels": {f"{DOMAIN}/{k}": annotations[f"{DOMAIN}/{k}"]
                   for k in ("server", "bundle", "category") if f"{DOMAIN}/{k}" in annotations},
        "auth": annotations.get(f"{DOMAIN}/auth", "none"),
    }
    if scopes:
        meta["oauthScopes"] = {"read": scopes, "write": values.get("writeScopes") or []}
    doc = {
        "$schema": SCHEMA,
        "name": f"{NAMESPACE}/{chart['name']}",
        "title": annotations.get("charts.openshift.io/name", chart["name"]),
        "description": short(chart["description"]),
        "version": chart["version"],
        "websiteUrl": f"{REPO}/tree/main/charts/{chart['name']}",
        "repository": {"url": REPO, "source": "github", "subfolder": f"charts/{chart['name']}"},
        "remotes": [remote],
        "_meta": {"io.modelcontextprotocol.registry/publisher-provided": meta},
    }
    if chart.get("icon"):
        doc["icons"] = [{"src": chart["icon"]}]
    return doc


def main() -> int:
    doc = server_json(pathlib.Path(sys.argv[1]))
    if doc is None:
        return 0
    pathlib.Path(sys.argv[2]).write_text(json.dumps(doc, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
