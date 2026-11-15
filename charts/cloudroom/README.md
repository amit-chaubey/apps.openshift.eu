# cloudroom

## Purpose

Runs one self-hosted Cloudroom core: a service that runs coding agents (Claude Code, OpenCode, Codex) as their own CLIs, keeps every session's queue and history on its volume, and serves one HTTP API protected by a bearer token. The Cloudroom desktop app connects to it with the Route URL and that token. One release is one core; every session of every user of that core shares one agent account and one volume, so give a release to one trusted person or team only. Agents run without approval prompts.

One pod holds:

- `core`: the Cloudroom core and every agent it starts. It runs in the container mode of the upstream disk-safety design (`core/docs/storage.md`, "Containers"): the core runs as the agent account (`uid`, 1001) with no capability, marks each agent's processes instead of using cgroups, and the image's storage policy `/etc/cloudroom/storage.json` names that account. The fixed user id needs an SCC that admits it, such as `anyuid`; the pod asks for nothing else.
- `token-proxy` (with `gateway.upstream`): on 127.0.0.1, adds the pod's ServiceAccount token (audience: the host of `gateway.upstream`) to every model call, so no model key is stored. Only this container gets the token.

The volume holds `/code` (the workspaces), the agent home `/home/cloudroom-agent` (harness logins and settings), the core's history `/var/lib/cloudroom/history`, `/tmp` and the npm cache, each a folder of one claim: the core refuses agent paths on more than one filesystem. The core warns at 5 GB free and pauses agents below 2 GB.

The core serves plain HTTP on `port` (9840). The Route terminates TLS (edge), and with `networkPolicy.enabled` only the router reaches that port. Every request needs the token, health checks included.

At every start the script `start.sh` creates the home folders of the enabled harnesses (the core offers a harness once its CLI and its home exist) and writes the agents' environment to `<state>/environment`: the core starts every agent with a clean environment and reads that file. With `gateway.upstream`:

- Claude Code sends to `<proxy><gateway.anthropicPath>` with a placeholder API key that the token proxy replaces; `gateway.claudeCode.*Model` map its aliases `opus`, `sonnet` and `haiku` to gateway models. The placeholder key wins over a Claude subscription login made in the app.
- OpenCode gets the provider `gateway.opencode.providerId` at `<proxy><gateway.openaiPath>` with `gateway.opencode.models`, in a config file that `OPENCODE_CONFIG` points to; the user's own `~/.config/opencode` stays theirs.
- `OPENAI_BASE_URL` and `OPENAI_API_KEY` point other OpenAI clients at the proxy.

Codex needs a ChatGPT login of its own: with `harnesses.codex.enabled` the user connects it in the app.

The image is built in the project (`builds.enabled`) from `builds.baseImage`, the core release archive `core.version` checked against `core.sha256`, and the harness CLIs at their npm versions. A changed Dockerfile gets a new BuildConfig and image tag; the pod waits for the build.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install cloudroom grncloud/cloudroom \
  --set route.host=cloudroom.apps.example.com \
  --set gateway.upstream=https://api.example.com \
  --set 'gateway.opencode.models={vendor/model}'
```

- `core.version`, `core.sha256`, `core.releaseUrl` (default: 0.1.111, its archive digest, the upstream GitHub releases): the core release the image is built from.
- `core.harness` (default: `claude-code`): harness of a session that names none.
- `core.env` (default: empty): extra environment of the core process.
- `core.agentEnv` (default: empty): extra environment of every agent; wins over the gateway lines.
- `harnesses.claudeCode`, `harnesses.opencode`, `harnesses.codex` (`enabled`, `version`; default: Claude Code 2.1.325 and OpenCode 1.18.34 on, Codex 0.155.1 off): CLIs in the image. `harnesses.codex.model` is required with Codex.
- `gateway.upstream`, `gateway.audience` (default: empty): model gateway and audience of the token. Empty: every harness uses its own login.
- `gateway.anthropicPath`, `gateway.openaiPath` (default: `/anthropic`, `/v1`): API paths on the gateway.
- `gateway.claudeCode.opusModel`, `sonnetModel`, `haikuModel` (default: empty): gateway models of Claude Code's aliases.
- `gateway.opencode.providerId`, `providerName`, `models`, `defaultModel` (default: `gateway`, `Gateway`, empty, the first model): the OpenCode provider. Without models no provider is written.
- `secrets.type` (default: `kubernetes`): `externalSecret` reads the token from the password of vault item `secrets.externalSecret.items.token` (default `<release>-token`) of `secrets.externalSecret.storeName`.
- `secrets.token` (default: empty): the API token, at least 32 characters; empty: generated once and kept. Under Argo CD pass it or use `externalSecret`, or it changes on every sync.
- `route.host`, `route.labels`, `route.timeout`, `route.annotations` (default: empty, empty, `3600s`, cert-manager `ClusterIssuer` `letsencrypt`): the Route; the annotations are used when a host is set.
- `uid` (default: 1001): the agent account; the image is built for it.
- `port` (default: 9840): the core port.
- `resources` (default: 500m and 2Gi requested, 4 CPU and 8Gi limit): the core container, which runs every agent.
- `storage.size`, `storage.accessMode`, `storage.className` (default: 20Gi, `ReadWriteOnce`, default class).
- `builds.enabled`, `builds.registry`, `builds.baseImage`, `builds.packages`, `builds.nodeSelector`, `builds.resources`: the in-project build. With `builds.enabled: false`, `image.repository` and `image.tag` name a prebuilt image of the same Dockerfile.
- `tokenProxy.image`, `tokenProxy.port`, `tokenProxy.resources` (default: UBI 9 Python 3.12 minimal, 8099).
- `networkPolicy.enabled` (default: true): only the router reaches the core port.
- `nodeSelector`, `affinity` (default: empty).

## How to login

There is no single sign-on: the bearer token is the login. It is key `token` of Secret `<release>-token`:

```bash
oc get secret <release>-token -o jsonpath='{.data.token}' | base64 -d
```

In the Cloudroom desktop app (v83 or newer) open **Settings > Cloudroom account > Connect your own Core**, or **Use my own Core** in first-run setup, and enter `https://<route.host>` and the token. Anyone with the token runs agents as the agent account and reads every session.

## Delivery instructions

- The address: `oc get route <release> -o jsonpath='https://{.spec.host}{"\n"}'`.
- The image build: `oc get builds -l app=<release>`; the pod starts once the build pushed its tag.
- Readiness from the pod, without the token on a command line: `oc exec deploy/<release> -c core -- sh -c 'printf "header = \"Authorization: Bearer %s\"\n" "$CLOUDROOM_TOKEN" | curl --config - -s http://127.0.0.1:9840/v1/ready'` answers `{"ready":true}`. `/v1/capabilities` lists the harnesses the core found.
- A wrong token gets 401 at every path.
- Rotate the token: change it in the Secret or the vault item, then `oc rollout restart deploy/<release>`; the core reads it at start. Reconnect the app with the new token.
- Back up the history: scale the Deployment to 0, copy the `history` folder of the claim, scale back to 1. A restart interrupts running work; queued prompts resume.
- A harness login made in the app (Claude subscription, Codex, Cursor) lives in the agent home on the volume and survives restarts and upgrades.
