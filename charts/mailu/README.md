# Mailu

## Purpose

Mailu is a mail server: SMTP, IMAP and POP3, webmail (Roundcube), a spam filter (rspamd)
and an admin interface. This chart wraps the upstream chart `mailu` 2.6.3 and adds what a
project on OpenShift needs: a Route for webmail, a public Service for the mail protocols,
NetworkPolicies, a spam filter configuration and, on request, a certificate, monitoring
and a page to copy mailboxes from another server (imapsync).

The upstream chart is included under the alias `upstream`. Every value of the upstream
chart is set below `upstream:`, for example `upstream.domain`. A chart that includes this
chart as `mailu` sets the same value as `mailu.upstream.domain`.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install mailu grncloud/mailu \
      --set upstream.domain=example.org \
      --set upstream.initialAccount.domain=example.org \
      --set 'upstream.hostnames={mail.example.org}'

Read the webmail address and the password of the first account (`admin@<domain>`):

    oc get route mailu -o jsonpath='https://{.spec.host}{"\n"}'
    oc get secret mailu-secrets -o jsonpath='{.data.initial-account-password}' | base64 -d

| Value | Default | Meaning |
|---|---|---|
| `upstream.domain` | `example.org` | Mail domain, the part after the @. |
| `upstream.initialAccount.domain` | `example.org` | Domain of the first admin account. Same value as the mail domain. |
| `upstream.hostnames` | `[mail.example.org]` | Hostnames of the mail server. The first one goes in the MX record. |
| `upstream.persistence.size` | `20Gi` | Size of the one volume that holds all mail and state. |
| `upstream.persistence.storageClass` | empty | Empty: the default StorageClass. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the four secrets from a secret store. |
| `secrets.initialAdminPassword` | empty | Password of the first admin account. Empty: generated. |
| `mailService.public` | `false` | `true` gives the mail protocols a public address (LoadBalancer Service). |
| `dns.enabled` | `false` | Ask the platform's DNS (external-dns) for the records of the mail domain. See Delivery instructions. |
| `global.dkim.managed` | `false` | `true`: cert-manager makes the DKIM signing key. Set it at install. |
| `global.dkim.secretTemplate` | `{}` | Labels and annotations of the Secret with that key. A platform that publishes the DKIM record recognises the Secret by them. |
| `dns.spf`, `dns.dmarc` | `v=spf1 mx -all`, `v=DMARC1; p=quarantine` | Content of the SPF and DMARC records. |
| `global.publicAddress.shared` | `false` | Let the other public Services of the project share one address. The mail Service never shares. |
| `mailService.annotations` | `{}` | Annotations of that Service, for example the address pool. |
| `mailService.ports.*` | mail ports on | Ports of that Service. `http` and `https` are off. |
| `egressService.enabled` | `false` | Second public address, used as the source of outgoing mail. |
| `route.hostname` | empty | Hostname of webmail. Empty: OpenShift assigns one. |
| `certificate.enabled` | `false` | Request a certificate for `upstream.hostnames` from `certificate.issuerName`. |
| `monitoring.enabled` | `false` | ServiceMonitors and alert rules. Needs the right to create them. |
| `monitoring.alerts` | `true` | `false` keeps the ServiceMonitors and leaves out the alert rules, for a platform that alerts centrally. |
| `imapsync.enabled` | `false` | Page to copy mailboxes from another mail server. |

Web access goes through the Route: the router ends TLS and sends plain HTTP to the mail
front end. Webmail is at `/webmail`, the admin interface at `/admin`. To serve the web
pages on the mail address itself, set `mailService.ports.http` and
`mailService.ports.https` to true.

Without `certificate.enabled` the mail ports use a certificate of the cluster's internal
service CA. Mail clients and other mail servers do not trust it. Set `certificate.enabled`
before real use.

Secrets left empty are generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD or `helm template`, cannot read the existing Secret and generates new values on
every render: set `secrets.secretKey`, `secrets.apiToken`, `secrets.roundcubeKey` and
`secrets.initialAdminPassword` there.

All pods that share the mail volume run on one node, because the volume is ReadWriteOnce.
The default requests add up to about 0.5 CPU and 2.2Gi of memory.

Install one release per project: the object names start with `mailu`, not with the
release name.

## Delivery instructions

The platform team does these steps for you. Ask for them in one request and name your
project, the mail domain and the hostname of the mail server.

- Public address. Set `mailService.public=true`. The mail Service gets a public IP address
  only from the public address pool. Ask which annotation selects that pool and set it,
  for example `--set 'mailService.annotations.metallb\.io/address-pool=<pool>'`. Read the
  address with `oc get service mailu-front-ext`.
- Inbound port 25. Ask the platform team to confirm that port 25 from the internet
  reaches the public range. Without it you can send mail but not receive it.
- Outgoing address. Outgoing mail must leave from a public address with reverse DNS. Ask
  whether the platform sends the mail of your pods from the address of the mail Service.
  If it does not, set `egressService.enabled=true` with the same pool annotation; outgoing
  mail then leaves from the address of `mailu-postfix-egress`.
- DNS records. `<mail host>` is the first of `upstream.hostnames`, `<domain>` is
  `upstream.domain`, `<address>` is shown by `oc get service mailu-front-ext`.

  | Type | Name | Value |
  |---|---|---|
  | `A` | `<mail host>` | `<address>` |
  | `MX` | `<domain>` | `10 <mail host>` |
  | `TXT` | `<domain>` | `v=spf1 mx -all` (SPF). Add `ip4:<outgoing address>` when mail leaves from another address. |
  | `TXT` | `dkim._domainkey.<domain>` | The record shown in the admin interface under Mail domains, Details (DKIM). Press Generate keys first, unless `global.dkim.managed=true`. |
  | `TXT` | `_dmarc.<domain>` | `v=DMARC1; p=quarantine; rua=mailto:postmaster@<domain>` (DMARC) |
  | `CNAME` | `autoconfig.<domain>` | `<mail host>`. Optional; also set `mailService.ports.http` and `https` to true. |
  | `PTR` | `<outgoing address>` | `<mail host>`. Set by the owner of the address: ask the platform team. |

  Your own DNS zone: create these records at your DNS provider. Start with `A` and wait
  until it resolves, then `MX`; mail for the domain arrives here from that moment.

  A zone the platform serves: set `dns.enabled=true` and `global.dkim.managed=true`. The
  `A` record then follows the Service, SPF and DMARC are requested with a DNSEndpoint,
  and the DKIM record follows the signing key that cert-manager keeps in the Secret
  `mailu-dkim`. The `MX` record is not written by the platform: ask the platform team
  for `MX <domain> 10 <mail host>.` and check with `dig MX <domain>`. Do not press
  Generate keys with a managed key: the key file is read-only.
- Shared address. With `global.publicAddress.shared=true` the public Services of the
  project that can share one address do so, and switch to `externalTrafficPolicy:
  Cluster`. The mail Service keeps its own address: behind a shared address every
  sending server would appear with the address of a cluster node, which defeats the
  spam checks and makes the server relay for anyone.
- Admin pod. The upstream chart always adds the capability `NET_BIND_SERVICE` to the
  admin container and the container must start as root. The `anyuid` SCC allows root but
  no added capability. Ask the platform team to confirm that the admin pod can run in
  your project before you rely on this chart.
- Webmail hostname. A `route.hostname` outside the default application domain needs a DNS
  record pointing at the platform's ingress.
- Certificate. Ask for the name of the issuer and set `certificate.enabled=true` and
  `certificate.issuerName`. The certificate is issued once the DNS records exist.
- Monitoring. `monitoring.enabled=true` needs the role `monitoring-edit` in your project.
- With `secrets.type=externalSecret`, create four items in your vault collection, named
  as in `secrets.externalSecret.items`, each with the value in the password field, and
  set `secrets.externalSecret.storeName` to your store.
