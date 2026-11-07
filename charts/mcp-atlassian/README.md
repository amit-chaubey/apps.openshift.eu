# mcp-atlassian

This chart supersedes `atlassian-mcp`, the earlier name of the same chart. Its releases stay
available; new versions are published under this name only.

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
- `credentials.mode: shared` with `credentials.callerOverride: true`: per caller with
  fallback. A caller that sends its own account acts as itself; a caller without one acts as
  the shared account. The Service then carries the gateway header annotation as in perCaller
  mode. An invalid caller account is refused, never replaced by the shared one.
- `credentials.cloudId`: the Cloud ID of the site (`https://<site>/_edge/tenant_info`). A
  Bearer token (an OAuth access token, or an API key of an Atlassian service account, which
  works only as Bearer) then goes to `https://api.atlassian.com/ex/jira/<cloudId>` and
  `.../ex/confluence/<cloudId>`; a Basic account keeps going to the site URL, so one server
  takes both. Without it a Bearer token goes to the site URL, which refuses it. In shared
  mode the server itself then counts as an OAuth client: the shared account must be a Bearer
  token too, `ATLASSIAN_OAUTH_ACCESS_TOKEN` in `credentials.existingSecret` (for example the
  API key of a team service account), because a shared Basic account and Bearer callers do
  not combine in one server.
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

### Token check

`auth.enabled` (default: false) puts the token check of the library chart `mcp-auth` in
front of the server: an oauth2-proxy sidecar takes the Service port and the server listens
on `127.0.0.1:auth.upstreamPort` (default: 18000) only. Every request but `GET /healthz` needs a bearer
JWT that `auth.issuerUrl` signed for one of `auth.audiences` (default: the release
namespace), else 401; a token whose groups claim names none of `auth.allowedGroups` gets
403. The caller's `Authorization` header reaches the server unchanged, streams pass as they
come, and `/.well-known/oauth-protected-resource` names the issuer without a token (RFC
9728). Without `auth.issuerUrl` every request but the health paths is refused. The README
of `mcp-auth` lists every `auth.*` value. The token check takes the `Authorization` header that `credentials.mode: perCaller` reads the caller's Atlassian account from, so it needs `credentials.mode: shared`; the chart refuses to render the two together.

## Install

```bash
helm repo add apps https://apps.openshift.eu
helm install atlassian apps/mcp-atlassian \
  --set jira.url=https://example.atlassian.net \
  --set confluence.url=https://example.atlassian.net/wiki
```

For a team of a tenant, add `--set team=<team> --set tenant=<tenant>`.

## Delivery instructions

- Ready when the Deployment `<release>` is Available and the Service `<release>` answers on
  port 8000, path `/mcp`.
- Callers send their own Atlassian credential: `Authorization: Basic base64(email:api_token)`
  or, with `credentials.cloudId`, `Authorization: Bearer <token>` directly, or through an MCP
  gateway in the header named by `agentgateway.dev/mcp-credential-header`. The `openhands`
  and `opencode-agent` charts send it from a vault item (`vaultSecrets` and `mcp.servers`).
- A call without a credential answers 401 in perCaller mode; that is the expected result for
  a caller that has no Atlassian account yet.

## Values

- `team` (default: empty), `tenant` (default: empty): team namespace `<tenant>-<team>`.
- `image.repository`, `image.tag` (default: `ghcr.io/sooperset/mcp-atlassian`, `0.23.1`).
- `jira.url`, `confluence.url` (default: empty): at least one is required.
- `credentials.mode` (default: `perCaller`): `perCaller` or `shared`.
- `credentials.gatewayHeader` (default: `X-Atlassian-Authorization`): header an MCP gateway
  copies into the backend `Authorization`; empty: no annotation.
- `credentials.callerOverride` (default: false): shared mode only; callers may bring their
  own account in `credentials.gatewayHeader`. Needs `credentials.gatewayHeader`; refused with
  `auth.enabled`, whose token check reads the same `Authorization` header.
- `credentials.cloudId` (default: empty): Cloud ID of the site, for Bearer tokens.
- `credentials.existingSecret` (default: empty): the shared account; required in shared mode.
  `JIRA_USERNAME` and `JIRA_API_TOKEN`, or with `credentials.cloudId`
  `ATLASSIAN_OAUTH_ACCESS_TOKEN`.
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
