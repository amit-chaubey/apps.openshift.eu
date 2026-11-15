#!/bin/bash
# Alert rules of the chatwoot Odoo bridge: promtool unit tests over the rendered PrometheusRule.
# Needs helm, python3 with PyYAML and promtool on the PATH.
set -eu
cd "$(dirname "$0")/.."
command -v promtool >/dev/null || { echo "FAIL promtool not found"; exit 1; }
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
helm template t charts/chatwoot --namespace test --api-versions security.openshift.io/v1 \
  --api-versions route.openshift.io/v1 --set odoo.enabled=true --set odoo.url=https://odoo.example.org \
  --set route.hostname=support.example.org --set secrets.odooApiKey=k --set monitoring.enabled=true |
  python3 -c '
import sys, yaml
rule = next(d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind") == "PrometheusRule")
yaml.safe_dump({"groups": rule["spec"]["groups"]}, sys.stdout)' > "$T/rules.yaml"

cat > "$T/test.yaml" <<'EOF'
rule_files: [rules.yaml]
evaluation_interval: 1m
tests:
# a lost reply alerts: the counter exists at 0 from the start
- interval: 1m
  input_series:
  - series: 'chatwoot_odoo_bridge_webhooks_total{namespace="test",result="given_up"}'
    values: '0x10 1x60'
  - series: 'chatwoot_odoo_bridge_webhooks_total{namespace="test",result="dropped"}'
    values: '0x70'
  - series: 'up{namespace="test",service="chatwoot-odoo-bridge"}'
    values: '1x70'
  alert_rule_test:
  - eval_time: 20m
    alertname: ChatwootOdooBridgeReplyLost
    exp_alerts:
    - exp_labels: {severity: warning, namespace: test, result: given_up}
      exp_annotations:
        summary: A staff reply did not reach Odoo
        description: The bridge gave up a webhook after its retries; the conversation has a private note asking staff to send it again.
  - eval_time: 20m
    alertname: ChatwootOdooBridgeDown
# a pod that is gone leaves no up series
- interval: 1m
  input_series:
  - series: 'up{namespace="test",service="chatwoot-odoo-bridge"}'
    values: '1x10 _x30'
  alert_rule_test:
  - eval_time: 30m
    alertname: ChatwootOdooBridgeDown
    exp_alerts:
    - exp_labels: {severity: critical, namespace: test, service: chatwoot-odoo-bridge}
      exp_annotations:
        summary: The Chatwoot Odoo bridge is unreachable
        description: No metrics for 5m. Staff replies are not sent to customers and Odoo events wait in the Odoo outbox.
# a pod that does not answer the scrape
- interval: 1m
  input_series:
  - series: 'up{namespace="test",service="chatwoot-odoo-bridge",pod="b"}'
    values: '0x20'
  alert_rule_test:
  - eval_time: 10m
    alertname: ChatwootOdooBridgeDown
    exp_alerts:
    - exp_labels: {severity: critical, namespace: test, service: chatwoot-odoo-bridge, pod: b}
      exp_annotations:
        summary: The Chatwoot Odoo bridge is unreachable
        description: No metrics for 5m. Staff replies are not sent to customers and Odoo events wait in the Odoo outbox.
# one event that keeps failing, retried an hour apart, alerts; a single failure does not
- interval: 1m
  input_series:
  - series: 'chatwoot_odoo_bridge_events_total{namespace="test",result="error"}'
    values: '0x30 1x60 2x60 3x60'
  alert_rule_test:
  - eval_time: 120m
    alertname: ChatwootOdooBridgeEventsFailing
  - eval_time: 200m
    alertname: ChatwootOdooBridgeEventsFailing
    exp_alerts:
    - exp_labels: {severity: warning, namespace: test, result: error}
      exp_annotations:
        summary: Odoo events keep failing in the Chatwoot bridge
        description: More than two Odoo event deliveries failed in 4 hours, from one event or several; they wait in the Odoo outbox, which retries up to 8 times over about 4 hours. The bridge log names the error.
# a healthy bridge alerts on nothing
- interval: 1m
  input_series:
  - series: 'up{namespace="test",service="chatwoot-odoo-bridge"}'
    values: '1x300'
  - series: 'chatwoot_odoo_bridge_events_total{namespace="test",result="error"}'
    values: '0x30 1x270'
  - series: 'chatwoot_odoo_bridge_webhooks_total{namespace="test",result="given_up"}'
    values: '0x300'
  alert_rule_test:
  - {eval_time: 280m, alertname: ChatwootOdooBridgeDown}
  - {eval_time: 280m, alertname: ChatwootOdooBridgeReplyLost}
  - {eval_time: 280m, alertname: ChatwootOdooBridgeEventsFailing}
EOF
if (cd "$T" && promtool test rules test.yaml); then echo "PASS charts/chatwoot alerts"; else echo "FAIL charts/chatwoot alerts"; exit 1; fi
