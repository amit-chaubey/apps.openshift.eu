# mkdocs

## Purpose

Builds a documentation site from a Git repository with MkDocs and serves it. The build runs in your project; a new commit on the branch is live within a quarter of an hour, or within minutes of the push with a webhook. Optionally only users who log in at an OIDC issuer see the site.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install mkdocs grncloud/mkdocs --set source.gitRepo=https://example.com/you/docs.git
```

Without `source.gitRepo` the chart builds an example site.

Read the address:

```bash
oc get route mkdocs -o jsonpath='https://{.spec.host}{"\n"}'
```

- `source.gitRepo` (default: empty): Repository with a `mkdocs.yml`. Empty: an example site.
- `source.gitRef` (default: main): Branch or tag.
- `source.contextDir` (default: empty): Directory that holds `mkdocs.yml`. Empty: the root.
- `source.requirementsFile` (default: empty): pip requirements file. Empty: `mkdocs-material`.
- `source.strict` (default: true): Fail the build on a broken internal link.
- `source.private`, `source.gitUsername`, `secrets.gitPassword`: Login of a private repository.
- `source.pollSchedule` (default: every 10 minutes): How often to look for a new commit. The same job restarts the site after a build; with an empty schedule run `oc rollout restart deployment/mkdocs` after a build yourself.
- `source.webhook` (default: false): Let the Git server start a build on a push. An image trigger restarts the site after the build, so set `source.pollSchedule` to empty.
- `secrets.webhookSecret` (default: empty): Secret part of the webhook address. Empty: generated once.
- `route.hostname` (default: empty): Hostname of the site. Empty: OpenShift assigns one. Set it at install; a tenant cannot change the hostname of an existing Route.
- `route.labels`, `route.annotations` (default: empty): Extra labels and annotations on the Route, for example to select it for a router shard or a platform policy.
- `oauth.enabled` (default: false): Put the site behind a login.
- `oauth.issuerUrl`, `oauth.clientId`, `secrets.clientSecret`: Your OIDC issuer and client. Required with `oauth.enabled`.
- `oauth.allowedGroups` (default: empty): Only these groups get in. Empty: every user of the issuer.
- `oauth.extraArgs` (default: empty): Further oauth2-proxy flags.
- `secrets.type` (default: kubernetes): `externalSecret` reads the secrets from a secret store.

A commit whose build fails is not built again; the site keeps serving the last good build. Push a fix to build again.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the platform's ingress. Ask the platform team for the record; the certificate follows automatically once it resolves.
- A login needs an OIDC client with the redirect address `https://<hostname>/oauth2/callback`. Ask the platform team for a client at the platform's issuer, or use your own issuer.
- To restrict by group, the issuer must put the user's groups in the token claim named by `oauth.groupsClaim`.
- With `source.webhook`, add a push webhook in your Git server. The address is `https://<cluster API>/apis/build.openshift.io/v1/namespaces/<project>/buildconfigs/mkdocs/webhooks/<secret>/<gitlab, github or generic>`; read the secret with `oc get secret mkdocs-webhook -o jsonpath='{.data.WebHookSecretKey}' | base64 -d`. The Git server must be able to reach the cluster API.
- With `source.webhook` under a GitOps tool, let the tool ignore the image of the container `mkdocs` in the Deployment: the image trigger sets it. In an Argo CD Application that is an `ignoreDifferences` entry for that field together with the sync option `RespectIgnoreDifferences=true`.
- With `secrets.type=externalSecret`, create the items named in `secrets.externalSecret.items` in your vault collection, each with the value in the password field, and set `secrets.externalSecret.storeName` to your store. The cookie secret item must hold 16, 24 or 32 characters; the webhook secret item letters and digits only.
