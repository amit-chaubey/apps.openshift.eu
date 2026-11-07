#!/usr/bin/env python3
"""Show the icon of an installed MCP server's settings on its card.

Agent Canvas only draws a logo for an installed server whose URL equals a Library
entry, and its settings mapper drops the server's icon. Servers behind the token
proxy never match. This keeps the icon in the mapper and lets the card fall back to
it. Fails the build when the frontend no longer has the expected shape.

The server marks /assets/ immutable, so a browser that cached an upstream bundle
keeps it under its upstream name. Every patched bundle, and every bundle or page
that names one of them, gets a new name and all references follow.
"""
import base64
import glob
import hashlib
import os
import re
import sys

ASSETS = sys.argv[1] if len(sys.argv) > 1 else "/opt/agent-canvas/frontend/assets"
PATCHED: list[str] = []


def patch(pattern: str, fix, want: int) -> None:
    files = glob.glob(f"{ASSETS}/{pattern}")
    if len(files) != 1:
        sys.exit(f"patch-mcp-icons: {pattern}: {len(files)} files")
    src = open(files[0]).read()
    out, count = fix(src)
    if count != want:
        sys.exit(f"patch-mcp-icons: {files[0]}: {count} changes, expected {want}")
    open(files[0], "w").write(out)
    PATCHED.append(files[0])
    print(f"patch-mcp-icons: {files[0]}: {count} changes")


def ref(name: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9_~.-]){re.escape(name)}(?![A-Za-z0-9_~-])")


def rename(patched: list[str]) -> None:
    """New names for the patched bundles and, transitively, every asset naming one."""
    pages = sorted(glob.glob(f"{ASSETS}/*.js") + glob.glob(f"{ASSETS}/*.css") +
                   glob.glob(f"{os.path.dirname(ASSETS)}/*.html"))
    text = {p: open(p, encoding="utf-8").read() for p in pages}
    seed = hashlib.sha256("".join(text[p] for p in sorted(patched)).encode()).digest()
    moved = set(patched)
    while True:
        names = [os.path.basename(p) for p in moved]
        more = {p for p in pages if p not in moved and p.startswith(ASSETS + "/")
                and any(ref(n).search(text[p]) for n in names)}
        if not more:
            break
        moved |= more
    new = {}
    for p in sorted(moved):
        base = os.path.basename(p)
        stem, ext = os.path.splitext(base)
        tag = base64.urlsafe_b64encode(hashlib.sha256(seed + base.encode()).digest())[:8].decode()
        new[base] = re.sub(r"-[A-Za-z0-9_-]{8}$", "", stem) + "-" + tag + ext
    for p in pages:
        out = text[p]
        for old, name in new.items():
            out = ref(old).sub(name, out)
        if out != text[p]:
            open(p, "w", encoding="utf-8").write(out)
    for p in sorted(moved):
        os.rename(p, os.path.join(ASSETS, new[os.path.basename(p)]))
    final = glob.glob(f"{ASSETS}/*") + glob.glob(f"{os.path.dirname(ASSETS)}/*.html")
    left = {n for f in final if f.endswith((".js", ".css", ".html"))
            for n in new if ref(n).search(open(f, encoding="utf-8").read())}
    if left:
        sys.exit(f"patch-mcp-icons: old names still referenced: {sorted(left)}")
    print(f"patch-mcp-icons: {len(new)} assets renamed")


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


if __name__ == "__main__":
    patch("mcp-installed-servers-*.js", mapper, 2)
    patch("mcp-[A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-][A-Za-z0-9_-].js", card, 1)
    rename(PATCHED)
