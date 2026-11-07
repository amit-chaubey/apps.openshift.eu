# MCP gateway annotations

A chart that runs an MCP server in the project describes it on its Service with the
discovery conventions of [agentgateway](https://agentgateway.dev). An MCP gateway that
selects Services by these keys picks the server up when the chart is installed and drops it
when the chart is removed; no agent needs a change either way.

## Service keys

| Key | Kind | Value | Effect |
|---|---|---|---|
| `appProtocol: agentgateway.dev/mcp` | field of the Service port | | Marks the port that speaks MCP over streamable HTTP |
| `agentgateway.dev/mcp-path` | annotation | Path of the MCP endpoint, for example `/mcp` | The path the gateway calls. Default: `/mcp` |
| `agentgateway.dev/mcp-target-name` | annotation | Name of the server, for example `atlassian` | The gateway prefixes every tool with it: `<target name>_<tool>`. Default: the Service name |
| `agentgateway.dev/mcp-credential-header` | annotation | Name of a request header, for example `X-Atlassian-Authorization` | The caller sends its own account for the server in this header; the gateway copies it into the request's `Authorization` to the server and removes the original header |
| `agentgateway.dev/mcp-credential-extauth` | annotation | `<service>:<port>` of a Service in the same namespace | The gateway sends each caller's token to that service, which answers with the `Authorization` header for the server. The service decides which account of the server a caller gets |

```yaml
apiVersion: v1
kind: Service
metadata:
  name: atlassian
  annotations:
    agentgateway.dev/mcp-path: /mcp
    agentgateway.dev/mcp-target-name: atlassian
    agentgateway.dev/mcp-credential-header: X-Atlassian-Authorization
spec:
  ports:
  - name: http
    port: 8000
    appProtocol: agentgateway.dev/mcp
```

## Choosing how the server gets a credential

| The server needs | Use | Example chart |
|---|---|---|
| No account, or one account for every caller | A Secret of the chart, read by the server itself | `mcp-playwright`, `mcp-atlassian` with `credentials.mode: shared` |
| The caller's own account, which the caller holds | `agentgateway.dev/mcp-credential-header` | `mcp-atlassian` with `credentials.mode: perCaller` |
| The caller's own account when it has one, else one shared account | `agentgateway.dev/mcp-credential-header` and a Secret of the chart | `mcp-atlassian` with `credentials.mode: shared` and `credentials.callerOverride: true` |
| An account per agent, which nobody hands out by hand | `agentgateway.dev/mcp-credential-extauth` and a credential service in the chart | `mcp-sonarqube` with `credentials.mode: broker` |
| The user's own login at a vendor | No Service key: a RemoteMCPServer with OAuth annotations | see [Agent OAuth login](oauth.md) |

A caller's `Authorization` header always authenticates the caller to the gateway, which is
why a per-caller credential travels in a second header.

An agent of the `openhands` or `opencode-agent` chart sends that header from a vault item:
`vaultSecrets` reads the item into a Secret mounted as files, and the `auth` of an
`mcp.servers` entry names the header and how to build it (`basic`, `bearer` or `raw`). The
agent's token proxy replaces only `Authorization`, so the second header reaches the gateway
unchanged.

## A token check in the chart

A server that a gateway or an agent reaches from outside its cluster, for example through a
Route, cannot rely on the gateway alone: anything that reaches the Route reaches the server.
Every MCP server chart whose server does not authenticate callers itself carries the same
token check, the library chart `mcp-auth`: an oauth2-proxy sidecar that takes the Service
port, switched by these values.

| Value | Who sets it | Content |
|---|---|---|
| `auth.enabled` | the chart, or the parent chart that exposes the server | Switches the check on |
| `auth.issuerUrl` | the platform, once for all installations: a constant value of the product or the default of a Template | Issuer of the callers' tokens. Empty: the check refuses every request but the health paths |
| `auth.audiences` | derived by the parent chart from the instance, when it can | Audiences a token must carry, so a token for another application is refused. Empty: the release namespace |
| `auth.allowedGroups` | the parent chart or the user | Groups a token must carry |
| `auth.resourceUrl` | the parent chart that publishes the server | Public address, named in the protected resource metadata |

The caller's token reaches the server unchanged. The check answers 401 without a valid
token, 403 for a token of another group, and serves `/.well-known/oauth-protected-resource`
(RFC 9728) without a token, so an agent finds the issuer. The chart holds no issuer address
as a default: a chart never names a platform. The README of `mcp-auth` describes the
values; `mcp-officeagent` is the reference chart.

| Chart | Caller authentication |
|---|---|
| `mcp-officeagent`, `mcp-slack`, `mcp-playwright`, `mcp-atlassian` (shared mode), `mcp-server` | `mcp-auth`, optional |
| `mcp-nextcloud`, `mcp-onlyoffice-live` | the server itself: Nextcloud checks the caller's token |
| `mcp-gmail`, `mcp-google-*`, `mcp-microsoft-365` | the vendor's OAuth login |

## An agent token for a server of the same issuer

An agent that holds its own OpenID Connect token sends it to a server that takes tokens of
the same issuer. A RemoteMCPServer says so with two annotations; without them the agent
reads the server's protected resource metadata.

| Key | Kind | Value | Effect |
|---|---|---|---|
| `mcp.openshift.eu/auth` | annotation | `oidc` | The server takes OpenID Connect bearer tokens |
| `mcp.openshift.eu/oidc-issuer` | annotation | Issuer URL, as the tokens' `iss` claim | The agent sends its own token when this is its own issuer, and skips the server otherwise |

The `openhands` chart acts on them: its token proxy forwards only to the URLs its MCP sync
registered from these RemoteMCPServers, over https or to a cluster Service.

## On the grn.cloud platform

The grn.cloud platform runs such a gateway for every tenant. For a Service with an
`agentgateway.dev/mcp` port in a tenant project, the platform:

- publishes the server on its own gateway path, `/mcp/<namespace>/<target name>`;
- writes a kagent RemoteMCPServer named after the target name into the server's namespace,
  which the project's agents discover;
- admits only the callers that the label `mcp.grn.cloud/expose` on the Service allows;
- turns `agentgateway.dev/mcp-credential-header` and `agentgateway.dev/mcp-credential-extauth`
  into the matching gateway policy. A caller that authenticates with an API key gets no
  credential from an extauth service.

| `mcp.grn.cloud/expose` | Who may call and see the server |
|---|---|
| `namespace` | The ServiceAccounts and the API key of the server's namespace, and the tenant's administrators |
| `tenant` | Every ServiceAccount, person and API key of the tenant |
| `public` | Every logged-in caller of the platform |

Without the label, a server in the tenant's home project is `tenant` and a server in any
other project is `namespace`. A chart passes the label through a value such as
`service.labels`; it never sets it by default, because the label belongs to the platform.

An agent of the project calls each server on its own path; it never uses a bundle of several
servers, so every server keeps its own tool list and icon.
