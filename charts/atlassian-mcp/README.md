# atlassian-mcp

Jira and Confluence of an Atlassian Cloud site as an MCP server
([sooperset/mcp-atlassian](https://github.com/sooperset/mcp-atlassian)), streamable HTTP on
port 8000, path `/mcp`, for a tenant or one of its teams.

## Purpose

- `credentials.mode: perCaller` (default): the server holds no Atlassian account. Every
  request carries its caller's own account, `Authorization: Basic base64(email:api_token)` or
  a Bearer token; a request without one is refused. Each agent or person therefore needs its
  own Atlassian account or API token: a person decides who gets one.
- `credentials.mode: shared`: one account for every caller, from `credentials.existingSecret`
  (`JIRA_USERNAME`, `JIRA_API_TOKEN`, optionally `CONFLUENCE_*`).
- Behind an MCP gateway the caller's `Authorization` header authenticates it to the gateway.
  The Service annotation `agentgateway.dev/mcp-credential-header` (from
  `credentials.gatewayHeader`, default `X-Atlassian-Authorization`) names the request header
  the gateway copies into the backend's `Authorization`.
- The Service follows the gateway's MCP discovery conventions: port `appProtocol`
  `agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and
  `agentgateway.dev/mcp-target-name` (tools `<targetName>_<tool>`).
- With `team`, every object goes into `<tenant>-<team>`, a namespace the team already has;
  without it, into the release namespace (for example the tenant's home namespace).
- Egress reaches the public internet only, plus DNS. No cluster-scoped objects, no Kubernetes
  API access.

## Install

```bash
helm repo add apps https://apps.openshift.eu
helm install atlassian apps/atlassian-mcp \
  --set jira.url=https://example.atlassian.net \
  --set confluence.url=https://example.atlassian.net/wiki
```

For a team of a tenant, add `--set team=<team> --set tenant=<tenant>`.

## Delivery instructions

- Ready when the Deployment `<release>` is Available and the Service `<release>` answers on
  port 8000, path `/mcp`.
- Callers send their own Atlassian credential: `Authorization: Basic base64(email:api_token)`
  directly, or through an MCP gateway in the header named by
  `agentgateway.dev/mcp-credential-header`.
- A call without a credential answers 401 in perCaller mode; that is the expected result for
  a caller that has no Atlassian account yet.

## Values

- `team` (default: empty), `tenant` (default: empty): team namespace `<tenant>-<team>`.
- `image.repository`, `image.tag` (default: `ghcr.io/sooperset/mcp-atlassian`, `0.23.1`).
- `jira.url`, `confluence.url` (default: empty): at least one is required.
- `credentials.mode` (default: `perCaller`): `perCaller` or `shared`.
- `credentials.gatewayHeader` (default: `X-Atlassian-Authorization`): header an MCP gateway
  copies into the backend `Authorization`; empty: no annotation.
- `credentials.existingSecret` (default: empty): the shared account; required in shared mode.
- `env` (default: `{}`): extra environment variables of the server.
- `port`, `path` (default: `8000`, `/mcp`).
- `mcp.appProtocol` (default: `agentgateway.dev/mcp`), `mcp.targetName` (default: `atlassian`).
- `service.labels`, `service.annotations` (default: `{}`): for example a platform's exposure
  label.
- `podLabels`, `podAnnotations` (default: `{}`).
- `resources` (default: 20m/128Mi request, 1 CPU/512Mi limit).
- `networkPolicy.enabled` (default: true), `networkPolicy.ingressNamespaces` (default: `[]`):
  namespaces that may call the server besides its own, for example the gateway's.
- `networkPolicy.egress.*`: internet egress except private ranges, plus DNS.
