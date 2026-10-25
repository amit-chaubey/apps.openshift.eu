# opencode-agent

## Purpose

Runs one personal OpenCode coding agent for one person in your project. The person works with it in the browser behind a login; other agents call it over A2A (agent-to-agent, port 8080). The agent works in a Git work tree on a persistent volume and acts on your project with its own ServiceAccount, which has the `edit` role in the project only.

The agent is a kagent `Agent` of type `BYO`: kagent renders the Deployment and the A2A Service from it. One pod holds five containers: the A2A adapter `opencode-a2a`, `opencode serve` (OpenCode 2.x, API and web UI) on 127.0.0.1, the OpenShift `oauth-proxy` in front of the UI, a UI proxy on 127.0.0.1 that hands the UI to OpenCode as its user `opencode` with the server password, and a token proxy on 127.0.0.1 that adds the agent's credential to every model and A2A call to `gateway.upstream`.

The OpenCode image carries a command-line toolset, each tool at the newest upstream release when the image is built: `oc`, `kubectl` and `ccoctl` (OpenShift stable channel), `hcp` (multicluster engine), `argocd`, `kn`, `virtctl`, `amtool`, `kubectl-cnpg`, `kubectl-mariadb` (the mariadb-operator binary), `kubectl-mdba` (from the krew index), `helm`, `kustomize`, `glab`, `clusteradm`, `velero`, `restic`, `odf` (built from source; there is no binary release), `operator-sdk`, `kubectl-krew`, `yq`, `uv`/`uvx`, `go`, `terraform`, `packer`, `gh`, `promtool`, `gitleaks`, `rclone`, `opa`, `conftest`, `trivy`, `grype`, `syft`, `kube-linter`, `hadolint`, `shellcheck`, `kubeconform`, `cosign` and `sonar-scanner` (on Alpine's OpenJDK 21, not its bundled glibc JRE); from Alpine `aws` (AWS CLI v2), `node`/`npm`/`npx`, `python3` with `pip` and PyYAML, `make`, `curl`, `jq`, `bash`, `nc`, `git`, `ssh`, `tmux`, `openssl`, `dig`, `rsync`, `psql` and `mariadb`; `bw`, `renovate` and `snyk` from npm (`bw`'s release binary needs glibc; `snyk` needs `SNYK_TOKEN` at run time); and from PyPI, each in its own environment, `mkdocs` (with mkdocs-material), `ansible-core` (with the collections kubernetes.core, community.general, ansible.posix and community.crypto), `hf` (huggingface_hub), `fastapi`, `uvicorn`, `semgrep` and `checkov`. The build runs every tool once as a non-root user and fails when one does not start. There is no C compiler (`g++`); `make` is there.

Local (stdio) MCP servers that an OpenCode project defines, such as `npx -y <package>` or `uvx <package>`, run inside the agent pod with the agent's own rights: its ServiceAccount, its work tree and its network access. npm, npx and uv keep their caches under `HOME` and `XDG_CACHE_HOME` on the work tree volume, so they work for the pod's arbitrary UID. `uvx` uses the image's Python and downloads another Python version into the volume when a package needs one.

The A2A adapter keeps its task store and session bindings in the pod (`emptyDir`), not on the shared volume: during a rolling update the old and the new pod run side by side, and two adapters on one SQLite file over a shared file system fail with "locking protocol". A2A tasks and conversation bindings therefore start empty after a pod restart; OpenCode sessions stay on the volume.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install sam grncloud/opencode-agent \
  --set person.name=sam --set person.email=sam@example.com \
  --set person.role="I am sales" \
  --set route.host=sam.apps.example.com \
  --set 'oauth.allowedGroups={team-admins}' \
  --set gateway.upstream=https://api.example.com
```

Objects are named `<person.name>-agent`. Read the address:

```bash
oc get route sam-agent -o jsonpath='https://{.spec.host}{"\n"}'
```

The UI keeps the list of open projects in the browser, so it starts empty in a new browser. Choose **Add project** and pick the work tree, `~/<repository>`; **New session** works once a project is open. A new repository goes in its own directory under `~`; git trusts every directory on the volume (`safe.directory`), because the volume root is owned by root and the pod runs as an arbitrary UID.

- `person.name` (default: the release name): Name prefix and Git user name.
- `person.email` (default: empty): Git email, and the UI owner when `oauth.ownerEmail` is empty.
- `person.role` (default: empty): One line appended to the agent instructions.
- `route.host` (default: empty): Hostname of the UI. Empty: OpenShift assigns one. Set it at install; a tenant cannot change the hostname of an existing Route.
- `route.labels` (default: empty): Extra labels on the Route only.
- `route.timeout` (default: 3600s): Router timeout; the UI streams.
- `route.annotations` (default: cert-manager issuer): Used when `route.host` is set.
- `oauth.ownerEmail` (default: `person.email`): OpenShift user name of the only person admitted to the UI. Empty: `oauth.allowedGroups`; both empty: everyone who may edit the project.
- `oauth.allowedGroups` (default: empty): OpenShift groups admitted. Only their members log in: the chart grants them the verb `login` on `services/proxy` of the UI Service, which `admin` and `edit` do not hold, and the login proxy checks that permission. With an owner, the owner must also be in one of them.
- `oauth.loginClusterRole` (default: empty): ClusterRole holding the verb `login` on `services/proxy`; `oauth.allowedGroups` are bound to it instead of to the chart's own Role. Creating a Role or binding that grants `login` needs that permission, or `bind` on the ClusterRole: an installer without it (a project admin, the project's Argo CD) needs this value.
- `oauthProxy.image` (default: `openshift/oauth-proxy:v4.4` from the cluster registry): The login proxy. OpenShift keeps that image stream tag on the oauth-proxy of the running release.
- `secrets.serverPassword`, `secrets.a2aToken`, `secrets.cookieSecret` (default: empty): Generated once and kept on upgrade.
- `secrets.type` (default: kubernetes): `externalSecret` reads every secret from a secret store.
- `gateway.upstream` (default: empty): Model and A2A gateway. Required for a working agent.
- `gateway.audience` (default: the host of `gateway.upstream`): Audience of the agent's ServiceAccount token, mounted at `/var/run/secrets/api-token/token`.
- `tokenProxy.agentToken.tokenUrl` (default: empty): Token endpoint of an OpenID Connect realm. Calls under `tokenProxy.agentToken.paths` (default `/mcp`) then carry the agent's own token from a `client_credentials` grant, authenticated with a ServiceAccount token for `tokenProxy.agentToken.audience` (federated client authentication, no stored secret); model and A2A calls keep the ServiceAccount token.
- `serviceAccount.labels` (default: empty): Labels on a ServiceAccount the chart creates (`serviceAccount.name` set), for example the one a platform uses to register it as a client of that realm.
- `a2a.gatewayNamespace` (default: empty): Namespace of the gateway that publishes this agent over A2A. Renders a ReferenceGrant for its HTTPRoutes and admits it in the NetworkPolicy.
- `a2a.allowedHosts`, `a2a.publicUrl`: Inbound Host allowlist and Agent Card address of the adapter.
- `a2a.tenantAuth` (default: on, cluster ServiceAccount issuer only): Besides the static credential, the adapter admits a bearer JWT of the agent's tenant: a ServiceAccount token from a namespace `<tenant>` or `<tenant>-*`, or an OIDC token from one of `a2a.tenantAuth.oidcIssuers` whose `groups` claim holds `<tenant>` or `<tenant>-*`. kagent forwards the token of whoever chats with the agent, so its chat UI reaches the agent this way. `a2a.tenantAuth.tenant` defaults to the release namespace up to its first `-`. Needs adapter `2.0.0+hoolia.2` or later.
- `a2a.peers` (default: empty): Agents this agent may call, as a name or `{name, namespace}` (namespace default: the release namespace). Each is reached at `<gateway.upstream origin>/a2a/<namespace>/<name>/` through the token proxy. With peers and `gateway.upstream` set, the opencode container gets `A2A_PEERS`, `A2A_GATEWAY_URL` and `A2A_PROXY_URL`, which a model plugin can turn into tools (the `@grncloud/opencode-grncloud` plugin registers `a2a_list_agents` and `a2a_call`), and the instructions describe those two tools.
- `git.url`, `git.branch`, `secrets.gitToken`: Repository cloned into `/workspace` on first start, never overwritten. The token goes to the host of `git.url` only.
- `opencode.plugins`, `opencode.npmScopes`, `opencode.env`, `opencode.config`: npm plugins, their registries, extra environment and overrides of `opencode.json`.
- `opencode.disabledProviders` (default: `[opencode]`): Providers that are off by default, written as `disabled_providers`. `opencode` is opencode.ai's public inference, which sends prompts outside your gateway. OpenCode 2.1.1 has no UI switch for providers; a user turns one back on in their own `~/.config/opencode/opencode.json`, which outranks this file: `{"policies": [{"action": "provider.use", "resource": "opencode", "effect": "allow"}]}`.
- `builds.enabled` (default: true): Build the OpenCode image (with the toolset and kubernetes-mcp-server) and the adapter image in your project. When false, set `a2a.image`, and `opencode.image` to an image that carries the toolset and `/tools/kubernetes-mcp-server`.
- `builds.resources` (default: 100m CPU and 256Mi requested, 6Gi limit): The OpenCode image build; compiling `odf` needs several Gi.
- `tools.goImage`, `tools.odfRepository`: Go image and repository the `odf` CLI is built from.
- `builds.nodeSelector`, `nodeSelector`, `affinity` (default: empty): Where the image builds and the agent pod run. The OpenCode image is about 5.7G on disk and every agent builds its own, so keep both on nodes with room for it.
- `storage.size`, `storage.accessMode`, `storage.className` (default: 20Gi, ReadWriteMany, the default class): The work tree volume. It is kept when the release is removed.
- `serviceAccount.name` (default: empty): Empty: `<person.name>-agent`, created by kagent. Another name is created by the chart. Either way it is the OAuth client of the login, with its redirect pointing at the Route.
- `networkPolicy.a2aNamespaces` (default: empty): More namespaces allowed to call A2A on 8080.

OpenCode runs under an exclusive lock on the volume. A new pod starts while the old one still runs; its OpenCode waits for the lock and starts when the old pod stops.

OpenCode 2.x converts the session store `opencode.db` in place on its first start, and OpenCode 1.x cannot read it afterwards. Before OpenCode starts, under the same lock, the chart copies `opencode.db` (with its `-wal` and `-shm` files) to `~/.home/backup/opencode.db.<UTC timestamp>` once: when a copy is already there it makes none. To go back to 1.x, stop the agent, put the copy back in `~/.home/.local/share/opencode/`, and install a 0.2 release of the chart.

## Delivery instructions

- kagent must run on the cluster: the chart renders a `kagent.dev/v1alpha2` `Agent`. To use the kagent UI, add kagent's namespace to `networkPolicy.a2aNamespaces`.
- People log in through the cluster's OAuth server with their OpenShift account; no login client is needed. The owner's OpenShift user name must be an email address (`oc whoami` after a console login) and equal `oauth.ownerEmail`. OpenCode 2.x knows one user, `opencode`; the UI proxy signs in as that user.
- The A2A gateway publishes the agent to other agents. Ask the platform team for the gateway's namespace and set `a2a.gatewayNamespace`; the platform adds the route and copies the Secret `<person.name>-agent-a2a-auth` (key `token`) to the gateway.
- The gateway in `gateway.upstream` must accept ServiceAccount tokens of your project with the audience `gateway.audience`.
- The OpenCode image build downloads from github.com, gitlab.com, mirror.openshift.com, developers.redhat.com, get.helm.sh, the Alpine package mirrors, PyPI and Docker Hub, so the build pod needs egress to them. The adapter image installs `a2a.package` (by default a source archive on github.com) and its dependencies from PyPI.
- The adapter for OpenCode 2.x cannot offer what 2.x removed: the session todo list is empty, and session share, workspaces and worktree reset answer with an error.
- `storage.accessMode` ReadWriteMany needs a StorageClass that offers it, such as a CephFS or NFS class. Set `storage.className` when it is not the default class. ReadWriteOnce and ReadWriteOncePod are rejected: the Agent renders a RollingUpdate Deployment without a strategy field, so the new pod would wait for the old pod to release the volume and the rollout would stall.
- With `secrets.type=externalSecret`, set `secrets.externalSecret.storeName` and create one vault item per secret, named `<person.name>-agent-<server-password, a2a-auth, cookie-secret, git-token>` or as set in `secrets.externalSecret.items`, with the value in the password field. The cookie secret item must hold 16, 24 or 32 characters.
- Agents that share a project can read each other's Secrets and exec into each other's pods. Give an agent its own project when that is not acceptable.
