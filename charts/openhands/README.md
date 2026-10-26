# openhands

## Purpose

Runs one shared OpenHands Agent Canvas (frontend, agent-server and automation in one image) in your project, for a team that works with it in the browser behind an OpenShift login. Every conversation runs in the same pod, with the same shell, the same volume and the same ServiceAccount, which has the `edit` role in the project only. OpenHands sub-agents run inside that pod.

One pod holds three containers: `agent-canvas`, the OpenShift `oauth-proxy` in front of it, and a token proxy on 127.0.0.1 that adds the pod's ServiceAccount token to every model and MCP call to `gateway.upstream`. Only the login proxy is in a Service. The frontend listens on 127.0.0.1 behind it and hands the session API key to the logged-in browser, so the backend is configured on the first visit; the agent-server and automation ports still require that key, so a pod in the same project that reaches them directly cannot drive the agent.

With `build.enabled` the chart builds the image in the project from the upstream image. It adds Debian packages (`git`, `jq`, `make`, `ssh`, `curl`, `aws`, `psql`, `mariadb`, `skopeo`, `tmux`, `dig`, `rsync`, Java 21, `pdftotext`, `rg`, network and process tools), a document and diagram toolset (LibreOffice without GUI as `soffice` for Word, PowerPoint, Excel and Visio import and PDF export, `pandoc`, `graphviz`, `plantuml` with nwdiag, `rsvg-convert`, ImageMagick, `mmdc` for Mermaid on the image's Chromium) and the Python, npm and pip tools in `build.tools` at their pinned versions (by default also `markitdown`, `python-docx`, `docxtpl`, `python-pptx`, `openpyxl`, `vsdx` and `diagrams`). The image sets `SHELL=/bin/bash`, which Claude Code's Bash tool needs. With `build.toolsImage` it also copies every binary in that image's `/usr/local/bin`, its Go toolchain, `/tools`, `/opt/sonar-scanner` and Ansible collections, so the agent gets exactly the command-line toolset of, for example, an `opencode-agent` image. The build runs every tool once as a non-root user and fails when one does not start. The image runs under the `restricted-v2` SCC with an arbitrary UID.

With `mcpDiscovery.enabled` a fourth container, `mcp-sync`, turns every kagent `RemoteMCPServer` in the project, and in every other Project the agent's ServiceAccount may see and list them in, into an MCP server of the agent and keeps that list in step every `mcpDiscovery.interval` seconds. A server on `gateway.upstream` is called through the token proxy with the pod's ServiceAccount token; a server that needs headers from a Secret is skipped. The user's on/off switch of a discovered server is kept. Its card icon is the `openhands.dev/icon` annotation of the `RemoteMCPServer`, else the logo of a known name (`github`, `gitlab`, `grafana`, `netbox`, `librenms`, `kagent-tools`, ...). With `git.host` it also writes the git token into the agent-server secrets store as `git.uiSecretName`, which turns on the UI's repository and merge request features.

With `a2a.enabled` a fifth container, `a2a`, makes the agent an A2A server (protocol 0.3, JSON-RPC with SSE streaming) for other agents and kagent. The instance is the base agent at `/`: every A2A context becomes its own conversation, tagged `a2acontext=<contextId>` so the mapping survives restarts, and built from the persisted settings (model, key, MCP servers). Every titled top-level conversation started in the UI is also a sub-agent named `<release>/<title>`, with its own card and endpoint at `/agents/<slug>/`; a message there continues that conversation, `GET /agents` lists them and the base card names them as skills. Agent cards are public; calls need a bearer token: `a2a.staticTokenSecret`, a ServiceAccount token from a namespace `<tenant>` or `<tenant>-*`, or a token from one of `a2a.tenantAuth.oidcIssuers` whose `groups` claim holds `<tenant>` or `<tenant>-*`. The agent-server stays on loopback; the shim is reached through the Service `<release>-a2a`. With kagent installed and `a2a.kagent.enabled` the chart registers a BYO `Agent` named like the release: kagent runs a BYO agent as its own Deployment on port 8080, so that pod relays to the shim, using the namespace's `default` ServiceAccount. kagent chats as the logged-in user, whose token the relay passes on. Sub-agents are not separate kagent Agents (kagent runs a Deployment per Agent, and Agent names cannot hold `/`); kagent chats with the instance, a conversation per chat.

With `mcpPublish.enabled` the same container publishes every remote MCP server a user adds in the UI to the whole project: it writes an `AgentgatewayBackend` with the server's name and a Secret, owned by that backend, with the headers OpenHands would send. The platform turns the backend into a gateway endpoint and a `RemoteMCPServer`, so the other agents of the project discover it. Removing the server in the UI removes the backend. A server that logs in with OAuth, runs inside the pod or has a local URL stays personal, and so does one whose target the platform refuses; the server's description in the UI says which happened and why. Edits and refused servers are retried every `mcpPublish.resync` seconds. Publishing needs `create` and `delete` on `AgentgatewayBackends` in the ServiceAccount's edit role.

With `kubernetesMcpServer.enabled` the agent also gets a local MCP server `kubernetes` (`/tools/kubernetes-mcp-server`, from the tools image) that acts with the pod's ServiceAccount on its own project and refuses the kinds in `kubernetesMcpServer.deniedResources`.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install team grncloud/openhands \
  --set route.host=openhands.apps.example.com \
  --set 'oauth.allowedGroups={developers}' \
  --set gateway.upstream=https://api.example.com \
  --set git.host=git.example.com --set secrets.gitToken=<token>
```

Objects are named after the release. Read the address (the session API key, for API clients, is the Secret `<release>-session-api-key`):

```bash
oc get route team -o jsonpath='https://{.spec.host}/canvas/{"\n"}'
oc extract secret/team-session-api-key --keys=value --to=-
```

- `team` (default: empty): Team of the tenant the app serves. Every object goes into `<tenant>-<team>`, a namespace the team already has; Argo CD's destination namespace stays empty. Empty: the release namespace.
- `tenant` (default: empty): Tenant the app belongs to; required with `team`.
- `image.repository`, `image.tag` (default: `ghcr.io/openhands/agent-canvas`, `1.33.0`): Upstream image, and the base of the build.
- `build.enabled` (default: true): Build the image with the toolset in the project.
- `build.toolsImage` (default: empty): Image whose binaries, Go toolchain, `/tools`, `/opt/sonar-scanner` and Ansible collections are copied in. Empty: Debian packages and `build.tools` only.
- `build.pullSecret` (default: empty): Pull secret for `build.toolsImage` and the base image.
- `build.tools` (default: see `values.yaml`): `{kind: uv|npm|pip, command, args, check}` per tool; `check` is the command the build runs to prove it starts. A `uv` tool whose command the base image already has is kept.
- `build.output.kind` (default: `ImageStreamTag`): `ImageStreamTag` pushes to an ImageStream in the project; `DockerImage` pushes to `build.output.name` with `build.output.pushSecret`.
- `route.host` (default: empty): Hostname of the UI. Empty: OpenShift assigns one.
- `route.labels` (default: empty): Extra labels on the Route only.
- `route.timeout` (default: 3600s): Router timeout; the UI holds WebSockets open.
- `oauth.ownerEmail` (default: empty): OpenShift user name (an email) of the only person admitted. Empty: `oauth.allowedGroups`; both empty: everyone who may edit the project.
- `oauth.allowedGroups` (default: empty): OpenShift groups admitted. Only their members log in: the chart grants them the verb `login` on `services/proxy` of the UI Service, which `admin` and `edit` do not hold, and the login proxy checks that permission. With an owner, the owner must also be in one of them.
- `oauth.loginClusterRole` (default: empty): ClusterRole holding the verb `login` on `services/proxy`; `oauth.allowedGroups` are bound to it instead of to the chart's own Role. Creating a Role or binding that grants `login` needs that permission, or `bind` on the ClusterRole: an installer without it (a project admin, the project's Argo CD) needs this value.
- `secrets.type` (default: `kubernetes`): `kubernetes` or `externalSecret`. With `externalSecret`, the git token and every secret with an item name come from the store; the others are generated in the project.
- `secrets.sessionApiKey`, `secrets.secretKey`, `secrets.cookieSecret` (default: generated once): Agent-server API key, settings encryption key, login cookie secret.
- `secrets.gitToken` (default: empty): Access token for `git.host`, also used by `glab`. `git` needs `write_repository`; `glab` merge requests need `api`.
- `git.host`, `git.userName`, `git.userEmail` (default: empty): Git host the token is sent to, and the commit identity.
- `gateway.upstream` (default: empty): OpenAI-compatible model and MCP gateway; the ServiceAccount token is its credential.
- `gateway.audience` (default: the host of `gateway.upstream`): Audience of that token.
- `tokenProxy.agentToken.tokenUrl` (default: empty): Token endpoint of an OpenID Connect realm. Calls under `tokenProxy.agentToken.paths` (default `/mcp`) then carry the agent's own token from a `client_credentials` grant, authenticated with a ServiceAccount token for `tokenProxy.agentToken.audience` (federated client authentication, no stored secret); model calls keep the ServiceAccount token.
- `serviceAccount.labels` (default: empty): Labels on the agent's ServiceAccount, for example the one a platform uses to register it as a client of that realm.
- `llm.model` (default: empty): Default model in LiteLLM form, for example `openai/<model>`.
- `llm.models` (default: empty): Further models of the gateway, same form, offered as profiles under **Settings > LLM**.
- `llm.gatewayModels` (default: `true`): Also a profile for every model the gateway lists at `/v1/models`, added at each pod start. A profile that exists is left as it is.
- `llm.connection.name` (default: `Gateway`): Provider connection to `gateway.upstream` listed under **Settings > LLM**; it holds `llm.model` (active) and `llm.models` as profiles. Empty: no connection.
- `llm.connection.provider` (default: `openai`): LiteLLM provider of the gateway's API.
- `skills.marketplaces` (default: empty): Skill marketplaces in Claude Code or OpenHands plugin format, `{name, source, ref, repoPath, autoLoad}` with `source` `github:<owner>/<repo>`; they replace the marketplaces registered in the UI.
- `mcpServers` (default: empty): `{name, path, timeout, icon}` per MCP server on `gateway.upstream`; `icon` is an image URL for its card.
- `mcpDiscovery.enabled` (default: true): MCP servers from the project's `RemoteMCPServer` objects; needs the kagent CRDs, does nothing without them.
- `mcpDiscovery.interval` (default: 60): Seconds between two passes.
- `mcpDiscovery.labelSelector` (default: empty): Only `RemoteMCPServer` objects with these labels.
- `mcpDiscovery.clusterRole` (default: `kagent-remotemcpserver-view`): ClusterRole of the kagent installation that reads `RemoteMCPServer` objects, bound in the release namespace; empty for no binding.
- `a2a.enabled` (default: true): The A2A server sidecar; see Purpose.
- `a2a.port` (default: 8090): Container port of the A2A server.
- `a2a.description` (default: empty): Description on the agent card and in kagent.
- `a2a.staticTokenSecret` (default: empty): Secret with key `token`, a static bearer credential besides the tenant JWTs.
- `a2a.tenantAuth.enabled` (default: true): Admit bearer JWTs of the tenant.
- `a2a.tenantAuth.tenant` (default: empty): The tenant; empty takes the release namespace up to its first `-`.
- `a2a.tenantAuth.kubernetesIssuer` (default: `https://kubernetes.default.svc`): The cluster's ServiceAccount token issuer; empty for none.
- `a2a.tenantAuth.oidcIssuers` (default: empty): OIDC issuers whose tokens carry a `groups` claim.
- `a2a.kagent.enabled` (default: true): Register the agent in kagent as a BYO `Agent`, when the kagent CRDs exist.
- `mcpPublish.enabled` (default: true): Publish the remote MCP servers users add in the UI as `AgentgatewayBackend` objects of the project; does nothing without the agentgateway CRDs.
- `mcpPublish.resync` (default: 600): Seconds between two passes that also compare the stored credentials and retry refused servers.
- `git.uiSecretName` (default: `GITLAB_TOKEN`): Secrets store name of the git token the UI reads; `GITHUB_TOKEN` for a GitHub host, empty to not write it.
- `mcpOAuth.callbackPort` (default: 18765): With `build.enabled` and `route.host`, MCP servers added in the UI that log in with OAuth return to `https://<route.host>/mcp-oauth/callback` (behind the OpenShift login) instead of `http://localhost:<port>/callback`; one login at a time. 0 keeps the upstream localhost redirect.
- `kubernetesMcpServer.enabled` (default: false): Local MCP server `kubernetes`; needs `build.toolsImage` with `/tools/kubernetes-mcp-server`.
- `kubernetesMcpServer.deniedResources` (default: `Secret`): Kinds that server refuses.
- `agentSecrets` (default: empty): Environment of the ACP agents (Claude Code, Codex, Gemini CLI), written into the agent-server secrets store, so the UI does not ask for it. Base URLs point at the token proxy, for example `ANTHROPIC_BASE_URL: http://127.0.0.1:8099/anthropic`. Not for real credentials: the values are in the pod spec.
- `agentProfiles` (default: empty): `name: {acp_server, acp_model}` per ACP agent profile offered under **Switch agent profile**.
- `env` (default: empty): Extra environment for `agent-canvas`.
- `storage.size`, `storage.className`, `storage.accessMode` (default: `20Gi`, default class, `ReadWriteOnce`): Volume for settings, conversations, Claude Code sessions (`~/.claude`) and `~/workspace`.

## Delivery instructions

Open `https://<route.host>/canvas/` and log in with OpenShift; the backend is already connected. The first-run wizard is marked done in the page, so the model and MCP servers seeded from `llm.model` and `mcpServers` are what the UI starts with. In **Settings**, check the model and the MCP servers. Clone repositories into `~/workspace`; git sends the token to `git.host` only, and `glab` uses the same token.

Everyone admitted to the UI uses the same pod, the same ServiceAccount, the same git identity and sees every conversation. The agent's shell sees the git token in its environment and can print it into a conversation, which sends it to the model; give it a token you can rotate. OpenHands adds a `Co-authored-by: openhands` trailer to the commits it writes unless told otherwise.
