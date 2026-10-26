"""Pages of the documentation site generated from the charts.

Used by mkdocs.yml as a hook: every `mkdocs build` and `mkdocs serve` reads
charts/*/Chart.yaml, README.md, values.yaml, values.schema.json and
release-notes/<chart>/*.md and adds the pages below in memory, so the site
never lags behind the charts and nothing generated is committed.

    charts/index.md                 chart overview
    charts/<chart>/index.md         README with chart facts
    charts/<chart>/values.md        values reference
    charts/<chart>/changelog.md     release notes, newest first
    mcp-catalog.md                  MCP servers by category and bundle

Run it directly to write the same pages into a directory for inspection:

    python3 ci/docs_gen.py /tmp/generated
"""

import json
import os
import re
import sys

import yaml

REPO_URL = "https://github.com/hoolia/apps.openshift.eu"
LABEL_DOMAIN = "mcp.openshift.eu"
NAV_SECTION = "Charts"

KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_./-]+|\"[^\"]+\"|'[^']+'):(\s|$)")


def root_of(config_file):
    return os.path.dirname(os.path.abspath(config_file))


def load_yaml(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or default
    except FileNotFoundError:
        return default


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def version_key(v):
    parts = re.split(r"[.+-]", v)
    return [int(p) if p.isdigit() else -1 for p in parts[:3]] + [v]


def charts(root):
    base = os.path.join(root, "charts")
    out = []
    for name in sorted(os.listdir(base)):
        meta = load_yaml(os.path.join(base, name, "Chart.yaml"))
        if not meta:
            continue
        meta["_dir"] = os.path.join(base, name)
        meta["_name"] = name
        out.append(meta)
    return out


def annotation(meta, key):
    return (meta.get("annotations") or {}).get(f"{LABEL_DOMAIN}/{key}", "")


def kind_of(meta):
    words = set(meta.get("keywords") or [])
    if annotation(meta, "server") or "mcp-server" in words:
        return "MCP server"
    if "mcp-bundle" in words or (annotation(meta, "bundle") and not annotation(meta, "server")):
        return "MCP bundle"
    if "mcp" in words and "mcp" in meta["_name"].split("-"):
        return "MCP server"
    return "Application"


def title_of(meta):
    return (meta.get("annotations") or {}).get("charts.openshift.io/name") or meta["_name"]


def cell(text):
    text = " ".join(str(text).split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("|", "\\|")


# values reference

def schema_node(schema, path):
    node = schema or {}
    for key in path:
        node = (node.get("properties") or {}).get(key)
        if node is None:
            return {}
    return node


def lookup(data, path):
    node = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(key)
        node = node[key]
    return node


def type_of(value, node):
    t = node.get("type")
    if isinstance(t, list):
        return " or ".join(t)
    if t:
        return t
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def default_of(value):
    if value is None:
        return "`null`"
    if isinstance(value, str):
        text = value if value != "" else '""'
    else:
        text = json.dumps(value, separators=(", ", ": "))
    if len(text) > 80:
        text = text[:77] + "..."
    return "`" + text.replace("`", "'") + "`"


def values_rows(values_text, data, schema):
    """One row per key of values.yaml: path, type, default, description.

    The description is the comment block right above the key; values.schema.json
    adds the type, an enum and a description where it has one.
    """
    rows = []
    stack = []
    comments = []
    for line in values_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            comments.append(stripped.lstrip("#").strip())
            continue
        if not stripped:
            comments = []
            continue
        m = KEY_RE.match(line)
        if not m or stripped.startswith("-"):
            comments = []
            continue
        indent = len(m.group("indent"))
        key = m.group("key").strip("\"'")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = [k for _, k in stack] + [key]
        stack.append((indent, key))
        try:
            value = lookup(data, path)
        except KeyError:
            comments = []
            continue
        node = schema_node(schema, path)
        text = " ".join(c for c in comments if c)
        if not text and node.get("description"):
            text = node["description"]
        if node.get("enum"):
            text = (text + " " if text else "") + "One of: " + ", ".join(
                f"`{e}`" for e in node["enum"]) + "."
        if node.get("deprecated"):
            text = "Deprecated. " + text
        nested = isinstance(value, dict) and value
        if not nested or text:
            rows.append((".".join(path), type_of(value, node),
                         "" if nested else default_of(value), text))
        comments = []
    return rows


def values_page(meta):
    name = meta["_name"]
    text = read(os.path.join(meta["_dir"], "values.yaml"))
    data = yaml.safe_load(text) or {}
    schema = load_yaml(os.path.join(meta["_dir"], "values.schema.json"), {})
    lines = [
        f"# {name} values",
        "",
        f"Every value of chart `{name}` {meta.get('version')}, read from "
        f"[`values.yaml`]({REPO_URL}/blob/main/charts/{name}/values.yaml)"
        + (f" and [`values.schema.json`]({REPO_URL}/blob/main/charts/{name}/values.schema.json)"
           if schema else "")
        + ". A map is listed with its keys; a map without a description of its own is "
        "listed by its keys only. Every value has a default: the chart installs with none given.",
        "",
        "| Value | Type | Default | Description |",
        "|---|---|---|---|",
    ]
    for path, typ, default, desc in values_rows(text, data, schema):
        lines.append(f"| `{path}` | {typ} | {default} | {cell(desc)} |")
    return "\n".join(lines) + "\n"


# README and changelog

LINK_RE = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)(\))")


def absolutize(markdown, chart):
    """Point relative links of a README at the file on GitHub."""
    def fix(m):
        target = m.group(2)
        if re.match(r"^([a-z]+:|#|/)", target):
            return m.group(0)
        kind = "raw" if m.group(1).startswith("!") else "blob"
        return f"{m.group(1)}{REPO_URL}/{kind}/main/charts/{chart}/{target}{m.group(3)}"
    return LINK_RE.sub(fix, markdown)


def readme_page(meta):
    name = meta["_name"]
    body = read(os.path.join(meta["_dir"], "README.md")).strip()
    body = re.sub(r"\A#\s+.*\n", "", body).strip()
    body = absolutize(body, name)
    facts = [
        f"# {title_of(meta)}",
        "",
        f"{meta.get('description', '')}",
        "",
        "| | |",
        "|---|---|",
        f"| Chart | `{name}` |",
        f"| Version | {meta.get('version')} |",
        f"| Application version | {meta.get('appVersion', '')} |",
        f"| Kind | {kind_of(meta)} |",
    ]
    for key, label in (("category", "MCP category"), ("bundle", "MCP bundle"),
                       ("server", "MCP server name"), ("auth", "MCP login")):
        if annotation(meta, key):
            facts.append(f"| {label} | `{annotation(meta, key)}` |")
    facts.append(f"| Source | [charts/{name}]({REPO_URL}/tree/main/charts/{name}) |")
    facts += [
        "",
        f"The values are listed under [Values](values.md), the changes per version under "
        f"[Changelog](changelog.md).",
        "",
        body,
        "",
    ]
    return "\n".join(facts)


def changelog_page(root, meta):
    name = meta["_name"]
    folder = os.path.join(root, "release-notes", name)
    versions = []
    if os.path.isdir(folder):
        versions = sorted((f[:-3] for f in os.listdir(folder) if f.endswith(".md")),
                          key=version_key, reverse=True)
    lines = [f"# {name} changelog", ""]
    if not versions:
        lines += ["This chart has no release notes.", ""]
    for v in versions:
        notes = read(os.path.join(folder, v + ".md")).strip()
        notes = re.sub(r"\A#\s+.*\n", "", notes).strip()
        notes = re.sub(r"^(#{1,5}) ", lambda m: "#" + m.group(1) + " ", notes, flags=re.M)
        lines += [f"## {v}", "", notes, ""]
    return "\n".join(lines)


# overview and catalog

def charts_index(all_charts):
    lines = [
        "# Charts",
        "",
        "Every chart of the repository, generated from its `Chart.yaml`. Add the repository once:",
        "",
        "```bash",
        "helm repo add grncloud https://apps.openshift.eu",
        "```",
        "",
    ]
    for kind in ("Application", "MCP server", "MCP bundle"):
        group = [c for c in all_charts if kind_of(c) == kind]
        if not group:
            continue
        lines += [f"## {kind}s", "", "| Chart | Version | Description |", "|---|---|---|"]
        for c in group:
            lines.append(f"| [{title_of(c)}]({c['_name']}/index.md) (`{c['_name']}`) "
                         f"| {c.get('version')} | {cell(c.get('description', ''))} |")
        lines.append("")
    return "\n".join(lines)


def catalog_page(all_charts):
    servers = [c for c in all_charts if kind_of(c) == "MCP server"]
    bundles = [c for c in all_charts if kind_of(c) == "MCP bundle"]
    lines = [
        "# MCP catalog",
        "",
        "MCP server charts grouped by category and by bundle, generated from the "
        f"`Chart.yaml` annotations `{LABEL_DOMAIN}/server`, `/category`, `/bundle` and "
        "`/auth`. An agent selects add-on servers by these labels: see "
        "[MCP catalog labels](conventions/mcp-catalog.md).",
        "",
        "## By category",
        "",
    ]
    by_cat = {}
    for c in servers:
        by_cat.setdefault(annotation(c, "category") or "without catalog labels", []).append(c)
    for cat in sorted(by_cat, key=lambda k: (k == "without catalog labels", k)):
        lines += [f"### {cat}", "", "| Server | Chart | Bundle | Login | Description |",
                  "|---|---|---|---|---|"]
        for c in by_cat[cat]:
            server = annotation(c, "server") or "-"
            bundle = annotation(c, "bundle") or "-"
            auth = annotation(c, "auth") or "-"
            lines.append(f"| `{server}` | [{c['_name']}](charts/{c['_name']}/index.md) "
                         f"| {bundle} | {auth} | {cell(c.get('description', ''))} |")
        lines.append("")
    lines += ["## By bundle", ""]
    names = sorted({annotation(c, "bundle") for c in servers + bundles if annotation(c, "bundle")})
    if not names:
        lines += ["No chart names a bundle.", ""]
    for b in names:
        charts_of = [c for c in bundles if annotation(c, "bundle") == b]
        members = [c for c in servers if annotation(c, "bundle") == b]
        lines += [f"### {b}", ""]
        for c in charts_of:
            lines.append(f"- Bundle chart: [{c['_name']}](charts/{c['_name']}/index.md), "
                         f"{cell(c.get('description', ''))}")
        for c in members:
            server = annotation(c, "server") or c["_name"]
            lines.append(f"- Server `{server}`: [{c['_name']}](charts/{c['_name']}/index.md)")
        lines.append("")
    return "\n".join(lines)


def pages(root):
    all_charts = charts(root)
    out = {"charts/index.md": charts_index(all_charts), "mcp-catalog.md": catalog_page(all_charts)}
    for c in all_charts:
        n = c["_name"]
        out[f"charts/{n}/index.md"] = readme_page(c)
        out[f"charts/{n}/values.md"] = values_page(c)
        out[f"charts/{n}/changelog.md"] = changelog_page(root, c)
    return out, all_charts


def nav(all_charts):
    entries = [{"Overview": "charts/index.md"}]
    for c in all_charts:
        n = c["_name"]
        entries.append({title_of(c): [
            f"charts/{n}/index.md",
            {"Values": f"charts/{n}/values.md"},
            {"Changelog": f"charts/{n}/changelog.md"},
        ]})
    return entries


# MkDocs hooks

_state = {}


def on_config(config, **kwargs):
    generated, all_charts = pages(root_of(config.config_file_path))
    _state["pages"] = generated
    for item in config.nav or []:
        if isinstance(item, dict) and NAV_SECTION in item:
            item[NAV_SECTION] = nav(all_charts)
    return config


def on_files(files, config, **kwargs):
    from mkdocs.structure.files import File
    for path, content in _state["pages"].items():
        f = File.generated(config, path, content=content)
        chart = path.split("/")[1] if path.startswith("charts/") and path.count("/") == 2 else None
        if chart and path.endswith("/index.md"):
            f.edit_uri = f"../charts/{chart}/README.md"
        else:
            f.edit_uri = None
        files.append(f)
    return files


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: ci/docs_gen.py <output directory>")
    generated, _ = pages(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for path, content in generated.items():
        target = os.path.join(sys.argv[1], path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    print(f"{len(generated)} pages in {sys.argv[1]}")
