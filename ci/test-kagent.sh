#!/bin/bash
# Render assertions for charts/kagent: the tool server of the upstream example agents
set -u
cd "$(dirname "$0")/.."
C=charts/kagent
fail=0
render() { helm template kagent "$C" --namespace test -f "$C/ci/test-values.yaml" "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }

# An example agent without any kagent-tool-server is refused
OUT=$(render --set upstream.k8s-agent.enabled=true)
has no-server "needs examples.enabled or a remoteMCPServers entry kagent-tool-server"

# The chart's own example tool server
OUT=$(render --set upstream.k8s-agent.enabled=true --set examples.enabled=true)
has examples "name: kagent-example-tools"
has examples "url: http://kagent-example-tools.test:8084/mcp"

# A remoteMCPServers entry kagent-tool-server replaces it: no example tool server
OUT=$(render --set upstream.k8s-agent.enabled=true \
  --set 'remoteMCPServers[0].name=kagent-tool-server' \
  --set 'remoteMCPServers[0].description=team tools' \
  --set 'remoteMCPServers[0].url=http://kagent-tools.team:8084/mcp')
hasnt remote "kagent-example-tools"
has remote "name: kagent-tool-server"
has remote 'url: "http://kagent-tools.team:8084/mcp"'

# Both at once would make two RemoteMCPServers of one name
OUT=$(render --set examples.enabled=true \
  --set 'remoteMCPServers[0].name=kagent-tool-server' \
  --set 'remoteMCPServers[0].description=team tools' \
  --set 'remoteMCPServers[0].url=http://kagent-tools.team:8084/mcp')
has both "choose one"

[ $fail = 0 ] && echo "OK kagent"
exit $fail
