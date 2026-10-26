# mcp-google-sheets

## Purpose

Makes Google's hosted Google Sheets MCP server (`https://sheetsmcp.googleapis.com/mcp/v1`) available to the agents of your project as a kagent `RemoteMCPServer`. Nothing runs in the project: every user logs in to Google with OAuth in the agent's UI and the agent calls the server with that user's own token, so it sees exactly what the user may see. Tools: read spreadsheets and cell values; with write scopes also update values, formulas and rows.

By default users grant read-only scopes (`https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/spreadsheets.readonly`); `write: true` adds `https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/spreadsheets`, so the agent can edit spreadsheets.

The `RemoteMCPServer` carries the labels `mcp.openshift.eu/server: google-sheets`, `mcp.openshift.eu/bundle: google-workspace`, `mcp.openshift.eu/category: productivity` and `mcp.openshift.eu/addon: "true"`: an add-on reaches an agent only when the agent asks for its bundle or server name (for an `openhands` agent, `mcps.bundles: [google-workspace]` or `mcps.servers: [google-sheets]`). Its annotations `openhands.dev/auth: oauth2`, `openhands.dev/oauth-client` and `openhands.dev/oauth-scopes` tell the agent how to log in. The chart `mcp-bundle-google-workspace` installs this server together with the other servers of the bundle and one shared OAuth client.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install google-sheets grncloud/mcp-google-sheets \
  --set secrets.clientId=<client id> --set secrets.clientSecret=<client secret>
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a bundle chart.
- `nameOverride` (default: empty): Name of the `RemoteMCPServer` and of the server in the agent UI; empty: `mcp.server`.
- `url` (default: `https://sheetsmcp.googleapis.com/mcp/v1`): Address of the MCP server.
- `scopes` (default: read-only, see Purpose): OAuth scopes every user grants.
- `write` (default: false): Also grant `writeScopes`.
- `writeScopes` (default: see Purpose): Scopes for writing.
- `mcp.labelDomain` (default: `mcp.openshift.eu`): Prefix of the selection labels.
- `mcp.server`, `mcp.bundle`, `mcp.category` (default: `google-sheets`, `google-workspace`, `productivity`): Values of the selection labels.
- `mcp.addon` (default: true): Label the server as an add-on; false gives it to every agent that may list `RemoteMCPServer` objects here.
- `mcp.description`, `mcp.icon`: Description and card icon in the agent UI.
- `oauthClient.existingSecret` (default: empty): Existing Secret with `client_id` and `client_secret`; empty: `<release>-<oauthClient.secretSuffix>`, written from `secrets`.
- `oauthClient.secretSuffix` (default: `oauth-client`): Name suffix of the chart's own client Secret.
- `oauthClient.create` (default: true): Write that Secret; bundle charts write one shared Secret instead.
- `secrets.type` (default: `kubernetes`): `kubernetes` writes the Secret from `secrets.clientId` and `secrets.clientSecret` (empty: no Secret, and agents skip the server until it exists); `externalSecret` reads them from `secrets.externalSecret.item` (username: client id, password: client secret) in `secrets.externalSecret.storeName`.

## Delivery instructions

Google offers its Workspace MCP servers to members of the Google Workspace Developer Preview Program. In a Google Cloud project of your organisation (https://developers.google.com/workspace/guides/configure-mcp-servers):

1. Enable the APIs and their MCP services: `gcloud services enable sheets.googleapis.com drive.googleapis.com sheetsmcp.googleapis.com --project=<project>`.
2. Google Auth Platform > Branding: configure the consent screen, audience **Internal** for a Workspace organisation.
3. Google Auth Platform > Data Access: add the scopes of `scopes` (and `writeScopes` with `write: true`).
4. Google Auth Platform > Clients > Create client: type **Web application**, select **Use this client for an AI-powered agent**, and add one authorized redirect URI per agent, `https://<agent route host>/mcp-oauth/callback`.
5. Put the client id and secret into `secrets.clientId` and `secrets.clientSecret`, or into a vault item for `secrets.type: externalSecret`. One client serves every Google server; `mcp-bundle-google-workspace` shares it.

The agent asks Google for offline access, so a login keeps working after the first hour.

Then give the agent the server: for an `openhands` agent in this project set `mcps.servers: [google-sheets]` or `mcps.bundles: [google-workspace]`. The server appears switched off under **Settings > MCP**; each user opens it, authorizes it with their own account and switches it on. A user who removes the grant at the vendor clicks **Reauthorize**.

Prompt injection: an agent that reads mail or documents from strangers can be steered by them. Keep `write: false` unless the agent needs to write, and review what it does.
