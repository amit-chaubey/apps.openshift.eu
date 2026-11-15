# oh-my-openagent

## Purpose

Runs OmO Native, the coding agent harness of [oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) (npm package `omo-ai`), for one person in a browser terminal behind an OpenShift login. OmO has no web interface or network API of its own: it is a terminal program, and its daemon and RPC mode listen on Unix sockets only. The chart serves the terminal with ttyd.

One pod holds:

- `terminal`: ttyd on 127.0.0.1, serving one tmux session that runs `omo` in `~/<omo.workdir>`. Every browser tab attaches to the same session, so closing a tab stops no work. When `omo` exits, the session drops to a shell; `omo` starts it again.
- `oauth-proxy`: the OpenShift login in front of the terminal; the only port in the Service.
- `token-proxy` (with `gateway.upstream` and `gateway.auth: serviceAccountToken`): on 127.0.0.1, adds the pod's ServiceAccount token (audience: the host of `gateway.upstream`) to every model call, so no key is stored.

The image holds node 24, ttyd, tmux, git, ripgrep, fd and a few shell tools, and no OmO. On start the container installs `omo.package@omo.version` from `omo.registry` into `~/.local/lib/omo/<version>` once, removes other versions, and links `omo` into `~/.local/bin`. The first start therefore takes a few minutes and needs the npm registry; later starts reuse the installed version. A changed `omo.version` installs the new version on the next start.

On every start the container also writes:

- `~/.omo/agent/models.json`: the gateway as one OpenAI-compatible provider named `gateway.provider` with `gateway.models`.
- `~/.omo/omo.jsonc`: telemetry and the device service off, `model_profile` pinned to `<gateway.provider>/<gateway.defaultModel>`, merged with `omo.config`.

`~/.omo/agent/settings.json` (default provider and model) is written on the first start only, so choices made in OmO survive a restart. OmO's MCP identity key `~/.omo/agent/config-identity.key` is set back to mode 0600 on every start, as the volume's fsGroup change makes it group-writable. The environment turns off usage reports and update checks.

The home directory (`/home/omo`) is the volume `<release>-home`: OmO, its sessions, memory and logins, and the work tree. It is kept when the release is removed.

With `builds.enabled` (the default) the chart builds the terminal image in the project from `files/Containerfile` with a BuildConfig, and the pod waits for the first build. With `terminal.image` it uses a prebuilt image and builds nothing.

## Install

```bash
helm repo add apps https://apps.openshift.eu
helm install omo apps/oh-my-openagent \
  --set route.host=omo.apps.example.com \
  --set oauth.ownerEmail=me@example.com \
  --set gateway.upstream=https://api.example.com \
  --set gateway.provider=example \
  --set 'gateway.models={my-model,my-small-model}'
```

- `team`, `tenant` (default: empty): with `team`, every object goes into `<tenant>-<team>`. Empty: the release namespace.
- `person.email`, `person.name` (default: empty): git author of the terminal; `person.email` is also the owner when `oauth.ownerEmail` is empty.
- `route.host` (default: empty): hostname of the terminal. Empty: OpenShift assigns one.
- `route.annotations` (default: cert-manager `ClusterIssuer` `letsencrypt`): used when a host is set.
- `route.timeout`, `route.tunnelTimeout` (default: `3600s`, `24h`): router timeouts; the terminal is one WebSocket.
- `oauth.ownerEmail` (default: empty): OpenShift user name of the only person admitted. Empty: `oauth.allowedGroups`; both empty: everyone who may edit the project.
- `oauth.allowedGroups`, `oauth.loginClusterRole` (default: empty): groups admitted, through a ClusterRole with verb `login` on `services/proxy` or the chart's own Role.
- `secrets.type` (default: `kubernetes`): `externalSecret` reads each secret from the password of a vault item in `secrets.externalSecret.storeName`: `secrets.externalSecret.items.cookieSecret` (default `<release>-cookie-secret`) and, with `gateway.auth: apiKey`, `items.gatewayApiKey` (default `<release>-gateway-api-key`).
- `secrets.cookieSecret` (default: empty): oauth-proxy cookie secret; empty: generated once. Under Argo CD pass it, or it changes on every sync. A value, or a vault password, that is not 16, 24 or 32 characters long is hashed to 32.
- `gateway.upstream` (default: empty): origin of the OpenAI-compatible gateway; without it OmO gets no provider. Required with `gateway.auth: apiKey`.
- `gateway.path` (default: `/v1`): path of the OpenAI API.
- `gateway.auth` (default: `serviceAccountToken`): `apiKey` sends `secrets.gatewayApiKey` instead of the ServiceAccount token.
- `gateway.audience` (default: empty, the host of `gateway.upstream`): audience of the token.
- `gateway.provider` (default: `gateway`): provider name in OmO; a model is `<provider>/<model id>`.
- `gateway.models` (default: empty): model ids, or `{id, name, contextWindow, maxTokens, reasoning, input}`.
- `gateway.defaultModel` (default: empty, the first of `gateway.models`).
- `omo.package`, `omo.version`, `omo.registry` (default: `omo-ai`, `5.1.70`, `https://registry.npmjs.org/`): what the container installs.
- `omo.config` (default: empty): keys merged into `~/.omo/omo.jsonc` at every start.
- `omo.settings` (default: empty): keys merged into the first `~/.omo/agent/settings.json`.
- `omo.env` (default: empty): extra environment of the terminal.
- `omo.workdir` (default: `work`): directory below HOME the session opens in.
- `terminal.image` (default: empty): prebuilt terminal image; empty: the build of `builds.enabled`.
- `terminal.port`, `terminal.fontSize` (default: 7681, 14): ttyd's loopback port and font size.
- `oauthProxy.image`, `oauthProxy.port` (default: the cluster's `openshift/oauth-proxy:v4.4` image stream tag, 4180).
- `tokenProxy.image`, `tokenProxy.port` (default: UBI 9 python 3.12 minimal, 8099).
- `builds.enabled`, `builds.baseImage`, `builds.registry`, `builds.nodeSelector` (default: true, UBI 10 node 24, the internal registry, none). The image is x86_64 only.
- `storage.size`, `storage.className`, `storage.accessMode` (default: `10Gi`, default class, `ReadWriteOnce`).
- `terminal.resources`, `oauthProxy.resources`, `tokenProxy.resources`, `builds.resources`: per container.
- `networkPolicy.enabled` (default: true): only the router reaches the login proxy port.

## How to login

Open `https://<route.host>/` and log in with OpenShift. The owner (`oauth.ownerEmail`) or a member of `oauth.allowedGroups` is admitted; everyone else gets 403. The page is a terminal with OmO running in it. Type a task at the prompt; `/model` lists the gateway's models.

## Delivery instructions

- The address of the terminal: `oc get route <release> -o jsonpath='https://{.spec.host}/{"\n"}'`.
- The first start installs OmO from npm; follow it with `oc logs deploy/<release> -c terminal`. The pod is ready when ttyd listens.
- `oc exec deploy/<release> -c terminal -- bash -lc 'cd ~/work && omo -p "say hello"'` runs one agent turn against the configured model from the pod.
- OmO is licensed under the Sustainable Use License 1.0 (fair-code): use for internal business purposes, and free distribution, are allowed; selling OmO itself or a hosted OmO service is not. Check that a delivery fits before charging for it.
