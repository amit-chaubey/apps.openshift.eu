# mcp-microsoft-365

## Purpose

Runs the Microsoft 365 MCP server [softeria/ms-365-mcp-server](https://github.com/softeria/ms-365-mcp-server) in your project for its agents: Outlook mail and calendar, OneDrive, OneNote, To Do, contacts and, for work and school accounts (`microsoft.orgMode`), Teams, SharePoint and shared mailboxes, through Microsoft Graph. The server holds no account. Every user logs in to Microsoft with OAuth in the agent's UI and the agent calls the server with that user's token, so it sees exactly what the user may see. By default the server offers only tools that read (`microsoft.readOnly`).

The server publishes a Route (`route.host`, required) because the login runs on it: the agent registers itself at the server (dynamic client registration), the user's browser goes to `https://<route.host>/authorize`, the server forwards to Microsoft with the Entra app registration in `secrets`, and Microsoft returns to the agent's own callback. MCP calls without a valid Microsoft token are refused.

A kagent `RemoteMCPServer` named `microsoft-365` points at `https://<route.host>/mcp`. It carries the labels `mcp.openshift.eu/server: microsoft-365`, `mcp.openshift.eu/bundle: microsoft-365`, `mcp.openshift.eu/category: productivity` and `mcp.openshift.eu/addon: "true"`: an add-on reaches an agent only when the agent asks for its bundle or server name (for an `openhands` agent, `mcps.bundles: [microsoft-365]`), and the annotation `openhands.dev/auth: oauth2` tells the agent to log in with OAuth.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install m365 grncloud/mcp-microsoft-365 \
  --set route.host=m365-mcp.apps.example.com \
  --set microsoft.tenantId=<tenant id> \
  --set secrets.clientId=<client id> --set secrets.clientSecret=<client secret> \
  --set 'microsoft.allowedRedirectUris={https://agent.apps.example.com/mcp-oauth/callback}'
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a bundle chart.
- `nameOverride` (default: empty): Name of the objects and of the server in the agent UI; empty: `mcp.server`.
- `image.repository`, `image.tag` (default: `ghcr.io/softeria/ms-365-mcp-server`, `0.172.0`): Server image.
- `microsoft.tenantId` (default: `common`): Entra tenant of the app registration, its id or `<name>.onmicrosoft.com`; a single-tenant app needs its own tenant here.
- `microsoft.orgMode` (default: true): Also Teams, SharePoint and shared mailboxes (work and school accounts).
- `microsoft.readOnly` (default: true): Only tools that read; false also offers sending mail and changing events and files.
- `microsoft.allowedScopes` (default: `User.Read Mail.Read MailboxSettings.Read Calendars.Read Contacts.Read People.Read Tasks.Read Notes.Read Files.Read.All Sites.Read.All Chat.Read Team.ReadBasic.All Channel.ReadBasic.All Presence.Read`): Space-separated Microsoft Graph scopes the server may request at most; tools that need more are hidden. The default holds only delegated scopes a user may consent to without an administrator; it hides shared mailboxes and calendars, other users, groups, Teams messages, meeting transcripts and recordings. Empty: every scope of the enabled tools, several of which need admin consent.
- `microsoft.allowedRedirectUris` (default: empty): Redirect URIs the server accepts, one per agent (`https://<agent route host>/mcp-oauth/callback`). Empty: any https URI; Entra still accepts only the URIs of the app registration.
- `route.host` (required): Public host of the server and its login.
- `route.labels`, `route.annotations` (default: cert-manager `letsencrypt` ClusterIssuer): Labels and annotations of the Route.
- `port` (default: 3000): Container port.
- `mcp.labelDomain` (default: `mcp.openshift.eu`): Prefix of the selection labels.
- `mcp.server`, `mcp.bundle`, `mcp.category` (default: `microsoft-365`, `microsoft-365`, `productivity`): Values of the selection labels.
- `mcp.addon` (default: true): Label the server as an add-on; false gives it to every agent that may list `RemoteMCPServer` objects here.
- `mcp.description`, `mcp.icon`: Description and card icon in the agent UI.
- `oauthClient.existingSecret` (default: empty): Existing Secret with `client_id` and `client_secret` of the Entra app registration; empty: `<release>-<oauthClient.secretSuffix>`, written from `secrets`.
- `oauthClient.secretSuffix` (default: `microsoft-oauth-client`), `oauthClient.create` (default: true): Name suffix of the chart's own client Secret, and whether the chart writes it.
- `secrets.type` (default: `kubernetes`): `kubernetes` writes the Secret from `secrets.clientId` and `secrets.clientSecret`; `externalSecret` reads them from `secrets.externalSecret.item` (username: client id, password: client secret) in `secrets.externalSecret.storeName`.
- `resources`: Requests and limits of the server.

## Delivery instructions

The server needs an app registration in your Microsoft Entra tenant; Microsoft sends the login code only to redirect URIs registered there. In the Microsoft Entra admin center (https://entra.microsoft.com), as a user who may register applications:

1. **Entra ID > App registrations > New registration**. Name: `Agents Microsoft 365 MCP`. Supported account types: **Accounts in this organizational directory only**. Redirect URI: platform **Web**, `https://<agent route host>/mcp-oauth/callback` of the first agent. **Register**.
2. On **Overview**, copy the **Application (client) ID** into `secrets.clientId` and the **Directory (tenant) ID** into `microsoft.tenantId`.
3. **Authentication > Web > Add URI**: one `https://<agent route host>/mcp-oauth/callback` per further agent. **Save**. List the same URIs in `microsoft.allowedRedirectUris`.
4. **Certificates & secrets > Client secrets > New client secret**: description `mcp`, expiry 24 months. Copy the **Value** (not the Secret ID) into `secrets.clientSecret`, or into a vault item for `secrets.type: externalSecret`. Renew it before it expires.
5. **API permissions > Add a permission > Microsoft Graph > Delegated permissions**: `offline_access` and the scopes of `microsoft.allowedScopes` (by default `User.Read`, `Mail.Read`, `MailboxSettings.Read`, `Calendars.Read`, `Contacts.Read`, `People.Read`, `Tasks.Read`, `Notes.Read`, `Files.Read.All`, `Sites.Read.All`, `Chat.Read`, `Team.ReadBasic.All`, `Channel.ReadBasic.All`, `Presence.Read`). **Add permissions**. `oc exec deploy/microsoft-365 -- ms-365-mcp-server --org-mode --read-only --list-permissions` prints every scope the tools could use.
6. Each user consents to these scopes at their first login; none needs an administrator, unless the tenant has switched user consent off (**Enterprise applications > Consent and permissions**), in which case an administrator clicks **Grant admin consent for <tenant>** under **API permissions** once. Scopes added to `microsoft.allowedScopes` beyond the default, such as `Mail.Read.Shared`, `User.Read.All` or `ChannelMessage.Read.All`, need that admin consent.

Then give the agent the server: for an `openhands` agent in this project set `mcps.bundles: [microsoft-365]` or `mcps.servers: [microsoft-365]`. The server appears switched off under **Settings > MCP**; each user opens it, authorizes it with their own Microsoft account and switches it on.

Prompt injection: an agent that reads mail or documents from strangers can be steered by them. Keep `microsoft.readOnly` unless the agent needs to write, and review what it does.
