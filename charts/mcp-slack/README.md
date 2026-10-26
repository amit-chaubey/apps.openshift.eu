# mcp-slack

## Purpose

Runs the Slack MCP server [korotovsky/slack-mcp-server](https://github.com/korotovsky/slack-mcp-server) in your project for its agents: the channels, threads, users and user groups of one Slack workspace, read through the Slack Web API as the bot of a Slack app you install in that workspace. The server holds the app's Bot User OAuth Token (`xoxb-...`); agents call the server without any Slack login of their own, so an agent works unattended.

The bot reads only public and private channels it has been invited to. Search (`conversations_search_messages`) needs a user token and is not offered with a bot token. By default no tool writes to Slack: posting (`slack.addMessage`, bot scope `chat:write`) and reactions (`slack.reactions`, bot scope `reactions:write`) are switches.

The server listens on streamable HTTP at `/mcp` on port `port` and asks callers for no credential. Its Service carries the port `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and `agentgateway.dev/mcp-target-name`, so an MCP gateway in front of it authenticates the callers and publishes the server; the NetworkPolicy admits the own project and `networkPolicy.ingressNamespaces` only. The Deployment and Service carry `mcp.openshift.eu/server: slack`, `mcp.openshift.eu/bundle: slack` and `mcp.openshift.eu/category: collaboration`.

Slack's own hosted MCP server (`https://mcp.slack.com/mcp`) accepts only user tokens from an OAuth login per user, so an agent without a person behind it cannot use it.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install slack grncloud/mcp-slack --set secrets.botToken=<xoxb-token>
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a bundle chart.
- `nameOverride` (default: empty): Name of the objects and of the server on the gateway (tools `<name>_*`); empty: `mcp.server`.
- `image.repository`, `image.tag` (default: `ghcr.io/korotovsky/slack-mcp-server`, `v1.3.0` by digest): Server image.
- `slack.existingSecret` (default: empty): Existing Secret with the token under `slack.tokenKey` (default `token`); empty: `<release>-<slack.secretSuffix>` (default `slack-bot-token`), written from `secrets`.
- `slack.tokenType` (default: `bot`): `bot` for a Bot User OAuth Token; `user` for a User OAuth Token (`xoxp-...`), with which the server acts as that person and can also search.
- `slack.addMessage` (default: false): Offer `conversations_add_message`; needs the bot scope `chat:write`. `slack.addMessageChannels` (default: empty) limits it to these channel IDs.
- `slack.reactions` (default: false): Offer `reactions_add` and `reactions_remove`; needs the bot scope `reactions:write`.
- `slack.enabledTools` (default: empty): Comma-separated tool names to register instead of the defaults. Empty: the tools that only read (`channels_list`, `channels_me`, `conversations_history`, `conversations_replies`, `users_search`, `usergroups_list`; with a user token also `conversations_search_messages` and `conversations_unreads`) plus the write tools switched on above. The server's own default would also offer joining and leaving channels, marking read and changing user groups.
- `slack.cacheTtl` (default: `1h`): Lifetime of the users and channels cache; a channel the bot just joined appears after it.
- `slack.logLevel` (default: `info`): Log level.
- `secrets.type` (default: `kubernetes`): `kubernetes` writes the Secret from `secrets.botToken`; `externalSecret` reads `secrets.externalSecret.property` (default `login.password`) of the vault item `secrets.externalSecret.item` in `secrets.externalSecret.storeName`.
- `port` (default: 13080): Container and Service port.
- `mcp.appProtocol` (default: `agentgateway.dev/mcp`): Service port appProtocol for gateway discovery.
- `mcp.labelDomain`, `mcp.server`, `mcp.bundle`, `mcp.category` (default: `mcp.openshift.eu`, `slack`, `slack`, `collaboration`): Catalog labels.
- `service.labels`, `service.annotations` (default: empty): Extra labels and annotations of the Service, for example the gateway's exposure label.
- `resources`: Requests and limits of the server.
- `networkPolicy.ingressNamespaces` (default: empty): Namespace selectors that may call the server besides its own project; the gateway's namespace belongs here. Egress goes to DNS and public addresses on port 443 only.

The server reads the token once at start: after a new token, `oc rollout restart deployment/slack`.

## Delivery instructions

The server needs a Slack app installed in your workspace. At https://api.slack.com/apps, as a member who may install apps in the workspace:

1. **Create New App > From a manifest**, pick the workspace, paste this manifest (YAML) and **Create**:

   ```yaml
   display_information:
     name: Agents
     description: Read access for the project's AI agents
   features:
     bot_user:
       display_name: agents
       always_online: false
   oauth_config:
     scopes:
       bot:
       - channels:read
       - channels:history
       - groups:read
       - groups:history
       - im:read
       - mpim:read
       - users:read
       - users:read.email
       - usergroups:read
   settings:
     org_deploy_enabled: false
     socket_mode_enabled: false
     token_rotation_enabled: false
   ```

   Add `chat:write` to the bot scopes for `slack.addMessage`, `reactions:write` for `slack.reactions`. `im:read` and `mpim:read` are needed because the server lists every conversation type at start.
2. **Install App > Install to Workspace > Allow**. A workspace that requires app approval sends the request to its admins first.
3. Copy the **Bot User OAuth Token** (`xoxb-...`) into `secrets.botToken`, or into the password of a vault item for `secrets.type: externalSecret`.
4. In Slack, invite the bot to every channel the agents may read: `/invite @agents` in the channel.

Prompt injection: an agent that reads channels with outside members (Slack Connect) can be steered by them. Keep `slack.addMessage` off unless the agent needs to post, and limit it with `slack.addMessageChannels`.
