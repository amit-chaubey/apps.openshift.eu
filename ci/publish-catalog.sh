#!/bin/bash
# Publishes catalog.json, built from Chart.yaml on main, next to index.yaml on gh-pages.
# The release workflow runs it after the releases, and so can a person.
# usage: ci/publish-catalog.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git checkout -q main && git pull -q --ff-only origin main
[ -z "$(git status --porcelain)" ] || { echo "working tree is not clean"; exit 1; }
P=$(mktemp -d)
python3 ci/catalog.py "$P/catalog.json"
git checkout -q gh-pages && git pull -q --ff-only origin gh-pages
if [ -f catalog.json ] && cmp -s catalog.json "$P/catalog.json"; then
  echo "catalog.json unchanged"
else
  cp "$P/catalog.json" catalog.json
  git add catalog.json && git commit -q -m "Update catalog.json" && git push -q origin gh-pages
  echo "catalog.json published"
fi
git checkout -q main
rm -rf "$P"
