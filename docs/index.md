# apps.openshift.eu

Helm charts that tenants of an OpenShift platform install into their own projects, and the
labels and annotations that change how those applications behave together. Every page is
built from the [chart repository](https://github.com/hoolia/apps.openshift.eu): a chart's
README, its `values.yaml` and its release notes.

```bash
helm repo add grncloud https://apps.openshift.eu
helm search repo grncloud
```

| Section | What it answers |
|---|---|
| [Conventions](conventions/index.md) | Which label or annotation makes an agent pick up an MCP server, log in with OAuth or reach a server through the MCP gateway |
| [MCP catalog](mcp-catalog.md) | Which MCP servers exist, by category and by bundle |
| [Charts](charts/index.md) | What each chart installs, how to install it, every value with its default, and what changed per version |

## What a chart assumes

The charts target an OpenShift platform with OpenShift Routes and builds, cert-manager,
External Secrets, the MariaDB and CloudNativePG operators, and object storage through
ObjectBucketClaim. They are not portable to an arbitrary Kubernetes cluster. A chart:

- installs into the release namespace and creates no Namespace and no cluster-scoped object;
- runs under the `restricted-v2` security context constraints;
- installs with no values given: every value has a default;
- keeps a generated secret on upgrade when `secrets.type` is `kubernetes`, or reads it from
  a secret store when `secrets.type` is `externalSecret`.

The rules for a new chart or a change to one are in
[CONTRIBUTING.md](https://github.com/hoolia/apps.openshift.eu/blob/main/CONTRIBUTING.md).

## Releases

A chart is released when the `version` in its `Chart.yaml` changes on the `main` branch.
Each GitHub Release carries the package, the release notes shown on the chart's Changelog
page and a CycloneDX SBOM of the chart's images.
