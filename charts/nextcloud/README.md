# Nextcloud

## Purpose

Nextcloud for files, calendar and contacts, with OnlyOffice for editing documents in the
browser. The chart runs Nextcloud on the upstream Nextcloud chart, a MariaDB database
through the platform's MariaDB operator, and an OnlyOffice document server. Around that
it runs Redis, the push server for the clients (notify_push) and the whiteboard backend.
A Mailu mail server runs in the same project, and every Nextcloud user with an address
in its mail domain finds a mailbox with that address in the Mail app. Team folders (the app `groupfolders`) are installed at the first start.
Nextcloud Talk with its signaling, TURN and recording servers, an AI assistant on an
OpenAI-compatible API, a database backup to object storage and monitoring are optional.
For the project's agents the chart runs three MCP servers in the same project and publishes
them on the Nextcloud host: the Nextcloud MCP server (the `mcp-nextcloud` chart), an Office
documents MCP server (the `mcp-officeagent` chart) and a live editing MCP server (the
`mcp-onlyoffice-live` chart).

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install nextcloud grncloud/nextcloud \
      --set nextcloud.nextcloud.host=cloud.example.org \
      --set mailu.upstream.domain=example.org \
      --set mailu.upstream.initialAccount.domain=example.org \
      --set 'mailu.upstream.hostnames={mail.example.org}'

Install one release per project. The first start takes a few minutes. Read the first
password:

    oc get secret nextcloud-admin -o jsonpath='{.data.password}' | base64 -d

| Value | Default | Meaning |
|---|---|---|
| `nextcloud.nextcloud.host` | `nextcloud.example.org` | Hostname of Nextcloud. Set it. |
| `onlyoffice.enabled` | `true` | Document editing. Nextcloud is connected to the document server at its start. |
| `onlyoffice.agentBridge.enabled` | `true` | Serve the plugin `agent-bridge` from the document server, through which `mcp-onlyoffice-live` edits documents live. |
| `onlyoffice.editFormats` | `[odt, ods, odp, rtf, txt, csv]` | Formats OnlyOffice opens for editing besides `docx`, `xlsx` and `pptx`. Saving them may lose formatting the format cannot hold. `doc`, `xls` and `ppt` cannot be edited in place: the editor converts them to a `docx`, `xlsx` or `pptx` copy. |
| `onlyoffice.lockFiles` | `true` | Enable the app `files_lock`, so a file open in OnlyOffice cannot be overwritten by a WebDAV write (`423 Locked`). `false` stops enabling it; the app stays enabled until you disable it. |
| `nextcloud.persistence.nextcloudData.size` | `25Gi` | Space for user files. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `secrets.adminPassword` | empty | Password of the first administrator. Empty: generated. |
| `secrets.generator.enabled` | `false` | The External Secrets Operator makes the credentials Secrets and generates their empty passwords once. |
| `global.publicAddress.shared` | `false` | Share one public address between the public Services of the project. See Delivery instructions. |
| `mailu.enabled` | `true` | Run a Mailu mail server in the same project. Its values are those of the `mailu` chart, under `mailu`. |
| `mailu.upstream.domain` | `example.org` | Mail domain of the mail server, normally the domain of the users' login email addresses. Set it. |
| `mailu.upstream.initialAccount.domain` | `example.org` | Domain of the first mail admin account, `admin@<domain>`. Same value as the mail domain. |
| `mailu.upstream.hostnames` | `[mail.example.org]` | Hostnames of the mail server; the first one is its name towards other mail servers and the target of the MX record. Set it to `mail.<mail domain>`. |
| `mail.enabled` | `true` | With `mailu.enabled`: install the Mail app and give every user with an address in a mail domain a mailbox with that address, see Mail for every user. |
| `mail.domains` | `[]` | Domains hosted on the mail server. Empty: `mailu.upstream.domain`. Only domains you own, never a public mail provider. |
| `settings.apps` | `[groupfolders]` | Store apps installed and enabled at every start. `groupfolders` is Team folders. |
| `hsts.enabled` | `true` | Send the Strict-Transport-Security header `hsts.header` on every hostname of this chart. |
| `settings.maintenanceWindowStart` | `1` | Hour (UTC) at which the heavy daily jobs start. Empty: not set. |
| `settings.defaultPhoneRegion` | empty | Country code for phone numbers without one, such as `NL`. Empty: not set. |
| `settings.repairOnUpgrade` | `true` | Once per Nextcloud version: update the store apps (a major upgrade disables apps without a compatible release), add missing database indices and run the expensive repair steps. |
| `settings.setupTimeout` | `300` | Seconds the setup script may take at a start. A step that fails or does not finish is skipped and logged as `setup: skipped`; it runs again at the next start. |
| `cache.enabled` | `true` | Redis for file locking and cache. Always runs when notify_push, the whiteboard or the worker is on. |
| `notifyPush.enabled` | `true` | Push server for the desktop and mobile clients, under `/push`. |
| `whiteboard.enabled` | `true` | Backend of the whiteboard app, under `/whiteboard`. |
| `mcp.route.enabled` | `true` | Publish each MCP server on the Nextcloud host at `/mcp/<server>`, see MCP servers on the Nextcloud host. |
| `mcp.route.timeout` | `300s` | Longest wait of the router for an MCP server's response. |
| `mcp.route.labels`, `mcp.route.annotations` | empty | Extra labels and annotations of the MCP Routes, for example a label of the platform. |
| `mcp-nextcloud.enabled` | `true` | Run the Nextcloud MCP server for agents in the same project. Its values are those of the `mcp-nextcloud` chart, under `mcp-nextcloud`. |
| `mcp-nextcloud.networkPolicy.ingressNamespaces` | the router | Namespaces (label selectors) that may call the Nextcloud MCP server besides the own project. |
| `mcp-officeagent.enabled` | `true` | Run the Office documents MCP server for agents in the same project, only with `onlyoffice.enabled`. Its values are those of the `mcp-officeagent` chart, under `mcp-officeagent`. |
| `mcp-officeagent.service.labels` | empty | Extra labels on the MCP server's Service, for example the exposure label of an MCP gateway. |
| `mcp-officeagent.networkPolicy.ingressNamespaces` | the router | Namespaces (label selectors) that may call the MCP server besides the own project: the router for the Route, and for example the namespace of an MCP gateway. |
| `mcp-officeagent.auth.enabled` | `true` | Check the caller's bearer token before the MCP server, so it can be exposed beyond the project. |
| `mcp-officeagent.auth.issuerUrl` | empty | OpenID Connect issuer of the callers' tokens, set once by the platform for every instance. Empty: every call but the health paths is refused. |
| `mcp-officeagent.auth.audiences` | the release namespace | A token must be issued for one of these. The default is the instance's OpenID Connect client named after its project; set `oidc.clientId` here when it differs. |
| `mcp-officeagent.auth.allowedGroups` | `<tenant>-admins` | A token must carry one of these groups. The default is the administrators group of the tenant, the part of the project name before the first `-`: `acme-admins` in `acme-nextcloud`. |
| `mcp-officeagent.auth.resourceUrl` | empty | Public address of the server, `https://<host>/mcp/officeagent`, named in its protected resource metadata so agents can discover the issuer. |
| `mcp-officeagent.auth.cookieSecret`, `mcp-officeagent.auth.clientSecret` | generated once | Secrets the token check requires; a GitOps tool that renders without cluster access passes them. |
| `mcp-onlyoffice-live.enabled` | `true` | Run the live editing MCP server for agents in the same project, only with `onlyoffice.enabled`. Its values are those of the `mcp-onlyoffice-live` chart, under `mcp-onlyoffice-live`. |
| `mcp-onlyoffice-live.service.labels` | empty | Extra labels on the live editing server's Service, for example the exposure label of an MCP gateway. |
| `mcp-onlyoffice-live.networkPolicy.ingressNamespaces` | the router | Namespaces (label selectors) that may call the live editing server besides the own project. |
| `taskprocessing.enabled` | `false` | Worker that runs queued tasks at once. Always runs when `ai.enabled`. |
| `ai.enabled` | `false` | Assistant and the OpenAI integration app. Also runs the worker and Redis. |
| `ai.url` | empty | Address of the OpenAI-compatible API, ending in `/v1`. Required with `ai.enabled`. |
| `secrets.aiApiKey` | empty | Key of that API. Required with `ai.enabled`; never generated. |
| `oidc.enabled` | `true` | Login with an OpenID Connect provider (app `user_oidc`) once `oidc.discoveryUri` and `oidc.clientId` are set; local logins keep working. |
| `oidc.discoveryUri` | empty | The provider's `/.well-known/openid-configuration`. |
| `oidc.clientId` | empty | Client at the provider; its secret is `secrets.oidcClientSecret` (required, never generated). |
| `oidc.groupsClaim` | `groups` | Claim with the user's groups; groups matching `oidc.groupsRegex` are created and kept in step. |
| `oidc.groupsRegex` | empty | Only users with a matching group may log in, for example `^(admin\|acme-admins)$`. A group `admin` in the claim makes the user an administrator. Empty: no restriction. |
| `oidc.bearer` | `true` | API and WebDAV calls with a Bearer token of the provider; the user is created on the first call. |
| `sso.enabled` | `false` | Login through a client the platform makes. The Route carries `sso.routeLabels`; the platform answers with Secret `nextcloud-sso` (keys `sso-client-id`, `sso-client-secret`, `sso-discovery-uri`, `sso-groups-regex`), and within a minute Nextcloud sets up provider `oidc.identifier` from it with the claims of `oidc`. Switched off, or the Secret removed, the provider goes. |
| `sso.routeLabels` | empty | Labels on the Route that ask the platform for the client. |
| `mailRoute.sso.enabled` | `true` | With OpenID Connect set up and `secrets.type` `kubernetes`: Webmail logs in at the provider, without the mailbox password. The client needs the redirect URI `https://<host>/mail/login/oauth2/callback`. |
| `ai.models.completion` | empty | Default model for text. Also `image`, `speechToText`, `textToSpeech`. Empty: the choice of the app. |
| `ai.providers.*` | text kinds on | Kinds of task the endpoint serves: `chat`, `text`, `translation`, `imageAnalysis`, `imageGeneration`, `speechToText`, `textToSpeech`. |
| `talk.enabled` | `false` | Nextcloud Talk with a signaling server, NATS, a TURN server and call recording. |
| `talk.turn.host` | empty | Public DNS name of the TURN server. Empty: `turn-<hostname of Nextcloud>`. |
| `talk.turn.public` | `false` | Request a public address for the TURN server: LoadBalancer Service, DNS record request and NetworkPolicy. |
| `talk.turn.externalIP` | empty | Public address of the TURN server. Empty with `talk.turn.public`: read from the DNS name when the pod starts. |
| `talk.turn.service.type` | `ClusterIP` | Service type when `talk.turn.public` is off. |
| `talk.turn.service.annotations` | empty | Annotations of the TURN Service, for example the address pool. |
| `talk.turn.tls.enabled` | `false` | TURN over TLS on port 5349 with a certificate for `talk.turn.host`. |
| `talk.recording.enabled` | `true` | Call recording, when Talk is on. |
| `backup.enabled` | `false` | Dump of the database to an S3 bucket. |
| `backup.storageClassName` | empty | StorageClass of the bucket claim. Required for the backup. |
| `backup.schedule` | `0 2 * * *` | Time of the backup. Empty: one backup at once. |
| `monitoring.enabled` | `false` | Exporters, ServiceMonitors and alert rules for the monitoring of your project. |

A default install requests 1.46 CPU and 5.1Gi of memory, within a quota of 4 CPU and 8Gi
of requests. Each part can be switched off on its own:

| Part | Value | CPU | Memory |
|---|---|---|---|
| Nextcloud, database, Redis, push, whiteboard, preview server | always | 0.50 | 1.2Gi |
| Mailu mail server with webmail | `mailu.enabled` | 0.53 | 2.2Gi |
| OnlyOffice document server | `onlyoffice.enabled` | 0.25 | 0.75Gi |
| Nextcloud MCP server | `mcp-nextcloud.enabled` | 0.05 | 0.25Gi |
| Office documents MCP server | `mcp-officeagent.enabled` | 0.03 | 0.16Gi |
| Live editing MCP server | `mcp-onlyoffice-live.enabled` | 0.10 | 0.5Gi |

Without OnlyOffice and the three MCP servers an install requests 1.03 CPU and 3.4Gi. Talk
adds 0.36 CPU and 0.45Gi, AI 0.05 CPU and 0.25Gi (the worker), monitoring 0.04 CPU and
0.13Gi.

Secrets left empty are generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD or `helm template`, cannot read the existing Secrets and generates new values on
every render: set every secret of an enabled component there. A changed database
password after the first install stops the database. Use letters and digits only.

The credentials are three Secrets, each with the keys `username` and `password`:
`nextcloud-admin` (first administrator), `nextcloud-mariadb` (database user `nextcloud`)
and `nextcloud-mariadb-root` (`password` only). The MariaDB database reads only the two
`nextcloud-mariadb` Secrets. An upgrade from 0.5 or older takes the passwords over from the
former Secret `nextcloud-credentials`. MariaDB does not allow its root password Secret to
change, so such a release keeps the root password in `nextcloud-credentials` (key
`mariadb-root-password`) instead of `nextcloud-mariadb-root`. `helm upgrade` detects that
itself; with Argo CD or `helm template` set `database.legacyRootSecret=true`.

With `secrets.generator.enabled` the External Secrets Operator generates each empty
password in these Secrets once (Password generator, `refreshPolicy: CreatedOnce`), so they
stay the same under Argo CD as well. Labels and annotations for `nextcloud-admin` go in
`secrets.generator.secretTemplate`. Uninstalling the release deletes the Secrets together
with their ExternalSecrets.

A script configures Nextcloud each time its pod starts: it installs and connects the
apps of the enabled components (OnlyOffice with its document server and JWT secret, Mail,
push server, whiteboard, Talk, AI), installs the apps of `settings.apps` and applies the
other values under `settings`. With `onlyoffice.enabled=false` it disables the app
`onlyoffice`. It writes only these settings; other settings and apps you change in
Nextcloud stay as they are. An app of an enabled component that you disable in Nextcloud
is enabled again at the next start: switch the component off in the values instead.

After you switch a component on or off with `helm upgrade`, restart Nextcloud and the
worker:

    oc rollout restart deployment/nextcloud
    oc rollout restart deployment/nextcloud-taskprocessing

The second command applies only with `ai.enabled` or `taskprocessing.enabled`.

## Mail for every user

With `mailu.enabled` and `mail.enabled` (both default) a Nextcloud user whose email
address is in one of the mail domains finds a mailbox with exactly that address in the
Mail app. For a user who logs in with OpenID Connect that is the login email, which
`user_oidc` stores in Nextcloud; the user needs no Nextcloud password. Once a minute the
cron container of the Nextcloud pod gives each new user:

1. A mailbox with the user's email address on the Mailu server, made through the Mailu
   API with the token of the Secret `mailu-secrets` and a random password nobody uses.
2. An app token of that mailbox, valid only from the networks in `mailu.upstream.subnet`
   (inside the cluster).
3. A Mail account with that address as email and IMAP user, on the mail front end
   `mailu-front`: IMAP on port 10143 and SMTP on port 10025, the ports Mailu keeps for
   webmail inside the project.

The mail domains are `mail.domains`, by default only `mailu.upstream.domain`. The
script adds each of them to Mailu. List the domains you own, the normal case is the
domain of your company. Mail for a domain arrives only when its `MX` record points at
this mail server, see Delivery instructions. Never list a domain of a public mail
provider such as `gmail.com`: Mailu treats every domain it hosts as local, so mail from
this server to anyone at that provider would stay here instead of leaving.

These users get no mailbox; the log of the container `nextcloud-cron` names each of
them once (`mail: <user>: no mailbox, <reason>`):

- A user without an email address. The user is tried again once an address is set.
- A user whose address is in another domain, for example a private address. Tried
  again when the address changes.
- A user whose address belongs to a mailbox that exists already and was not made for
  that user.

A user is handled once; the result is a file per user in `<data directory>/.chart-mail`.
A user who got a mailbox `<user id>@<mail domain>` from chart 0.14.0 keeps it; it is not
changed or deleted. Users read their mail in Nextcloud. With OpenID Connect set up, the menu
entry Webmail opens `https://<host>/mail/login`: it logs the user in at the provider and
opens the Mailu webmail as the mailbox of the user's email, without a password. Mailu takes
that login only from its own front end, under a header name derived from
`secrets.mailLoginSecret`. Without OpenID Connect the webmail (`https://<host>/mail/inbox/`)
asks for the mailbox password, which a user sets in the Mailu admin interface after an
administrator sets a first one there.

## Office documents for agents

The `mcp-officeagent` server (OfficeAgent.NET) creates, reads and edits Word, PowerPoint
and Excel files itself, headless. It does not use the OnlyOffice document server and has
no access to the files in Nextcloud. Its Service carries the agentgateway discovery keys
(`appProtocol: agentgateway.dev/mcp`, `agentgateway.dev/mcp-path: /`,
`agentgateway.dev/mcp-target-name: officeagent`), so an MCP gateway that selects Services
by these keys publishes it with tools named `officeagent_*`. Its documents claim
`officeagent-documents` survives deleting the Application, as the Nextcloud volumes do.

An agent that holds both this server and a Nextcloud MCP server (WebDAV tools) edits a
document of a Nextcloud user in four steps:

1. Read the file with the Nextcloud MCP server.
2. Import it into the `session` connection with `officeagent_import_document_content`
   and edit it there.
3. Export the result with `officeagent_export_document_content`.
4. Write it back to the same path with the Nextcloud MCP server.

Nextcloud keeps the previous content as a version of the file, so a user restores it
under Versions in the file's details.

The server checks every caller's token (`mcp-officeagent.auth.enabled`, on by default): a
bearer JWT of `mcp-officeagent.auth.issuerUrl` issued for this instance, by default the
OpenID Connect client named after the project, from a member of the tenant's administrators
group (`mcp-officeagent.auth.allowedGroups`). Without a token the answer is 401, from
another group 403. Until the issuer is set the server refuses every call but its health
paths; Nextcloud itself is not affected.

A file that is open in OnlyOffice is overwritten by the editor's next save, so step 4
fails with `423 Locked` while anyone has the file open (`onlyoffice.lockFiles`). Edit such
a file live instead.

## Live editing for agents

The `mcp-onlyoffice-live` server opens a document in OnlyOffice as the calling agent, in a
headless browser, and joins the document's co-editing session. People who have the
document open see the agent's name, its cursor and each edit as it happens; comments and
tracked changes carry the agent's name. Tools, named `office-live_*` behind a gateway:
`open`, `create`, `read`, `insert` (also word by word), `replace`, `comment`,
`track_changes`, `save`, `view`, `close`, `sessions`, `file_status`.

- The server holds no secret. It forwards the caller's `Authorization` to Nextcloud: the
  agent's own OpenID Connect token (with `oidc.bearer`) or Basic with the agent's app
  password. Nextcloud decides which files the agent may open.
- It edits through the plugin `agent-bridge`, which the document server serves from the
  ConfigMap `onlyoffice-agent-bridge`.
- It calls Nextcloud on the Service `nextcloud` and opens editor pages on the public
  address in the ConfigMap `nextcloud-public-url`.
- At most 4 documents are open at once, 2 per account; a session idle for 15 minutes is
  closed.

A second MCP server for live co-editing in the open document may be added to this chart
later.

## MCP servers on the Nextcloud host

With `mcp.route.enabled` (default) every enabled MCP server gets a Route on the host of
Nextcloud, without a certificate of its own: the certificate of the Route `nextcloud`
covers the host. The router rewrites the path to the server's own MCP path and waits
`mcp.route.timeout` for an answer.

| Server | Address | Route | Rewritten to | Caller's credential checked by |
|---|---|---|---|---|
| `mcp-nextcloud` | `https://<host>/mcp/nextcloud` | `mcp-nextcloud` | `/mcp` | Nextcloud (`user_oidc`) |
| `mcp-officeagent` | `https://<host>/mcp/officeagent` | `mcp-officeagent` | `/` | the token check: issuer, audience, group |
| `mcp-onlyoffice-live` | `https://<host>/mcp/office-live` | `mcp-office-live` | `/mcp` | Nextcloud (`user_oidc`) |

With an issuer set, the Route `mcp-officeagent-metadata` also serves the officeagent's
protected resource metadata at its well-known address,
`https://<host>/.well-known/oauth-protected-resource/mcp/officeagent`. The NetworkPolicy
of each server admits the router (`network.openshift.io/policy-group: ingress`).

An agent in another namespace or cluster reaches a server with its own OpenID Connect
token. For an agent chart that routes tokens by the `mcp.openshift.eu/auth` annotations
(the `openhands` chart), the agent's namespace holds one RemoteMCPServer per server, with
`<host>`, `<issuer>` and the namespace filled in:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer
metadata:
  name: nextcloud
  namespace: <agent namespace>
  annotations:
    mcp.openshift.eu/auth: oidc
    mcp.openshift.eu/oidc-issuer: <issuer>
spec:
  description: Nextcloud files, notes, calendar and contacts
  url: https://<host>/mcp/nextcloud
  protocol: STREAMABLE_HTTP
---
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer
metadata:
  name: officeagent
  namespace: <agent namespace>
  annotations:
    mcp.openshift.eu/auth: oidc
    mcp.openshift.eu/oidc-issuer: <issuer>
spec:
  description: Word, PowerPoint and Excel documents
  url: https://<host>/mcp/officeagent
  protocol: STREAMABLE_HTTP
---
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer
metadata:
  name: office-live
  namespace: <agent namespace>
  annotations:
    mcp.openshift.eu/auth: oidc
    mcp.openshift.eu/oidc-issuer: <issuer>
spec:
  description: Live co-editing of documents in ONLYOFFICE
  url: https://<host>/mcp/office-live
  protocol: STREAMABLE_HTTP
```

The agent's token must name this instance's OpenID Connect client in `aud`, so Nextcloud
and the token check accept it, and carry the group of `mcp-officeagent.auth.allowedGroups`
for the officeagent.

## Nextcloud versions

Nextcloud upgrades one major version at a time and never downgrades. Each chart release
that changes the major version moves exactly one major further, so an instance upgrades by
taking every such release in order:

| Chart | Nextcloud |
|---|---|
| 0.1.0 to 0.7.0 | 33 |
| 0.8.0, 0.8.1 | 34 |
| 0.9.0 | 35 |

An instance that skips a release with a new major does not start: the image refuses the
jump (`It is only possible to upgrade one major version at a time`) and leaves the data
untouched. Install the skipped release first. A new install starts at the latest release.

## Delivery instructions

- DNS records. A hostname under the platform's application domain needs nothing: the
  platform's DNS follows the Routes and the public Services by itself. For your own
  domain, create these records at your DNS provider. `<host>` is
  `nextcloud.nextcloud.host`. Read `<ingress>` with
  `oc get route nextcloud -o jsonpath='{.status.ingress[0].routerCanonicalHostname}'`.

  | Type | Name | Value | Needed for |
  |---|---|---|---|
  | `CNAME` | `<host>` | `<ingress>` | Nextcloud and every component on its paths: OnlyOffice, Talk signaling, push, whiteboard, mail admin and webmail |
  | `A` | `turn-<host>` (or `talk.turn.host`) | address of `oc get service nextcloud-turn` | Talk across networks, with `talk.turn.public` |
  | `A` | `mail.<mail domain>` | address of `oc get service mailu-front-ext` | Mail |
  | `MX` | `<mail domain>` | `10 mail.<mail domain>.` | Mail. Requested with `mailu.dns.enabled`; a DNS provider that does not write `MX` records leaves it to you. |
  | `TXT` | `<mail domain>` | `v=spf1 mx -all` | Mail (SPF). With `mailu.egressService.enabled` it also names the outgoing address, see the `mailu` chart. |
  | `TXT` | `dkim._domainkey.<mail domain>` | record shown in the mail admin interface under Mail domains, Details | Mail (DKIM). Written by the platform with `global.dkim.managed=true`. |
  | `TXT` | `_dmarc.<mail domain>` | `v=DMARC1; p=quarantine` | Mail (DMARC) |

  A name that is the zone itself (`example.org`) cannot be a `CNAME`: use your
  provider's `ALIAS` record, or an `A` record with the address `<ingress>` resolves to.
  Create the records before you switch the hostname: each certificate is issued once
  its name resolves here. The mail rows are explained in the Delivery instructions of
  the `mailu` chart, with `mailu.mailService.public=true`, `mailu.dns.enabled` and
  `global.dkim.managed`.
- Public addresses. Talk and mail each take one address of the load balancer. With
  `global.publicAddress.shared=true` the public Services that can share one address do
  so; they then use `externalTrafficPolicy: Cluster` and no longer see the address of
  their client. The mail Service always keeps an address of its own.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` for the components you enable in your vault collection,
  each with the value in the password field, and set `secrets.externalSecret.storeName`
  to your store.
- Mail needs DNS records and a public address: see the Delivery instructions of the
  `mailu` chart. Set `mailu.enabled=false` when you send mail through another server.
- Mail admin. The first mail administrator is `admin@<mail domain>`. Its password is
  generated once and kept in the Secret `mailu-secrets`, key `initial-account-password`,
  never in a mail. Read it in the console under
  `/k8s/ns/<project>/secrets/mailu-secrets` with Reveal values, or with
  `oc get secret mailu-secrets -o jsonpath='{.data.initial-account-password}' | base64 -d`.
  Sign in at `https://<host>/mail/admin/`, in Nextcloud the menu entry Mail admin (group
  `admin` only, with `mailRoute.externalSites`). Change the password there after the
  first sign-in; the Secret keeps the first one. Webmail is `https://<host>/mail/inbox/`,
  menu entry Webmail.
- Each further domain in `mail.domains` needs the same `MX`, SPF and DMARC records as
  the mail domain, pointing at the same mail server, before its mail arrives here. Its
  DKIM key is made in the mail admin interface under Mail domains (`global.dkim.managed`
  covers `mailu.upstream.domain` only).
- Talk works inside one network without more. For calls across networks the TURN server
  needs a public address:
  1. Ask the platform team for the annotation that selects the public address pool and
     set it in `talk.turn.service.annotations`.
  2. Set `talk.turn.public=true`. The Service then requests an address and asks the
     platform's DNS for a record of `talk.turn.host` with it. A name under the
     platform's application domain works without a request. For your own domain,
     read the address with `oc get service nextcloud-turn` and create the A record
     yourself.
  3. The TURN server reads its public address from that DNS name when it starts and
     restarts itself when the name changes. Until the name resolves it runs without a
     public address, so calls inside the cluster's network work and the pod is ready.
     Set `talk.turn.externalIP` only to override this.
  4. The Service and a NetworkPolicy open TCP and UDP port 3478 and the UDP ports
     `talk.turn.relayPorts.min` to `max`. Ask the platform team to allow them when a
     firewall sits in front of the pool.
  5. The signaling hostname needs a DNS record like the other hostnames.
- AI needs an OpenAI-compatible API that your project can reach, and its key. The
  chart runs no model. Switch off the kinds under `ai.providers` that the API does
  not serve.
- The backup covers the database only. It is a logical dump to an S3 bucket that the
  chart claims with an ObjectBucketClaim; ask the platform team which StorageClass to set
  in `backup.storageClassName`. The bucket gets a generated name. The chart reads the
  name after the claim is bound, so run `helm upgrade` a second time with the same
  values: that run creates the Backup. Check it with
  `oc get backups.k8s.mariadb.com`. User files and the Nextcloud volume are not in this
  backup. Ask the platform team which backup covers the volumes of your project.
- Monitoring creates ServiceMonitors and a PrometheusRule in your project. A project
  admin may create these where the platform runs monitoring for user workloads; the
  metrics and alerts then show under Observe in the console of your project.
- The Office documents MCP server answers the own project. For agents behind an MCP
  gateway in another namespace, add that namespace to
  `mcp-officeagent.networkPolicy.ingressNamespaces` and set the gateway's exposure label,
  if it has one, in `mcp-officeagent.service.labels`. Set
  `mcp-officeagent.enabled=false` when the project's agents need no Office documents.
- The live editing server `office-live` is published the same way, with
  `mcp-onlyoffice-live.networkPolicy.ingressNamespaces` and
  `mcp-onlyoffice-live.service.labels`. The gateway must pass each agent's own credential
  (its OpenID Connect token, or its app password) to the server; give every agent its own
  Nextcloud account, so people see who edits. Set `mcp-onlyoffice-live.enabled=false` when
  the project's agents do not edit documents live.
