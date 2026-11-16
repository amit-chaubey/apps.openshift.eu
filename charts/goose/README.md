# goose

## Purpose

Runs one personal [goose](https://github.com/aaif-goose/goose) agent in your project, reachable three ways:

- **Browser terminal**: `goose session` in a terminal (ttyd) behind the OpenShift login. Each browser tab starts its own session.
- **goose Desktop**: `goose serve` (ACP over HTTPS and WebSocket) on its own Route. goose Desktop connects with the generated secret key.
- **A2A**: other agents of your tenant send A2A messages; every A2A `contextId` is its own goose session, so a caller continues the conversation it started.

Models come from an OpenAI-compatible gateway (`gateway.upstream`), which the agent calls with its own ServiceAccount token, so no API key is stored anywhere.

One pod holds these containers:

- `goose` runs `goose serve` on port 3284. Every ACP call needs the secret key; `/status` answers without one.
- `terminal` runs ttyd on 127.0.0.1 with `terminal.command`; only the login proxy reaches it. ttyd is a static binary that an init container downloads and checks against `terminal.ttyd.sha256`.
- `oauth-proxy` (OpenShift oauth-proxy) logs people in to the terminal through the cluster OAuth server, with the agent's ServiceAccount as OAuth client.
- `token-proxy` on 127.0.0.1:8099 adds the projected ServiceAccount token to every model call. goose uses its `openai` provider against it.
- `a2a-bridge` serves A2A v0.3 on port 8090 and runs each message as a goose turn over ACP on loopback. It admits a caller's bearer JWT of the tenant only.

Choices:

- The goose home (`/home/goose`: config, sessions, memory, extensions) lives on one volume, shared by all containers. The Deployment uses `strategy: Recreate`, so the volume can be ReadWriteOnce.
- Sessions run in mode `auto` (`llm.mode`): goose runs tools without asking, because an A2A caller cannot answer a question. Tools run inside the pod, under the restricted SCC.
- Telemetry and the system keyring are off. Extensions and other settings that you add with `goose configure` in the terminal are kept on the volume.
- With kagent installed, the chart registers a BYO kagent Agent named like the release, whose relay pod forwards to the A2A Service.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install goose grncloud/goose \
  --set oauth.ownerEmail=sam@example.com \
  --set route.host=goose-sam.apps.example.com \
  --set desktop.host=goose-desktop-sam.apps.example.com \
  --set gateway.upstream=https://api.example.com \
  --set llm.model=my-model
```

Read the addresses and the secret key for goose Desktop:

```bash
oc get route goose goose-desktop -o jsonpath='{range .items[*]}https://{.spec.host}{"\n"}{end}'
oc extract secret/goose-secret-key --to=-
```

- `team` (default: empty): Team of the tenant the app serves. Every object goes into `<tenant>-<team>`, a namespace the team already has. Empty: the release namespace.
- `tenant` (default: empty): Tenant the app belongs to; required with `team`.
- `image.repository`, `image.tag`, `image.digest` (default: `ghcr.io/aaif-goose/goose`, `1.59.2`, empty): The goose image; a digest pins it.
- `gateway.upstream` (default: empty): OpenAI-compatible gateway; goose calls `<upstream>/v1/chat/completions`. Required for models; without it the token proxy is left out.
- `gateway.audience` (default: the host of `gateway.upstream`): Audience of the ServiceAccount token.
- `llm.model` (default: empty): Model name on the gateway (`GOOSE_MODEL`).
- `llm.contextLimit` (default: 0): Context window in tokens (`GOOSE_CONTEXT_LIMIT`); 0 leaves it to goose.
- `llm.mode` (default: `auto`): `auto`, `approve`, `smart_approve` or `chat`. A2A turns need `auto`.
- `llm.env` (default: empty): Extra goose environment variables, name to value; they override the chart's.
- `route.host` (default: empty): Host of the terminal. Empty: OpenShift assigns one. Set it at install; a tenant cannot change the host of an existing Route.
- `route.labels`, `route.timeout` (default: empty, 3600s), `route.annotations` (default: cert-manager issuer, used when a host is set): Apply to both Routes.
- `terminal.enabled` (default: true): The browser terminal, its login proxy and Route.
- `terminal.command` (default: `goose session`): Command in each terminal tab, for example `bash` to run several goose commands.
- `terminal.ttyd.url`, `terminal.ttyd.sha256` (default: ttyd 1.7.7 for x86_64): Static ttyd binary and its checksum. For arm64 nodes use `ttyd.aarch64` and its checksum from the ttyd release.
- `desktop.enabled` (default: true), `desktop.host` (default: empty): `goose serve` on Route `<release>-desktop`. Disabled, goose listens on loopback only and only the A2A bridge reaches it.
- `oauth.ownerEmail` (default: empty): The only user admitted to the terminal. Empty: `oauth.allowedGroups`; both empty: everyone who may edit the project.
- `oauth.allowedGroups` (default: empty): OpenShift groups admitted. The chart grants them the verb `login` on `services/proxy` of the Service, which `admin` and `edit` do not hold, and the login proxy checks that permission. With an owner, the owner must also be in one of them.
- `oauth.loginClusterRole` (default: empty): ClusterRole holding the verb `login` on `services/proxy`; `oauth.allowedGroups` are bound to it instead of to the chart's own Role. An installer without that permission (a project admin, the project's Argo CD) needs this value.
- `secrets.secretKey`, `secrets.cookieSecret` (default: empty): Secret key of `goose serve` and login cookie key, generated once and kept on upgrade.
- `secrets.type` (default: kubernetes): `externalSecret` reads both from a secret store.
- `a2a.enabled` (default: true): The A2A bridge and Service `<release>-a2a`.
- `a2a.agentName`, `a2a.description` (default: `<release>.<namespace>`, "goose agent"): Agent card.
- `a2a.gatewayNamespace` (default: empty): Namespace of the gateway's HTTPRoutes that may publish this agent; the chart adds ReferenceGrant `<release>-a2a` to the kagent Service `<release>`.
- `a2a.turnTimeout` (default: 3600): Seconds one A2A turn may run.
- `a2a.tenantAuth.tenant` (default: the release namespace up to its first `-`): Callers are admitted from namespaces `<tenant>` and `<tenant>-*` (ServiceAccount tokens) or with a groups claim `<tenant>` or `<tenant>-*` (OIDC tokens).
- `a2a.tenantAuth.kubernetesIssuer` (default: `https://kubernetes.default.svc`), `a2a.tenantAuth.oidcIssuers` (default: empty): Trusted issuers. Signatures must be RS256, RS384 or RS512.
- `a2a.kagent.enabled` (default: true): Registers the BYO kagent Agent when the cluster serves `kagent.dev/v1alpha2`.
- `resources`, `terminal.resources`, `oauthProxy.resources`, `tokenProxy.resources`, `a2a.resources`: Requests and limits per container (goose: 100m CPU and 512Mi requested).
- `storage.size`, `storage.accessMode`, `storage.className` (default: 10Gi, ReadWriteOnce, the default class): The goose home. It is kept when the release is removed.
- `serviceAccount.labels` (default: empty): Labels on the agent's ServiceAccount.
- `networkPolicy.enabled` (default: true): Admits the terminal and goose Desktop from the router only, and A2A from anywhere; the bridge checks every caller.
- `nodeSelector`, `affinity` (default: empty): Pod placement.

## How to login

- **Single sign-on**: the terminal uses the OpenShift login of the cluster, through an OAuth proxy (`oauth.*`).
- **goose Desktop**: the secret key in Secret `<release>-secret-key`, key `secret-key`. In goose Desktop open Settings, goose Server, enable "Use external server", enter `https://<desktop host>` and the key. Leave the certificate fingerprint empty: the Route's certificate is renewed every few months.
- **A2A**: the caller's own bearer JWT of the tenant; no credential is stored.

## Delivery instructions

- The gateway in `gateway.upstream` must accept ServiceAccount tokens of your project with the audience `gateway.audience` and answer the OpenAI chat completions API under `/v1`.
- The pod needs outbound HTTPS to download ttyd from GitHub at every start, unless `terminal.enabled` is false. Mirror the binary and set `terminal.ttyd.url` where GitHub is unreachable.
- Treat the secret key like a password: anyone with it can run tools in the pod through goose Desktop. Rotate it by deleting Secret `<release>-secret-key` and upgrading the release, then restart the pod.
- With `secrets.type=externalSecret`, set `secrets.externalSecret.storeName` and create one vault item per secret, named `<release>-<secret-key, cookie-secret>` or as set in `secrets.externalSecret.items`, with the value in the password field. The cookie secret item must hold 16, 24 or 32 characters.
- goose writes the A2A context map to `/home/goose/.local/share/goose/a2a-contexts.json`. Deleting it starts new sessions for every caller.
