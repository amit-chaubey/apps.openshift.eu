#!/bin/bash
# Releases one chart: GitHub Release with the package, its release notes and its SBOM,
# then index.yaml on gh-pages. The release workflow runs it, and so can a person.
# The index is rebuilt from the packages of all releases, so it always describes what
# a download returns.
# usage: ci/release.sh <chart>
set -euo pipefail
cd "$(dirname "$0")/.."
C=${1:?chart name}
for t in gitleaks syft; do
  command -v $t >/dev/null || { echo "$t is required for a release"; exit 1; }
done
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
git checkout -q main && git pull -q --ff-only origin main
[ -z "$(git status --porcelain)" ] || { echo "working tree is not clean"; exit 1; }
ci/check.sh
V=$(awk '/^version:/{print $2}' "charts/$C/Chart.yaml"); TAG="$C-$V"
N="release-notes/$C/$V.md"
[ -s "$N" ] || { echo "$N is missing"; exit 1; }
P=$(mktemp -d)
if gh release view "$TAG" >/dev/null 2>&1; then
  echo "release $TAG exists"
else
  helm package "charts/$C" -d "$P" >/dev/null
  ci/sbom.py "charts/$C" "$P/$C-$V.cdx.json"
  gh release create "$TAG" "$P/$C-$V.tgz" "$P/$C-$V.cdx.json" --target main --title "$TAG" \
    --notes-file "$N" >/dev/null
  rm -f "$P/$C-$V.tgz" "$P/$C-$V.cdx.json"
  echo "release $TAG created"
fi
for t in $(gh release list --limit 1000 --json tagName --jq '.[].tagName'); do
  mkdir -p "$P/$t"
  gh release download "$t" --pattern '*.tgz' --dir "$P/$t"
  if [ -f "$P/index.yaml" ]; then
    helm repo index "$P/$t" --url "https://github.com/$REPO/releases/download/$t" --merge "$P/index.yaml"
  else
    helm repo index "$P/$t" --url "https://github.com/$REPO/releases/download/$t"
  fi
  mv "$P/$t/index.yaml" "$P/index.yaml"
done
git checkout -q gh-pages && git pull -q --ff-only origin gh-pages
# the generated timestamp changes on every run; compare without it
if [ -f index.yaml ] && diff -q <(grep -v '^generated:' index.yaml) <(grep -v '^generated:' "$P/index.yaml") >/dev/null; then
  echo "index.yaml unchanged"
else
  cp "$P/index.yaml" index.yaml
  git add index.yaml && git commit -q -m "Update index.yaml for $TAG" && git push -q origin gh-pages
  echo "index.yaml published"
fi
git checkout -q main
rm -rf "$P"
