# mcp-auth

## Purpose

Library chart with the token check that the MCP server charts of this repository put in
front of a server that does not authenticate its callers itself. It renders nothing on its
own: a chart depends on it and includes its templates. The check is an
[oauth2-proxy](https://github.com/oauth2-proxy/oauth2-proxy) sidecar
(`quay.io/oauth2-proxy/oauth2-proxy`, v7.15.5 by digest) that takes the chart's Service
port; the server then listens on `127.0.0.1:<auth.upstreamPort>` only.

With `auth.enabled` the sidecar:

| Request | Answer |
|---|---|
| No bearer token, or a token that the issuer did not sign, that has expired or whose `aud` names none of `auth.audiences` | 401, never a login redirect |
| A valid token whose groups claim (`auth.groupsClaim`) names none of `auth.allowedGroups`, when the list is set | 403 |
| A valid token of an allowed group | passed to the server, `Authorization` header unchanged |
| `GET /oauth2/ping` | 200 from the sidecar, for its own probe |
| `GET` on the server's health path (the chart names it) and `auth.skipAuthRoutes` | passed to the server without a token |
| `GET /.well-known/oauth-protected-resource` | 200: protected resource metadata (RFC 9728) with `authorization_servers: [auth.issuerUrl]` and `resource: auth.resourceUrl`, without a token |

- `auth.issuerUrl` empty with `auth.enabled`: the sidecar starts with an issuer that no
  token can match (`https://issuer.invalid`), so every request but the health paths gets
  401. The chart still installs; it never fails open.
- Streams: the sidecar flushes an event stream at once and has no idle limit on an open
  response; `auth.upstreamTimeout` (default `3600s`) bounds the wait for the server's
  response headers.
- oauth2-proxy requires a cookie secret and a client secret. Bearer tokens open no
  session, so neither protects a login; both are random, generated once into the Secret
  `<name>-auth` and read back on every upgrade (`lookup`). `auth.cookieSecret` and
  `auth.clientSecret` set them, `auth.existingSecret` names a Secret with the keys
  `cookie-secret` and `client-secret` instead.
- The sidecar runs as any non-root user with a read-only root filesystem, no capability
  and the `RuntimeDefault` seccomp profile, so `restricted-v2` admits it. It asks 10m CPU
  and 32Mi memory.
- The metadata is served from the ConfigMap `<name>-auth` at the path prefix
  `/.well-known/`, which the sidecar then answers itself instead of the server.

### Values

Every chart that uses the library carries this block in its own `values.yaml`, with the
same keys and comments. The chart sets its own default of `upstreamPort`.

| Value | Default | Meaning |
|---|---|---|
| `auth.enabled` | `false` | Switches the sidecar on |
| `auth.issuerUrl` | empty | OpenID Connect issuer of the tokens, as their `iss` claim |
| `auth.audiences` | empty: the release namespace | A token's `aud` must name one of these; the first is oauth2-proxy's client id. Each entry is a template |
| `auth.allowedGroups` | empty: any group | A token's groups claim must name one of these. Each entry is a template |
| `auth.resourceUrl` | empty: `http://<name>.<namespace>.svc:<port><path>` | Address callers use, the `resource` of the metadata. A template |
| `auth.groupsClaim` | `groups` | Claim with the caller's groups |
| `auth.upstreamPort` | per chart | Localhost port of the server |
| `auth.skipAuthRoutes` | empty | Further requests without a token, `method=path_regex` |
| `auth.upstreamTimeout` | `3600s` | Longest wait for the server's response headers |
| `auth.flushInterval` | `100ms` | Flush interval of a streamed response that is no event stream |
| `auth.existingSecret` | empty | Secret with `cookie-secret` and `client-secret` |
| `auth.cookieSecret`, `auth.clientSecret` | empty: generated once | The two secrets; a GitOps tool that renders without cluster access passes them |
| `auth.extraArgs` | empty | Further oauth2-proxy flags |
| `auth.image.*` | oauth2-proxy v7.15.5 by digest | Sidecar image |
| `auth.resources` | 10m/32Mi request, 128Mi limit | Sidecar resources |

The schema of the block is in `values.schema.json` of any consuming chart, for example
`charts/mcp-officeagent/values.schema.json`.

## Install

A library chart is not installed and not released on its own. A chart in this repository
depends on it from the same commit:

```yaml
dependencies:
- name: mcp-auth
  version: 0.1.0
  repository: file://../mcp-auth
```

and includes its templates, with `name` the server's object name and `port` its Service
port:

| Template | Where |
|---|---|
| `mcp-auth.container` (`root`, `name`, `port`, optional `healthPath`) | the pod's container list |
| `mcp-auth.volumes` (`root`, `name`) | the pod's volumes |
| `mcp-auth.serverHost`, `mcp-auth.serverPort` (`root`, `port`) | the server's listen address |
| `mcp-auth.secretName`, `mcp-auth.secretData` (`root`, `name`, `namespace`) | a Secret of the chart, with its own labels |
| `mcp-auth.metadataData` (`root`, `name`, `namespace`, `port`, `path`) | a ConfigMap `<name>-auth` of the chart, with its own labels |
| `mcp-auth.networkPolicyEgress` (root) | egress to the issuer, for a chart whose server reaches nothing else |

The chart's Service keeps `targetPort: mcp`: with `auth.enabled` the sidecar owns that
container port and the server container declares none.

## Delivery instructions

1. Set `auth.enabled`, `auth.issuerUrl`, `auth.audiences` and, where only some callers may
   use the server, `auth.allowedGroups`.
2. Check the result from outside the pod: a request without a token gets 401, one with a
   valid token of an allowed group gets the server's answer, and
   `/.well-known/oauth-protected-resource` names the issuer.
3. Under a GitOps tool that renders without cluster access, pass `auth.cookieSecret` and
   `auth.clientSecret` (or `auth.existingSecret`), else they change on every render.
