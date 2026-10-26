# Conventions

Charts and agents find each other through labels and annotations, not through a list kept in
one place. A server chart labels its objects; an agent chart reads those labels and decides
which servers it uses and how it logs in. This section is the reference for every such key.
A key not listed here changes no behaviour.

| Key | Kind | On | Set by | Read by | Page |
|---|---|---|---|---|---|
| `mcp.openshift.eu/server` | label, Chart.yaml annotation | RemoteMCPServer, Service | MCP server chart | agent chart, catalog | [MCP catalog labels](mcp-catalog.md) |
| `mcp.openshift.eu/bundle` | label, Chart.yaml annotation | RemoteMCPServer, Service | MCP server chart | agent chart, catalog | [MCP catalog labels](mcp-catalog.md) |
| `mcp.openshift.eu/category` | label, Chart.yaml annotation | RemoteMCPServer, Service | MCP server chart | catalog | [MCP catalog labels](mcp-catalog.md) |
| `mcp.openshift.eu/addon` | label | RemoteMCPServer | MCP server chart | agent chart | [MCP catalog labels](mcp-catalog.md) |
| `mcp.openshift.eu/auth` | Chart.yaml annotation | Chart.yaml | MCP server chart | catalog | [MCP catalog labels](mcp-catalog.md) |
| `openhands.dev/icon` | annotation | RemoteMCPServer | MCP server chart, platform | `openhands` | [MCP catalog labels](mcp-catalog.md) |
| `openhands.dev/auth` | annotation | RemoteMCPServer | MCP server chart | `openhands` | [Agent OAuth login](oauth.md) |
| `openhands.dev/oauth-scopes` | annotation | RemoteMCPServer | MCP server chart | `openhands` | [Agent OAuth login](oauth.md) |
| `openhands.dev/oauth-client` | annotation | RemoteMCPServer | MCP server chart | `openhands` | [Agent OAuth login](oauth.md) |
| `openhands.dev/oauth-client-auth-method` | annotation | RemoteMCPServer | MCP server chart | `openhands` | [Agent OAuth login](oauth.md) |
| `appProtocol: agentgateway.dev/mcp` | Service port field | Service | MCP server chart | MCP gateway | [MCP gateway annotations](mcp-gateway.md) |
| `agentgateway.dev/mcp-path` | annotation | Service | MCP server chart | MCP gateway | [MCP gateway annotations](mcp-gateway.md) |
| `agentgateway.dev/mcp-target-name` | annotation | Service | MCP server chart | MCP gateway | [MCP gateway annotations](mcp-gateway.md) |
| `agentgateway.dev/mcp-credential-header` | annotation | Service | MCP server chart | MCP gateway | [MCP gateway annotations](mcp-gateway.md) |
| `agentgateway.dev/mcp-credential-extauth` | annotation | Service | MCP server chart | MCP gateway | [MCP gateway annotations](mcp-gateway.md) |

The values of the `openhands` chart that act on these labels, `mcp.labelDomain`,
`mcp.bundles` and `mcp.servers`, are described with the labels in
[MCP catalog labels](mcp-catalog.md#selecting-add-ons-in-an-agent).

## Rules for a new key

- A chart holds no platform, company or tenant name and no domain name, except the label
  domain `mcp.openshift.eu`, which is the neutral prefix of the MCP catalog.
- A label or annotation that a chart sets for the console or for reporting goes on the
  object's own metadata, never on a pod template: a changed pod template restarts every pod.
- A key that changes behaviour gets a row in the table above and a section on its page in
  the same pull request that introduces it.
