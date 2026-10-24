# Nextcloud

## Purpose

Nextcloud for files, calendar and contacts, with OnlyOffice for editing documents in the
browser. The chart runs Nextcloud on the upstream Nextcloud chart, a MariaDB database
through the platform's MariaDB operator, and an OnlyOffice document server. Around that
it runs Redis, the push server for the clients (notify_push) and the whiteboard backend.
Nextcloud Talk with its signaling, TURN and recording servers, an AI assistant on an
OpenAI-compatible API, a database backup to object storage and monitoring are optional.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install nextcloud grncloud/nextcloud --set nextcloud.nextcloud.host=cloud.example.org

Install one release per project. The first start takes a few minutes. Read the first
password:

    oc get secret nextcloud-credentials -o jsonpath='{.data.nextcloud-password}' | base64 -d

| Value | Default | Meaning |
|---|---|---|
| `nextcloud.nextcloud.host` | `nextcloud.example.org` | Hostname of Nextcloud. Set it. |
| `onlyoffice.host` | empty | Hostname of OnlyOffice. Empty: `office-<hostname of Nextcloud>`. |
| `onlyoffice.enabled` | `true` | Document editing. Nextcloud is connected to the document server at its start. |
| `nextcloud.persistence.nextcloudData.size` | `25Gi` | Space for user files. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `secrets.adminPassword` | empty | Password of the first administrator. Empty: generated. |
| `secrets.generator.enabled` | `false` | The External Secrets Operator makes `nextcloud-credentials` and generates its empty passwords once. |
| `smtp.host` | empty | Outgoing mail server. |
| `global.publicAddress.shared` | `false` | Share one public address between the public Services of the project. See Delivery instructions. |
| `mailu.enabled` | `true` | Run a Mailu mail server in the same project. Its values are those of the `mailu` chart, under `mailu`. |
| `hsts.enabled` | `true` | Send the Strict-Transport-Security header `hsts.header` on every hostname of this chart. |
| `settings.maintenanceWindowStart` | `1` | Hour (UTC) at which the heavy daily jobs start. Empty: not set. |
| `settings.defaultPhoneRegion` | empty | Country code for phone numbers without one, such as `NL`. Empty: not set. |
| `settings.repairOnUpgrade` | `true` | Once per Nextcloud version: add missing database indices and run the expensive repair steps. |
| `settings.setupTimeout` | `300` | Seconds the setup script may take at a start. A step that fails or does not finish is skipped and logged as `setup: skipped`; it runs again at the next start. |
| `cache.enabled` | `true` | Redis for file locking and cache. Always runs when notify_push, the whiteboard or the worker is on. |
| `notifyPush.enabled` | `true` | Push server for the desktop and mobile clients, under `/push`. |
| `whiteboard.enabled` | `true` | Backend of the whiteboard app, under `/whiteboard`. |
| `taskprocessing.enabled` | `false` | Worker that runs queued tasks at once. Always runs when `ai.enabled`. |
| `ai.enabled` | `false` | Assistant and the OpenAI integration app. Also runs the worker and Redis. |
| `ai.url` | empty | Address of the OpenAI-compatible API, ending in `/v1`. Required with `ai.enabled`. |
| `secrets.aiApiKey` | empty | Key of that API. Required with `ai.enabled`; never generated. |
| `ai.models.completion` | empty | Default model for text. Also `image`, `speechToText`, `textToSpeech`. Empty: the choice of the app. |
| `ai.providers.*` | text kinds on | Kinds of task the endpoint serves: `chat`, `text`, `translation`, `imageAnalysis`, `imageGeneration`, `speechToText`, `textToSpeech`. |
| `talk.enabled` | `false` | Nextcloud Talk with a signaling server, NATS, a TURN server and call recording. |
| `talk.signaling.host` | empty | Hostname of the signaling server. Empty: `signaling-<hostname of Nextcloud>`. |
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

A default install requests 0.75 CPU and 2.0Gi of memory. Talk adds 0.36 CPU and 0.45Gi,
AI 0.05 CPU and 0.25Gi (the worker), monitoring 0.04 CPU and 0.13Gi.

Secrets left empty are generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD or `helm template`, cannot read the existing Secrets and generates new values on
every render: set every secret of an enabled component there. A changed database
password after the first install stops the database. Use letters and digits only.

With `secrets.generator.enabled` the External Secrets Operator generates the empty
administrator and database passwords in `nextcloud-credentials` (Password generator,
`refreshPolicy: CreatedOnce`), so they stay the same under Argo CD as well. Labels and
annotations for that Secret go in `secrets.generator.secretTemplate`. Uninstalling the
release deletes the Secret together with its ExternalSecret.

A script configures Nextcloud each time its pod starts: it installs and connects the
apps of the enabled components (OnlyOffice, push server, whiteboard, Talk, AI) and
applies the values under `settings`. It writes only these settings; other settings and
apps you change in Nextcloud stay as they are. An app of an enabled component that you
disable in Nextcloud is enabled again at the next start: switch the component off in
the values instead. After you switch a component on or off with `helm upgrade`, restart
Nextcloud and the worker:

    oc rollout restart deployment/nextcloud
    oc rollout restart deployment/nextcloud-taskprocessing

The second command applies only with `ai.enabled` or `taskprocessing.enabled`.

## Delivery instructions

- DNS records. A hostname under the platform's application domain needs nothing: the
  platform's DNS follows the Routes and the public Services by itself. For your own
  domain, create these records at your DNS provider. `<host>` is
  `nextcloud.nextcloud.host`. Read `<ingress>` with
  `oc get route nextcloud -o jsonpath='{.status.ingress[0].routerCanonicalHostname}'`.

  | Type | Name | Value | Needed for |
  |---|---|---|---|
  | `CNAME` | `<host>` | `<ingress>` | Nextcloud, push and whiteboard |
  | `CNAME` | `office-<host>` (or `onlyoffice.host`) | `<ingress>` | OnlyOffice |
  | `CNAME` | `signaling-<host>` (or `talk.signaling.host`) | `<ingress>` | Talk |
  | `A` | `turn-<host>` (or `talk.turn.host`) | address of `oc get service nextcloud-turn` | Talk across networks, with `talk.turn.public` |
  | `A` | `mail.<mail domain>` | address of `oc get service mailu-front-ext` | Mail |
  | `MX` | `<mail domain>` | `10 mail.<mail domain>.` | Mail. Never written by the platform: ask the platform team for a mail domain in a zone of the platform. |
  | `TXT` | `<mail domain>` | `v=spf1 mx -all` | Mail (SPF) |
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
     restarts itself when the name changes. Set `talk.turn.externalIP` only to
     override this.
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
