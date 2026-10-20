# mcp-server

## Purpose

Runs one MCP server from any container image that serves streamable HTTP, for one team in your project, with its upstream credential taken from an existing Secret. It is meant to sit behind an MCP gateway, not on a public Route. Examples: `grafana/mcp-grafana`, `mcp-atlassian`, `mcp-server-odoo`.

Choices:

- Nothing renders until `image.repository` and `image.tag` are set; pin the tag.
- The Service port carries `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` (`path`) and `agentgateway.dev/mcp-target-name` (default: the release name), so an agentgateway backend with a label selector discovers it and prefixes its tools with the target name.
- `envFromSecret` names a Secret you create (for example from an ExternalSecret); its keys become environment variables. The chart never writes a credential.
- No ServiceAccount token in the pod; non-root, no capabilities, `RuntimeDefault` seccomp.
- A NetworkPolicy admits callers from the own project and `networkPolicy.ingressNamespaces` only. Egress goes to cluster DNS and the public internet; a private upstream needs an entry in `networkPolicy.egress.allow`.
- The default port 8000 is outside the ports a typical project default opens to everyone (80, 443, 8080, 8443).

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
oc create secret generic grafana-mcp --from-literal=GRAFANA_URL=https://grafana.example.com \
  --from-literal=GRAFANA_SERVICE_ACCOUNT_TOKEN=<token>
helm install grafana grncloud/mcp-server \
  --set image.repository=docker.io/grafana/mcp-grafana --set image.tag=2.0.2 \
  --set 'args={-t,streamable-http,--address,0.0.0.0:8000,--allowed-hosts,*,--disable-write}' \
  --set envFromSecret=grafana-mcp
```

## Delivery instructions

- Check the pod is Ready: `oc get pods -l app.kubernetes.io/instance=grafana`.
- Through your MCP gateway the tools appear as `<release>_<tool>`. From inside the project: `POST http://<release>:8000/mcp` with an MCP `initialize` request.
- The server has whatever rights its credential has: give it a read-only one where the upstream allows it.
