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
| No account, or one account for every caller | A Secret of the chart, read by the server itself | `playwright-mcp`, `atlassian-mcp` with `credentials.mode: shared` |
| The caller's own account, which the caller holds | `agentgateway.dev/mcp-credential-header` | `atlassian-mcp` with `credentials.mode: perCaller` |
| An account per agent, which nobody hands out by hand | `agentgateway.dev/mcp-credential-extauth` and a credential service in the chart | `mcp-sonarqube` with `credentials.mode: broker` |
| The user's own login at a vendor | No Service key: a RemoteMCPServer with OAuth annotations | see [Agent OAuth login](oauth.md) |

A caller's `Authorization` header always authenticates the caller to the gateway, which is
why a per-caller credential travels in a second header.

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
