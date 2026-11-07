# mcp-playwright

This chart supersedes `playwright-mcp`, the earlier name of the same chart. Its releases stay
available; new versions are published under this name only.

## Purpose

Runs one [Playwright MCP](https://github.com/microsoft/playwright-mcp) server in your project: a headless Chromium browser that AI agents drive through MCP tools (`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_take_screenshot` and more). It serves streamable HTTP MCP on port 8931 at `/mcp` and is meant to sit behind an MCP gateway, not on a public Route.

Choices:

- The Service port carries `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and `agentgateway.dev/mcp-target-name`, so an agentgateway backend with a label selector discovers it and prefixes its tools with the target name (default: the release name).
- Every MCP session gets its own in-memory browser profile (`--isolated`); sessions share no cookies or storage.
- The pod has no ServiceAccount token and no browser sandbox of its own; the pod is the sandbox (non-root, no capabilities, `RuntimeDefault` seccomp).
- A NetworkPolicy admits callers from the own project and from `networkPolicy.ingressNamespaces` only, and lets the browser reach cluster DNS and the public internet, never private ranges (`networkPolicy.egress.exceptCidrs` plus your own public ranges in `extraExceptCidrs`).
- Port 8931 is outside the ports a typical project default opens to everyone (80, 443, 8080, 8443).

### Token check

`auth.enabled` (default: false) puts the token check of the library chart `mcp-auth` in
front of the server: an oauth2-proxy sidecar takes the Service port and the server listens
on `127.0.0.1:auth.upstreamPort` (default: 18931) only. Every request needs a bearer
JWT that `auth.issuerUrl` signed for one of `auth.audiences` (default: the release
namespace), else 401; a token whose groups claim names none of `auth.allowedGroups` gets
403. The caller's `Authorization` header reaches the server unchanged, streams pass as they
come, and `/.well-known/oauth-protected-resource` names the issuer without a token (RFC
9728). Without `auth.issuerUrl` every request but the health paths is refused. The README
of `mcp-auth` lists every `auth.*` value.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install web grncloud/mcp-playwright \
  --set 'networkPolicy.ingressNamespaces[0].matchLabels.kubernetes\.io/metadata\.name=my-gateway-namespace'
```

## Delivery instructions

- Check the pod is Ready: `oc get pods -l app.kubernetes.io/instance=web`.
- Through your MCP gateway the tools appear as `web_browser_navigate` and so on. From inside the project: `POST http://web:8931/mcp` with an MCP `initialize` request.
- A page that the browser must not reach (an internal service with a public address) goes into `networkPolicy.egress.extraExceptCidrs`.
