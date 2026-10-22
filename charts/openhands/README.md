# openhands

## Purpose

Runs one shared OpenHands Agent Canvas (frontend, agent-server and automation in one image) in your project, for a team that works with it in the browser behind an OpenShift login. Every conversation runs in the same pod, with the same shell, the same volume and the same ServiceAccount, which has the `edit` role in the project only. OpenHands sub-agents run inside that pod.

One pod holds three containers: `agent-canvas`, the OpenShift `oauth-proxy` in front of it, and a token proxy on 127.0.0.1 that adds the pod's ServiceAccount token to every model and MCP call to `gateway.upstream`. Only the login proxy is in a Service. The frontend listens on 127.0.0.1 behind it and hands the session API key to the logged-in browser, so the backend is configured on the first visit; the agent-server and automation ports still require that key, so a pod in the same project that reaches them directly cannot drive the agent.

With `build.enabled` the chart builds the image in the project from the upstream image. It adds Debian packages (`git`, `jq`, `make`, `ssh`, `curl`, `aws`, `psql`, `mariadb`, `skopeo`, `tmux`, `dig`, `rsync`, Java 21, `pdftotext`) and the Python, npm and pip tools in `build.tools` at their pinned versions. With `build.toolsImage` it also copies every binary in that image's `/usr/local/bin`, its Go toolchain, `/tools`, `/opt/sonar-scanner` and Ansible collections, so the agent gets exactly the command-line toolset of, for example, an `opencode-agent` image. The build runs every tool once as a non-root user and fails when one does not start. The image runs under the `restricted-v2` SCC with an arbitrary UID.

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

- `image.repository`, `image.tag` (default: `ghcr.io/openhands/agent-canvas`, `1.30.0`): Upstream image, and the base of the build.
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
- `llm.model` (default: empty): Default model in LiteLLM form, for example `openai/<model>`.
- `mcpServers` (default: empty): `{name, path, timeout}` per MCP server on `gateway.upstream`.
- `kubernetesMcpServer.enabled` (default: false): Local MCP server `kubernetes`; needs `build.toolsImage` with `/tools/kubernetes-mcp-server`.
- `kubernetesMcpServer.deniedResources` (default: `Secret`): Kinds that server refuses.
- `agentSecrets` (default: empty): Environment of the ACP agents (Claude Code, Codex, Gemini CLI), written into the agent-server secrets store, so the UI does not ask for it. Base URLs point at the token proxy, for example `ANTHROPIC_BASE_URL: http://127.0.0.1:8099/anthropic`. Not for real credentials: the values are in the pod spec.
- `agentProfiles` (default: empty): `name: {acp_server, acp_model}` per ACP agent profile offered under **Switch agent profile**.
- `env` (default: empty): Extra environment for `agent-canvas`.
- `storage.size`, `storage.className`, `storage.accessMode` (default: `20Gi`, default class, `ReadWriteOnce`): Volume for settings, conversations and `~/workspace`.

## Delivery instructions

Open `https://<route.host>/canvas/` and log in with OpenShift; the backend is already connected. The first-run wizard is marked done in the page, so the model and MCP servers seeded from `llm.model` and `mcpServers` are what the UI starts with. In **Settings**, check the model and the MCP servers. Clone repositories into `~/workspace`; git sends the token to `git.host` only, and `glab` uses the same token.

Everyone admitted to the UI uses the same pod, the same ServiceAccount, the same git identity and sees every conversation. The agent's shell sees the git token in its environment and can print it into a conversation, which sends it to the model; give it a token you can rotate. OpenHands adds a `Co-authored-by: openhands` trailer to the commits it writes unless told otherwise.
