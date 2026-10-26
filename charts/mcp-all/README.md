# mcp-all

## Purpose

Every MCP bundle of the catalog as a switch, so one values file decides which bundles and single MCP servers the agents of your project get. Each bundle is a dependency with its own values: `googleWorkspace` (`mcp-bundle-google-workspace`: Gmail, Drive, Docs, Sheets, Calendar) and `microsoft365` (`mcp-bundle-microsoft-365`). Nothing is installed until a bundle is switched on.

The servers keep their selection labels (`mcp.openshift.eu/server`, `/bundle`, `/category`, `/addon`), so an agent still chooses what it uses, for an `openhands` agent with `mcps.bundles` and `mcps.servers`.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
cat > mcp.yaml <<'EOF'
googleWorkspace:
  enabled: true
  secrets:
    clientId: <google client id>
    clientSecret: <google client secret>
  # single servers: leave the others out
  googleDrive:
    enabled: false
microsoft365:
  enabled: false
EOF
helm install mcp grncloud/mcp-all -f mcp.yaml
```

- `googleWorkspace` (default: `enabled: false`): The values of `mcp-bundle-google-workspace`; `secrets` holds the shared Google OAuth client, `<server>.enabled: false` leaves a server out.
- `microsoft365` (default: `enabled: false`): The values of `mcp-bundle-microsoft-365`; the server's own values sit under `microsoft365.microsoft365` (`route.host`, `microsoft.tenantId`, `secrets`).

## Delivery instructions

Create the OAuth clients of the bundles you switch on as their charts describe (https://github.com/hoolia/apps.openshift.eu/tree/main/charts). Then give the agents what they need: for an `openhands` agent in this project set `mcps.bundles` (for example `[google-workspace]`) or `mcps.servers` (for example `[gmail]`). The servers appear switched off under **Settings > MCP**; each user opens one, authorizes it with their own account and switches it on.
