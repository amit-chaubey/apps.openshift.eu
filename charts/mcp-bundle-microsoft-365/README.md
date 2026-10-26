# mcp-bundle-microsoft-365

## Purpose

Installs the Microsoft 365 MCP servers of the catalog for the agents of your project in one release. Today the bundle holds one server, `mcp-microsoft-365` (Softeria ms-365-mcp-server: Outlook mail and calendar, OneDrive, OneNote, To Do and, for work accounts, Teams and SharePoint), a `RemoteMCPServer` named `microsoft-365`; further Microsoft servers join the bundle as they come. Every user logs in to Microsoft with OAuth in the agent's UI; the servers offer only tools that read by default.

The servers carry the labels `mcp.openshift.eu/bundle: microsoft-365`, `mcp.openshift.eu/category: productivity` and `mcp.openshift.eu/addon: "true"`, so an agent gets them only when it asks for the bundle (for an `openhands` agent, `mcp.bundles: [microsoft-365]`) or for a server by name.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install m365 grncloud/mcp-bundle-microsoft-365 \
  --set microsoft365.route.host=m365-mcp.apps.example.com \
  --set microsoft365.microsoft.tenantId=<tenant id> \
  --set microsoft365.secrets.clientId=<client id> \
  --set microsoft365.secrets.clientSecret=<client secret>
```

- `enabled` (default: true): Switch of the bundle inside `mcp-all`.
- `microsoft365` (default: `enabled: true`): The values of `mcp-microsoft-365`; `route.host` and the Entra app registration in `secrets` are required.

## Delivery instructions

Register the Entra app and fill in its client as the Delivery instructions of `mcp-microsoft-365` describe (https://github.com/hoolia/apps.openshift.eu/tree/main/charts/mcp-microsoft-365). Then give the agent the bundle: for an `openhands` agent in this project set `mcp.bundles: [microsoft-365]`. The server appears switched off under **Settings > MCP**; each user opens it, authorizes it with their own Microsoft account and switches it on.
