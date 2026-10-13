# Contributing

A pull request is merged when it meets every rule under "Required". The rules under
"Preferred" make a chart better on OpenShift; a reviewer may ask for them and will not
refuse a pull request over them.

`ci/check.sh` checks most of the required rules. Run it before you open a pull request:

    ci/check.sh

It needs `helm` and Python 3 with PyYAML. `gitleaks` is used when it is installed.

## Required

### Scope of a chart

- A chart installs into the release namespace. It creates no Namespace, no cluster-scoped
  object, no operator, no CRD and no SecurityContextConstraints binding.
- A chart runs under the `restricted-v2` security context constraints: no root user, no
  host path, no host network, no capability besides `NET_BIND_SERVICE`. A container that
  needs a fixed user id sets `runAsUser` on that container only.
- A chart holds no name of a platform, cluster, company or tenant, no domain name and no
  storage class name. Those are values with a neutral default.

### Values

- Every value has a default. `helm install <name> <chart>` with no values gives a working
  application.
- Value names are camelCase. A feature switch is `<feature>.enabled`.
- Every value is described in `values.yaml` by a comment above it.
- An image is pinned by tag or digest. `latest` is not a tag. `appVersion` in
  `Chart.yaml` names the version of the application the chart installs.
- Every container has CPU and memory requests and a memory limit. A default install fits
  in 4 CPU and 8Gi of requests.

### Secrets

- No secret, password, token or key is written in a template or as a default value.
- `secrets.type` is `kubernetes` (default) or `externalSecret`. With `kubernetes`, a
  secret left empty is generated once and read back with `lookup` on every later render,
  so an upgrade never changes it.
- Every generated secret is also a value, so a caller that renders without cluster access
  can pass it.

### Labels

Every object a chart renders from its own templates carries these labels in
`metadata.labels`:

| Label | Value |
|---|---|
| `app` | The component the object belongs to; all objects of one component share it |
| `app.kubernetes.io/name` | The product: the chart name, or `postgresql`, `redis` and so on for a dependency |
| `app.kubernetes.io/component` | The role of the object: `server`, `worker`, `database`, `cache`, `route`, `credentials`, `storage` |
| `app.kubernetes.io/part-of` | The application the component is part of; `.Release.Name` unless the chart draws several groups |
| `app.kubernetes.io/version` | `.Chart.AppVersion` |

- `app`, `app.kubernetes.io/name` and `app.kubernetes.io/component` are literal lines on
  each object, so a template shows what the object is without opening a helper:

      labels:
        app: n8n
        app.kubernetes.io/name: n8n
        app.kubernetes.io/component: worker
        {{- include "n8n.commonLabels" $ | nindent 4 }}

- A value is an expression only where it must equal a name that is one, such as the full
  name of a release.
- One helper per chart, `<chart>.commonLabels`, takes the root context and emits the labels
  that are the same on every object: `app.kubernetes.io/part-of` and
  `app.kubernetes.io/version`. A chart that draws several groups writes
  `app.kubernetes.io/part-of` as a literal on each object and leaves it out of the helper.
- `ci/check.sh` fails when an object lacks one of the five labels.
- The number of an `argocd.argoproj.io/sync-wave` annotation is a quoted literal in the
  template, not a value.
- Labels that select pods are fixed for the life of a release. A selector, the labels of a
  pod template and the labels of a volume claim template never change between two versions
  of a chart: a selector cannot be changed on upgrade, and a changed pod template restarts
  every pod.
- A new label for the console or for reporting goes on the object's own metadata, not on
  the pod template.

### Workloads

- A workload with a ReadWriteOnce volume is a StatefulSet, or a Deployment with
  `strategy: Recreate`.
- Every long-running container has a readiness probe. A liveness probe is set only when a
  restart repairs the failure it detects.
- A change in a ConfigMap or Secret that the application reads at start reaches the pods
  through a checksum annotation on the pod template.
- External access is a Route with the `cert-manager.io/issuer-kind` and
  `cert-manager.io/issuer-name` annotations. A chart renders no Ingress.

### Chart hygiene

- `helm lint` passes with no error.
- `Chart.yaml` has `apiVersion: v2`, a `description`, a `version` in semantic versioning
  and an `appVersion`. A change to a chart raises its `version`: patch for a fix, minor for
  a new value or feature, major for a change that needs an action from the user.
- A named template is prefixed with the chart name, or with `wrapper.` in a chart that
  wraps a dependency, so it cannot collide with a template of a subchart. The label helper
  always carries the chart name; a chart with a dependency checks that no packaged subchart
  defines `<chart>.commonLabels`.
- A dependency is pinned to an exact version. Its packages and `Chart.lock` match
  `Chart.yaml`: `helm dependency build` succeeds on a clean clone.
- A template indents with two spaces and uses `nindent` with `include`, not `template`.
- `README.md` of a chart has exactly the sections Purpose, Install and Delivery
  instructions.

### Upgrades

- `helm upgrade` from the previous released version, with the same values, changes no
  generated secret and restarts no pod unless the change is about that pod.
- A value is not renamed or removed without a major version.

## Preferred

These make a chart read well in the OpenShift console. They go on the workload's own
metadata, never on a pod template, each as a literal line in the template.

| Key | Kind | Value |
|---|---|---|
| `app.openshift.io/runtime` | label | Name of a console icon: `nodejs`, `python`, `java`, `golang`, `php`, `redis`, `postgresql` |
| `app.openshift.io/vcs-uri` | annotation | Source repository of the component |
| `app.openshift.io/connects-to` | annotation | JSON list of `{"apiVersion", "kind", "name"}`: the components this one depends on |

- An arrow goes from one component to another: the application to its database, its cache
  or its object storage, a worker to its queue. It does not go from a Service to its
  Deployment.
- `app.kubernetes.io/part-of` draws one box per group in the topology view. A chart with
  many workloads uses a few groups; a small chart uses one.
- A NetworkPolicy admits only what the application needs between its own components.
- Monitoring is a value, off by default: a ServiceMonitor and a PrometheusRule behind
  `monitoring.enabled`.
- A backup of the data is a value, off by default, and the Delivery instructions say how
  to restore it.

## Pull requests

- One chart or one concern per pull request.
- The description says what changed, why, and how it was tested: at least a fresh install
  and an upgrade from the released version, both as a user without cluster-admin.
- A chart is released when its `version` changes on `main`.
