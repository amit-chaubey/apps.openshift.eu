#!/usr/bin/env python3
"""Show the icon of an installed MCP server's settings on its card.

Agent Canvas only draws a logo for an installed server whose URL equals a Library
entry, and its settings mapper drops the server's icon. Servers behind the token
proxy never match. This keeps the icon in the mapper and lets the card fall back to
it. Fails the build when the frontend no longer has the expected shape.
"""
import glob
import re
import sys

ASSETS = sys.argv[1] if len(sys.argv) > 1 else "/opt/agent-canvas/frontend/assets"


def patch(pattern: str, fix, want: int) -> None:
    files = glob.glob(f"{ASSETS}/{pattern}")
    if len(files) != 1:
        sys.exit(f"patch-mcp-icons: {pattern}: {len(files)} files")
    src = open(files[0]).read()
    out, count = fix(src)
    if count != want:
        sys.exit(f"patch-mcp-icons: {files[0]}: {count} changes, expected {want}")
    open(files[0], "w").write(out)
    print(f"patch-mcp-icons: {files[0]}: {count} changes")


def mapper(src: str):
    # ([id,config])=>...{id:id,type:...,name:id,...}: add icon:config.icon to both shapes
    m = re.search(r"Object\.entries\(\w+\)\.map\(\(\[(\w+),(\w+)\]\)=>", src)
    if not m:
        return src, 0
    key, conf = m.groups()
    return re.subn(rf"name:{key},", f"name:{key},icon:{conf}.icon??void 0,", src)


def card(src: str):
    # the installed card: entry is the matched Library entry, else the server's icon
    m = re.search(r"function \w+\(\{server:(\w+),onEdit:\w+,onToggleEnabled", src)
    if not m:
        return src, 0
    srv = m.group(1)
    return re.subn(r"(\(0,\w+\.jsx\)\(\w+,\{entry:)(\w+)(,fallback:)",
                   rf"\1\2??({srv}.icon?{{name:{srv}.name,logoUrl:{srv}.icon}}:void 0)\3",
                   src, count=1)


patch("mcp-installed-servers-*.js", mapper, 2)
patch("mcp-[A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-].js", card, 1)
