# mcp-bundle-google-workspace

## Purpose

Installs Google's hosted Workspace MCP servers for the agents of your project in one release: `mcp-gmail`, `mcp-google-drive`, `mcp-google-docs`, `mcp-google-sheets` and `mcp-google-calendar`, each a kagent `RemoteMCPServer` (`gmail`, `google-drive`, `google-docs`, `google-sheets`, `google-calendar`). Nothing runs in the project: every user logs in to Google with OAuth in the agent's UI, and users grant read-only scopes unless a server's `write` is true.

All servers share one OAuth client, the Secret `<release>-google-oauth-client` this chart writes. They carry the labels `mcp.openshift.eu/bundle: google-workspace`, `mcp.openshift.eu/category: productivity` and `mcp.openshift.eu/addon: "true"`, so an agent gets them only when it asks for the bundle (for an `openhands` agent, `mcps.bundles: [google-workspace]`) or for single servers by name (`mcps.servers: [gmail]`).

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install google grncloud/mcp-bundle-google-workspace \
  --set secrets.clientId=<client id> --set secrets.clientSecret=<client secret>
```

- `enabled` (default: true): Switch of the bundle inside `mcp-all`.
- `secrets.type` (default: `kubernetes`): `kubernetes` writes the shared client Secret from `secrets.clientId` and `secrets.clientSecret` (empty: no Secret, and agents skip the servers until it exists); `externalSecret` reads them from `secrets.externalSecret.item` (username: client id, password: client secret) in `secrets.externalSecret.storeName`.
- `gmail`, `googleDrive`, `googleDocs`, `googleSheets`, `googleCalendar` (default: `enabled: true`): The values of each server's chart, for example `googleDocs.write: true` for write scopes or `googleDrive.enabled: false` to leave a server out. Keep `oauthClient` as set: it points every server at the shared client.

## Delivery instructions

Google offers its Workspace MCP servers to members of the Google Workspace Developer Preview Program. In a Google Cloud project of your organisation (https://developers.google.com/workspace/guides/configure-mcp-servers):

1. Enable the APIs and their MCP services: `gcloud services enable gmail.googleapis.com drive.googleapis.com docs.googleapis.com sheets.googleapis.com calendar-json.googleapis.com gmailmcp.googleapis.com drivemcp.googleapis.com docsmcp.googleapis.com sheetsmcp.googleapis.com calendarmcp.googleapis.com --project=<project>`.
2. Google Auth Platform > Branding: configure the consent screen, audience **Internal** for a Workspace organisation.
3. Google Auth Platform > Data Access: add the scopes of the servers you use (read-only by default, see each server's chart).
4. Google Auth Platform > Clients > Create client: type **Web application**, select **Use this client for an AI-powered agent**, and add one authorized redirect URI per agent, `https://<agent route host>/mcp-oauth/callback`.
5. Put the client id and secret into `secrets.clientId` and `secrets.clientSecret`, or into a vault item for `secrets.type: externalSecret`.

Then give the agent the bundle: for an `openhands` agent in this project set `mcps.bundles: [google-workspace]`. The servers appear switched off under **Settings > MCP**; each user opens one, authorizes it with their own Google account and switches it on. The agent asks Google for offline access, so a login keeps working after the first hour.

Prompt injection: an agent that reads mail or documents from strangers can be steered by them. Keep write scopes off unless the agent needs them, and review what it does.
