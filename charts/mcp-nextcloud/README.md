# mcp-nextcloud

## Purpose

Runs the Nextcloud MCP server [cbcoutinho/nextcloud-mcp-server](https://github.com/cbcoutinho/nextcloud-mcp-server) in your project for its agents: files (WebDAV), notes, calendar, contacts, deck, tables, cookbook, news and talk of one Nextcloud, always as the calling account. The server holds no Nextcloud credential of its own (`MCP_DEPLOYMENT_MODE=multi_user_basic`): it passes each caller's `Authorization` header to Nextcloud, a bearer token that Nextcloud's `user_oidc` app accepts (`nextcloud.bearerPassthrough`) or Basic with an app password. Nextcloud decides who the caller is and what it may read; a call without a credential gets 401. The server therefore needs no token check in front of it.

The default image `ghcr.io/hoolia/nextcloud-mcp-server:0.201.2-bearer.2` (pinned by digest, built from `images/nextcloud-mcp-server` in this repository) is upstream 0.201.2 with the bearer passthrough patch; the upstream releases up to 0.202.1 pass no bearer token through.

The server listens on streamable HTTP at `/mcp` on port `port`. Its objects are named `mcp-nextcloud`, so they never collide with those of Nextcloud in the same project. The Service carries the port `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and `agentgateway.dev/mcp-target-name: nextcloud`; the NetworkPolicy admits the own project and `networkPolicy.ingressNamespaces`, and lets the server reach DNS and its own project, where Nextcloud runs. Semantic search state stays in memory.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install mcp-nextcloud grncloud/mcp-nextcloud --set nextcloud.url=http://nextcloud:8080
```

The `nextcloud` chart of this repository installs it as a dependency and publishes it on the Nextcloud host at `/mcp/nextcloud`.

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a parent chart.
- `nameOverride` (default: empty, meaning `mcp-<mcp.server>`): Name of the objects.
- `image.repository`, `image.tag` (default: `ghcr.io/hoolia/nextcloud-mcp-server`, `0.201.2-bearer.2` by digest): Server image.
- `nextcloud.url` (default: `http://nextcloud:8080`): Address of Nextcloud the server calls.
- `nextcloud.deploymentMode` (default: `multi_user_basic`): `MCP_DEPLOYMENT_MODE` of the server.
- `nextcloud.bearerPassthrough` (default: true): Pass the caller's bearer token to Nextcloud.
- `env` (default: empty): Extra environment variables of the server.
- `port`, `path` (default: `8000`, `/mcp`): Container and Service port, path of the MCP endpoint.
- `mcp.appProtocol`, `mcp.labelDomain`, `mcp.server`, `mcp.bundle`, `mcp.category` (default: `agentgateway.dev/mcp`, `mcp.openshift.eu`, `nextcloud`, `nextcloud`, `collaboration`): Gateway discovery and catalog labels.
- `service.labels`, `service.annotations` (default: empty): Extra labels and annotations of the Service.
- `resources` (default: 50m/256Mi request, 1Gi limit): Requests and limits of the server.
- `networkPolicy.ingressNamespaces` (default: empty): Namespace selectors that may call the server besides its own project, for example the router's or an MCP gateway's.

## Delivery instructions

1. Install the chart in the project of Nextcloud, or point `nextcloud.url` at it.
2. Check that `user_oidc` in Nextcloud validates bearer tokens of your identity provider; without that, only Basic with an app password works.
3. Call `POST /mcp` with an MCP `initialize` request and a caller's bearer token: 200. Without a token: 401.

Prompt injection: files, notes and chats can contain text that steers the agent that reads them. Treat content from outside the project as untrusted input.
