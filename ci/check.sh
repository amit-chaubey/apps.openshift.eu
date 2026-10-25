#!/bin/bash
# Release gate for every chart: lint, render, tenant-safe kinds, no identity, no secrets
set -eu
cd "$(dirname "$0")/.."
ls charts/*/Chart.yaml >/dev/null 2>&1 || { echo "FAIL no chart under charts/"; exit 1; }
fail=0
for c in charts/*/; do
  c=${c%/}
  n=$(basename "$c")
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    # helm builds only from repositories it knows by name
    for r in $(grep -E '^\s+repository: https://' "$c/Chart.yaml" | awk '{print $2}'); do
      helm repo add "dep-$(echo "$r" | cksum | cut -d' ' -f1)" "$r" >/dev/null
    done
    helm dependency build "$c" >/dev/null || { echo "FAIL $n: helm dependency build"; fail=1; continue; }
  fi
  # values a chart needs to render at all, as in chart-testing
  ci=""
  [ -f "$c/ci/test-values.yaml" ] && ci="-f $c/ci/test-values.yaml"
  helm lint "$c" --namespace test $ci >/dev/null || { echo "FAIL $n: helm lint"; fail=1; }
  for mode in kubernetes externalSecret; do
    args="$ci --set secrets.type=$mode"
    [ "$mode" = externalSecret ] && args="$args --set secrets.externalSecret.storeName=vault-test"
    out=$(helm template test "$c" --namespace test $args) || { echo "FAIL $n: render $mode"; fail=1; continue; }
    bad=$(echo "$out" | grep -E '^kind: (Namespace|ClusterRole|ClusterRoleBinding|ClusterPolicy|CustomResourceDefinition|Subscription|OperatorGroup|SecurityContextConstraints)$' || true)
    [ -z "$bad" ] || { echo "FAIL $n: forbidden kind in $mode render: $bad"; fail=1; }
    if echo "$out" | grep -q 'system:openshift:scc:'; then echo "FAIL $n: SCC binding"; fail=1; fi
    lab=$(echo "$out" | python3 ci/check_labels.py "$n") || { echo "FAIL $n: labels in $mode render:"; echo "$lab" | sed 's/^/  /'; fail=1; }
  done
  # a public upstream source archive is provenance, not platform identity
  id=$(grep -rniE 'grncloud|grn\.cloud|hoolia|berry|146\.19\.|openshift\.eu|panther' "$c/templates" "$c/values.yaml" |
    grep -vE '^[^:]+:[0-9]+:\s*(package: opencode-a2a @ https://github\.com/hoolia/opencode-a2a/archive/|version: [0-9.]+\+hoolia\.[0-9]+$)' ||
    true)
  [ -z "$id" ] || { echo "FAIL $n: platform identity: $id"; fail=1; }
  for f in description icon home; do
    grep -q "^$f:" "$c/Chart.yaml" || { echo "FAIL $n: Chart.yaml lacks $f"; fail=1; }
  done
  grep -q 'charts.openshift.io/name' "$c/Chart.yaml" || { echo "FAIL $n: Chart.yaml lacks charts.openshift.io/name"; fail=1; }
  for s in '## Purpose' '## Install' '## Delivery instructions'; do
    grep -q "^$s" "$c/README.md" 2>/dev/null || { echo "FAIL $n: README lacks $s"; fail=1; }
  done
  [ -f "$c/values.schema.json" ] || { echo "FAIL $n: no values.schema.json"; fail=1; }
  v=$(awk '/^version:/{print $2}' "$c/Chart.yaml")
  [ -s "release-notes/$n/$v.md" ] || { echo "FAIL $n: no release-notes/$n/$v.md"; fail=1; }
done
# the charts carry a copy of the agent-identity proxy until they use the injected sidecar
for f in charts/*/files/token-proxy.py; do
  cmp -s "$f" images/agent-identity/token-proxy.py || { echo "FAIL $f differs from images/agent-identity/token-proxy.py"; fail=1; }
done
if command -v gitleaks >/dev/null; then
  gitleaks detect --source . --no-git --redact >/dev/null || { echo "FAIL gitleaks"; fail=1; }
else
  echo "SKIP gitleaks not installed locally"
fi
[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
