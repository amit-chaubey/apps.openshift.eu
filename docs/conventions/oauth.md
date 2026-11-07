# Agent OAuth login

A RemoteMCPServer annotated `openhands.dev/auth: oauth2` is a server that every user logs in
to with OAuth at the server's vendor, for example Google or Microsoft. The agent calls the
server with that user's own token, so it sees exactly what the user may see. No account of
the server is stored in the project. The `openhands` chart reads these annotations from
version 0.10.0.

## Annotations

| Annotation | Required | Value | Effect |
|---|---|---|---|
| `openhands.dev/auth` | yes | `oauth2` | The agent adds the server with OAuth login, at the server's own URL (`spec.url`), not through the token proxy or the MCP gateway |
| `openhands.dev/oauth-scopes` | no | Space-separated scopes, for example `https://www.googleapis.com/auth/gmail.readonly` | The scopes the user grants. Server charts ask for read-only scopes by default and add write scopes with a value such as `write: true` |
| `openhands.dev/oauth-client` | no | Name of a Secret in the namespace of the RemoteMCPServer, with the keys `client_id` and `client_secret` | The pre-registered OAuth client the agent uses. Without the annotation the agent registers itself at the server (dynamic client registration) |
| `openhands.dev/oauth-client-auth-method` | no | `client_secret_post`, `client_secret_basic` or `none` | How the agent authenticates the client at the token endpoint. Without the annotation the agent leaves the method unset and its OAuth client picks one |

```yaml
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer
metadata:
  name: gmail
  labels:
    mcp.openshift.eu/server: gmail
    mcp.openshift.eu/bundle: google-workspace
    mcp.openshift.eu/category: productivity
    mcp.openshift.eu/addon: "true"
  annotations:
    openhands.dev/auth: oauth2
    openhands.dev/oauth-client: gmail-oauth-client
    openhands.dev/oauth-scopes: https://www.googleapis.com/auth/gmail.readonly
spec:
  url: https://gmailmcp.googleapis.com/mcp/v1
  protocol: STREAMABLE_HTTP
```

## What the user does

1. The server appears in the agent under **Settings > MCP**, switched off.
2. The user authorizes it in the browser. The vendor returns to
   `https://<agent host>/mcp-oauth/callback`, where `<agent host>` is the `route.host` of the
   `openhands` release.
3. The user switches the server on.

The agent keeps the tokens in its settings, encrypted with the settings key, refreshes them
during conversations and offers **Reauthorize** when a refresh fails.

## What the client owner does

- Register `https://<agent host>/mcp-oauth/callback` as a redirect URI of the OAuth client,
  once per agent. Each server chart's Delivery instructions name the vendor's steps.
- Put the client in the Secret named by `openhands.dev/oauth-client`. A bundle chart writes
  one Secret that all its servers share.

## Behaviour on change

- The agent never overwrites a user's login or switch.
- A changed client id, client secret, scope list or authentication method reaches every
  agent on its next pass; the user's tokens stay.
- Removing `openhands.dev/oauth-scopes` removes the scopes from the agent's entry.
- An OAuth server stays personal to each user: the agent never publishes it to the rest of
  the project.
- An authorization request to `accounts.google.com` also asks for offline access, so Google
  issues a refresh token. This needs the image that the `openhands` chart builds
  (`build.enabled: true`).
