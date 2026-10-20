# openwebui

## Purpose

Runs one personal Open WebUI chat for one person in your project. The person logs in with their OpenShift account and lands in Open WebUI as its admin; nobody else is admitted. With `team.enabled` the instance serves a team instead: every member of `oauth.allowedGroups` logs in with their OpenShift account and gets their own Open WebUI account. Models come from an OpenAI-compatible gateway (`gateway.upstream`), which the instance calls with its own ServiceAccount token, so no API key is stored anywhere.

One pod holds three containers:

- `openwebui` listens on 127.0.0.1:8080 only, so nothing reaches it except through the login proxy;
- `oauth-proxy` (OpenShift oauth-proxy) logs the person in through the cluster OAuth server with the instance ServiceAccount as OAuth client and passes the user's email in `X-Forwarded-Email`, which Open WebUI trusts for its own login (`WEBUI_AUTH_TRUSTED_EMAIL_HEADER`); the proxy replaces any such header a client sends;
- `token-proxy` on 127.0.0.1:8099 adds the projected ServiceAccount token to every model call to `gateway.upstream`. Open WebUI points at `http://127.0.0.1:8099/v1` with a placeholder key.

Choices:

- Signup is off and the first user is the admin: the owner, the only email the proxy admits. With `team.enabled` the proxy admits every group member, Open WebUI creates an account with role `user` at their first login, and the first person to log in becomes admin.
- Chats, uploads, settings (SQLite) and the embedding model cache live on one volume. The Deployment uses `strategy: Recreate`, so the old pod stops before the new one starts and the volume can be ReadWriteOnce.
- Ollama, telemetry and the version check are off. The local embedding model encodes 64 chunks per batch.
- With `openwebui.persistentConfig` (default true), settings the owner changes in the admin panel survive restarts and win over the values of this chart after the first start.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install sam grncloud/openwebui \
  --set person.email=sam@example.com \
  --set 'oauth.allowedGroups={team-admins}' \
  --set route.host=chat-sam.apps.example.com \
  --set gateway.upstream=https://api.example.com
```

Objects are named `<person.name>-openwebui`. Read the address:

```bash
oc get route sam-openwebui -o jsonpath='https://{.spec.host}{"\n"}'
```

- `person.name` (default: the release name): Name prefix of the objects.
- `person.email` (default: empty): OpenShift user name (an email) of the owner.
- `oauth.ownerEmail` (default: `person.email`): The only user admitted. Both empty: nobody logs in.
- `oauth.allowedGroups` (default: empty): OpenShift groups the owner must be in. The chart grants these groups `get` on `services/proxy` of the Service and the login proxy checks that permission, so a user who holds it through `admin` or `edit` in the project passes as well. Empty: no group check.
- `team.enabled` (default: false): Admits every member of `oauth.allowedGroups`, not only the owner, with role `user` (`DEFAULT_USER_ROLE`). Requires `oauth.allowedGroups`; `oauth.ownerEmail` is not used.
- `team.memberTools` (default: false): Members may write their own tools, which run as Python in the pod, and connect tool servers from their browser. Admins always may.
- `qdrant.url` (default: empty): HTTP address of a Qdrant in reach of the pod, for example `http://qdrant:6333` for chart `qdrant` in the same project. Empty: document vectors stay in the built-in store on the data volume.
- `route.host` (default: empty): Hostname. Empty: OpenShift assigns one. Set it at install; a tenant cannot change the hostname of an existing Route. Also sets `WEBUI_URL`.
- `route.labels`, `route.timeout` (default: empty, 3600s), `route.annotations` (default: cert-manager issuer, used when `route.host` is set).
- `gateway.upstream` (default: empty): OpenAI-compatible gateway; Open WebUI calls `<upstream>/v1`. Required for models.
- `gateway.audience` (default: the host of `gateway.upstream`): Audience of the ServiceAccount token, mounted at `/var/run/secrets/api-token/token`.
- `openwebui.image` (default: `ghcr.io/open-webui/open-webui:v0.12.3`), `openwebui.title` (default: empty), `openwebui.persistentConfig` (default: true).
- `openwebui.env` (default: empty): Extra Open WebUI environment variables, name to value; they override the chart's.
- `openwebui.resources` (default: 100m CPU and 1Gi requested, 3Gi limit).
- `secrets.cookieSecret`, `secrets.secretKey` (default: empty): Login cookie key and Open WebUI session key, generated once and kept on upgrade.
- `secrets.type` (default: kubernetes): `externalSecret` reads both from a secret store.
- `storage.size`, `storage.accessMode`, `storage.className` (default: 10Gi, ReadWriteOnce, the default class): The data volume. It is kept when the release is removed.
- `oauthProxy.image` (default: `openshift/oauth-proxy:v4.4` from the cluster registry): OpenShift keeps that image stream tag current.
- `networkPolicy.enabled` (default: true): Admits the login proxy from the router only.

## Delivery instructions

- People log in through the cluster's OAuth server with their OpenShift account; no login client is needed. The owner's OpenShift user name must be an email address (`oc whoami` after a console login) and equal `oauth.ownerEmail`.
- With `team.enabled`, let the intended admin log in first. Every member of the team calls the gateway with the one ServiceAccount token of the instance, so the gateway sees one caller for the whole team.
- The gateway in `gateway.upstream` must accept ServiceAccount tokens of your project with the audience `gateway.audience` and answer the OpenAI API under `/v1`.
- The first start downloads the embedding model for document search (about 100 MB) from Hugging Face; the pod needs outbound HTTPS. Later starts read it from the volume.
- Open WebUI keeps SQLite on the volume: a block storage class suits it best. ReadWriteMany works too.
- With `secrets.type=externalSecret`, set `secrets.externalSecret.storeName` and create one vault item per secret, named `<person.name>-openwebui-<cookie-secret, secret-key>` or as set in `secrets.externalSecret.items`, with the value in the password field. The cookie secret item must hold 16, 24 or 32 characters.
- Sign out in Open WebUI redirects to the login proxy's `/oauth/sign_out`, which clears the proxy cookie.
