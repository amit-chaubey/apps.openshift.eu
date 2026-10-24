# kagent

## Purpose

Runs the kagent agent controller and its web UI for one tenant in your project, behind the OpenShift login. Agents (`Agent` objects) in your project, and in the team projects the platform assigns to this controller, are deployed and served by it; the UI lists and chats with them.

The chart wraps the upstream [kagent chart](https://github.com/kagent-dev/kagent) (`upstream.*` values) and keeps everything inside the release namespace:

- `kagent-login` (OpenShift oauth-proxy) logs people in through the cluster OAuth server with its ServiceAccount as OAuth client and forwards to the UI. The Route points at it.
- The UI and controller come from upstream; RBAC is namespaced (`upstream.rbac.namespaces`), never cluster-wide.
- NetworkPolicies admit the login proxy from the router only and the UI from the login proxy only. The upstream UI Route is off (`upstream.ui.route.enabled: false`).
- `login.provider: openshift` (default): the controller runs in auth mode `unsecure` and every user of one instance acts as the same kagent user (`admin@kagent.dev`), so all of them see the same chat sessions. kagent takes a user identity only from a JWT, which the OpenShift login does not issue.
- `login.provider: oidc`: upstream `oauth2-proxy` logs people in against an OIDC provider and the controller runs `trusted-proxy`, so each person has their own sessions. Set `upstream.oauth2-proxy.enabled`, its `extraEnv` (`OIDC_ISSUER_URL`, `OIDC_REDIRECT_URL` = `https://<route.host>/oauth2/callback`, `UPSTREAM_URL` = `http://kagent-ui:8080`) and `extraArgs.allowed-group`, and `upstream.controller.auth.mode: trusted-proxy`; the chart refuses other combinations. The chart sets the scope, the Secret `kagent-oidc` and a login skip list of the login page and static assets only. With `secrets.type: externalSecret` the chart writes that Secret, `kagent-oidc`, from your secret store.

Choices:

- The CRDs, the kmcp controller access, the tool servers of team namespaces and the controller's access to them belong to the platform (kagent-operator), not to this chart: kmcp, kagent-tools and the built-in agents are off.
- With `upstream.kmcp.enabled` (and `upstream.kmcp.rbac.create: false`) the chart adds kmcp's leader-election Role; the platform grants kmcp the rest.
- The controller's database is a CloudNativePG `Cluster` `kagent-postgres` in the release namespace (`database.cnpg`); the controller reads its connection URL from the generated Secret `kagent-postgres-app`. Without the CloudNativePG operator, turn `database.cnpg.enabled` off and use upstream's bundled PostgreSQL (development only) or your own database.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install kagent grncloud/kagent -n team-kagent \
  --set 'upstream.rbac.namespaces={team-kagent}' \
  --set route.host=kagent.apps.example.com
```

With Argo CD, set the namespace from the destination: `spec.source.helm.parameters` `upstream.rbac.namespaces[0]` = `$ARGOCD_APP_NAMESPACE`.

- `upstream.rbac.namespaces` (required): The release namespace. The chart refuses to render without it, because empty gives cluster-wide RBAC.
- `route.host` (default: empty): Hostname. Empty: OpenShift assigns one. Set it at install; a tenant cannot change the hostname of an existing Route.
- `route.labels`, `route.timeout` (default: empty, 3600s), `route.annotations` (default: cert-manager issuer, used when `route.host` is set).
- `login.provider` (default: openshift): `openshift` or `oidc`, see Purpose.
- `login.allowedGroups` (default: empty): OpenShift groups admitted. Only their members log in: the chart grants them the verb `login` on `services/proxy` of the login Service, which `admin` and `edit` do not hold. Empty: everyone who may edit the project.
- `login.loginClusterRole` (default: empty): ClusterRole holding the verb `login` on `services/proxy`; `login.allowedGroups` are bound to it instead of to the chart's own Role. An installer without that permission (a project admin, the project's Argo CD) needs this value.
- `login.image` (default: `openshift/oauth-proxy:v4.4` from the cluster registry), `login.port` (default: 4180), `login.resources`.
- `secrets.cookieSecret` (default: empty): OpenShift login cookie key, generated once and kept on upgrade.
- `secrets.type` (default: kubernetes): `externalSecret` with `login.provider: oidc` writes Secret `kagent-oidc` through an ExternalSecret: `secrets.externalSecret.storeKind`, `storeName`, `refreshInterval`, and `items.oidcClientSecret` and `items.oidcCookieSecret` (remote keys whose `login.password` holds the value). Set `upstream.oauth2-proxy.config.existingSecret: kagent-oidc`.
- `login.oidc.clientId` (default: empty): Client id at the OIDC provider, written into `kagent-oidc`.
- `database.cnpg.enabled` (default: true): CloudNativePG `Cluster` `kagent-postgres` as the controller's database. Off: set `upstream.database.postgres.urlFile` to empty and `bundled.enabled`, `url` or your own `urlFile`, and `upstream.controller.volumes` and `volumeMounts` to `[]`.
- `database.cnpg.instances` (default: 1): One instance has no PodDisruptionBudget, so a node drain restarts it; two or more get one.
- `database.cnpg.storage.size`, `storage.storageClass`, `imageName`, `resources` (default: 1Gi, cluster default, operator default, empty).
- `database.cnpg.operatorNamespaceSelector` (default: `kubernetes.io/metadata.name: openshift-operators`): Namespace of the CloudNativePG operator, admitted to the instances.
- `networkPolicy.enabled` (default: true), `networkPolicy.agentNamespaceSelector` (default: empty): Namespaces whose agent pods reach the controller API on port 8083. `networkPolicy.agentPodSelector` (default: empty, every pod of those namespaces) narrows it, for example to `app.kubernetes.io/managed-by: kagent`. With `route.host` the router also reaches cert-manager's HTTP-01 solver.
- `remoteMCPServers` (default: empty): RemoteMCPServers in the release namespace for MCP servers of your own: `name`, `description`, `url`, `protocol` (`STREAMABLE_HTTP` or `SSE`), `timeout`, `allowedNamespaces` (default `{from: Same}`; `{from: Selector, selector: ...}` lets Agents in other namespaces use it) and `headers`. A header takes its value from `vaultItem` (a remote key of `secrets.externalSecret.storeName`, property `vaultProperty`, default `login.password`, written into Secret `mcp-<name>` behind an optional `prefix` such as `Bearer `) or from `existingSecret` (`name`, `key`).
- `examples.enabled` (default: false): The tool server of the upstream example agents, RemoteMCPServer `kagent-tool-server`, bound to `examples.tools.clusterRole` (default `view`) in the release namespace only. Switch the agents on per agent with `upstream.<agent>.enabled` (for example `k8s-agent`, `helm-agent`); the chart refuses them without `examples.enabled`. Upstream's own `kagent-tools` binds cluster-admin and stays refused.
- `upstream.providers` (default: upstream OpenAI with Secret `kagent-openai`): The default model. Create the API key Secret, or point `upstream.providers.openAI.config.baseUrl` at an OpenAI-compatible gateway.
- `upstream.controller.env` (default: empty): Extra controller environment, for example `WATCH_NAMESPACES` from a pod annotation the platform writes.

### Who gets in

kagent itself checks nobody: in `trusted-proxy` mode the controller decodes the caller's JWT without verifying it and its authorizer allows every request. The login proxy and the NetworkPolicies are the boundary, and inside it everyone may do everything:

| Caller | Path | Admitted when |
|---|---|---|
| Person in a browser | Route, oauth2-proxy, UI, controller | Logged in at the OIDC provider and the claim `groups` holds one of `extraArgs.allowed-group` |
| API client with a bearer token | Route, oauth2-proxy | The token is issued for the client and passes the same `allowed-group` check (oauth2-proxy authorizes bearer sessions like cookie sessions) |
| ServiceAccount, agent pod | Controller Service, port 8083 | The pod runs in a namespace `networkPolicy.agentNamespaceSelector` selects |

A Kubernetes ServiceAccount token carries no `groups`, so it never passes the login proxy; ServiceAccounts reach the controller inside the cluster only. Select the tenant's namespaces in `networkPolicy.agentNamespaceSelector` by a label tenants cannot set themselves. Every person admitted can create, change and delete Agents, ModelConfigs and tool servers in every namespace the controller serves; chat sessions are per person.

Upstream's controller Roles `kagent-writer-role` and `kagent-getter-role` carry wildcard rules that a project admin cannot grant, so the chart renders them with explicit rules within a project admin's rights; the platform's kagent-operator grants the controller its full rules in every namespace of the tenant, the release namespace included.

## Delivery instructions

- `openshift`: people log in through the cluster's OAuth server with their OpenShift account; no login client is needed. `oidc`: register a confidential client with redirect URI `https://<route.host>/oauth2/callback` whose tokens carry the groups that `upstream.oauth2-proxy.extraArgs.allowed-group` names.
- Team projects are served when the platform assigns them: it writes the controller's watch list and binds the controller in each of them. Without that, the controller serves the release namespace only.
- The default `ModelConfig` reconciles once its API key Secret exists.
- Do not add a NetworkPolicy that admits the router to the UI: only the login Route may reach it.
- From 0.4.0 the chart runs upstream kagent 0.10.4: the CRDs of the platform's kagent-operator must be 0.10.4 before the upgrade.
