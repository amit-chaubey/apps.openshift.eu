# GitLab

## Purpose

A GitLab instance for source code, merge requests and pipelines, run by the GitLab
operator of the platform. The chart creates one `GitLab` custom resource, a PostgreSQL
database from the CloudNativePG operator, a Valkey cache, one object storage bucket for
uploads, artifacts, packages and LFS, and a Route. The container registry, the agent
server, the runner, Git over SSH and the bundled monitoring are off.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install gitlab grncloud/gitlab \
      --set route.hostname=gitlab.example.org \
      --set objectStorage.bucket=gitlab-mycompany-4f7a

Install one release per project and name the release `gitlab`. The first start takes
about ten minutes. Log in as `root` with:

    oc get secret gitlab-credentials -o jsonpath='{.data.root-password}' | base64 -d

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname. GitLab builds every link and clone address from it. |
| `objectStorage.bucket` | empty | Bucket name, unique in the object store. Empty: generated. |
| `objectStorage.storageClassName` | empty | Object StorageClass of the bucket claim. |
| `gitlab.chartVersion` | `10.3.1` | GitLab chart version; one the operator supports. |
| `gitlab.edition` | `ce` | `ce` or `ee`. |
| `gitlab.gitaly.size` | `20Gi` | Volume of the Git repositories. |
| `ssh.enabled` | `false` | Git over SSH. See the delivery instructions. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the passwords from a secret store. |
| `secrets.rootPassword` | empty | Password of `root` at the first start. Empty: generated. |
| `secrets.valkeyPassword` | empty | Password of the cache. Empty: generated once and kept. |
| `gitlab.extraValues` | empty | Values merged over the ones given to the GitLab chart. |
| `mcp.annotations` | MCP path and target name | Annotations on the GitLab Service for MCP gateways that discover MCP servers (GitLab serves MCP at `/api/v4/mcp`). |
| `mcp.labels` | empty | Labels on the GitLab Service, for example the one a tenant-wide MCP endpoint selects. |

The default requests add up to about 0.9 CPU and 4.7Gi of memory; the database
migration at install and upgrade adds 0.25 CPU and 200Mi for a few minutes.

With an empty `objectStorage.bucket` the bucket gets a generated name, and GitLab itself
is created by a second `helm upgrade` once the bucket exists. A tool that renders the
chart without access to the cluster, such as Argo CD, must set the bucket name and both
passwords. The root password is applied at the first start only. The other internal
secrets are generated in the project by the operator and kept on every upgrade.

## Delivery instructions

- The platform must run the GitLab operator so that it watches every project, with a
  version that supports `gitlab.chartVersion`. A new operator version needs a new value.
- Tenants need a security context constraint that allows a fixed user ID together with
  the `runtime/default` seccomp profile: the GitLab containers run as user 1000.
- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress; the certificate follows automatically once the name resolves.
- Git over SSH needs `ssh.enabled=true` and a TCP address (LoadBalancer or NodePort) for
  the Service `gitlab-gitlab-shell`, which a tenant cannot create. Ask the platform
  team; Git over HTTPS works without it.
- Backups with the toolbox need a second bucket named in `gitlab.extraValues`
  (`global.appConfig.backups`). Ask the platform team to set up and test a restore.
- Outgoing mail needs a mail server in `gitlab.extraValues` (`global.smtp`).
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection with the value in the password
  field, and set `secrets.externalSecret.storeName` to your store.
