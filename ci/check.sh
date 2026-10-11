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
  helm lint "$c" >/dev/null || { echo "FAIL $n: helm lint"; fail=1; }
  for mode in kubernetes externalSecret; do
    args="--set secrets.type=$mode"
    [ "$mode" = externalSecret ] && args="$args --set secrets.externalSecret.storeName=vault-test"
    out=$(helm template test "$c" --namespace test $args) || { echo "FAIL $n: render $mode"; fail=1; continue; }
    bad=$(echo "$out" | grep -E '^kind: (Namespace|ClusterRole|ClusterRoleBinding|ClusterPolicy|CustomResourceDefinition|Subscription|OperatorGroup|SecurityContextConstraints)$' || true)
    [ -z "$bad" ] || { echo "FAIL $n: forbidden kind in $mode render: $bad"; fail=1; }
    if echo "$out" | grep -q 'system:openshift:scc:'; then echo "FAIL $n: SCC binding"; fail=1; fi
    lab=$(echo "$out" | python3 ci/check_labels.py "$n") || { echo "FAIL $n: labels in $mode render:"; echo "$lab" | sed 's/^/  /'; fail=1; }
  done
  id=$(grep -rniE 'grncloud|grn\.cloud|hoolia|berry|146\.19\.|openshift\.eu|panther' "$c/templates" "$c/values.yaml" || true)
  [ -z "$id" ] || { echo "FAIL $n: platform identity: $id"; fail=1; }
  for f in description icon home; do
    grep -q "^$f:" "$c/Chart.yaml" || { echo "FAIL $n: Chart.yaml lacks $f"; fail=1; }
  done
  grep -q 'charts.openshift.io/name' "$c/Chart.yaml" || { echo "FAIL $n: Chart.yaml lacks charts.openshift.io/name"; fail=1; }
  for s in '## Purpose' '## Install' '## Delivery instructions'; do
    grep -q "^$s" "$c/README.md" 2>/dev/null || { echo "FAIL $n: README lacks $s"; fail=1; }
  done
  [ -f "$c/values.schema.json" ] || { echo "FAIL $n: no values.schema.json"; fail=1; }
done
if command -v gitleaks >/dev/null; then
  gitleaks detect --source . --no-git --redact >/dev/null || { echo "FAIL gitleaks"; fail=1; }
else
  echo "SKIP gitleaks not installed locally"
fi
[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
