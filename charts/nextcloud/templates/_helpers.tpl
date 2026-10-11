{{- define "wrapper.host" -}}
{{- required "nextcloud.nextcloud.host is required" .Values.nextcloud.nextcloud.host -}}
{{- end -}}

{{- define "wrapper.onlyofficeHost" -}}
{{- if .Values.onlyoffice.host -}}
{{- .Values.onlyoffice.host -}}
{{- else -}}
{{- printf "office-%s" (include "wrapper.host" .) -}}
{{- end -}}
{{- end -}}

{{/*
Value of one Secret key, base64: the given value, else the value already in the
cluster, else a new random one.
*/}}
{{- define "wrapper.secretValue" -}}
{{- if .value -}}
{{- .value | b64enc -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key -}}
{{- else -}}
{{- randAlphaNum (int .length) | b64enc -}}
{{- end -}}
{{- end -}}

{{- define "wrapper.existingData" -}}
{{- $found := lookup "v1" "Secret" .namespace .name -}}
{{- if $found -}}{{- $found.data | toJson -}}{{- else -}}{}{{- end -}}
{{- end -}}

{{- define "wrapper.signalingHost" -}}
{{- .Values.talk.signaling.host | default (printf "signaling-%s" (include "wrapper.host" .)) -}}
{{- end -}}

{{- define "wrapper.turnHost" -}}
{{- .Values.talk.turn.host | default (printf "turn-%s" (include "wrapper.host" .)) -}}
{{- end -}}

{{/* Redis runs when a component that needs it is on. */}}
{{- define "wrapper.cacheEnabled" -}}
{{- if or .Values.cache.enabled .Values.notifyPush.enabled .Values.whiteboard.enabled (include "wrapper.workerEnabled" .) -}}true{{- end -}}
{{- end -}}

{{/* The worker runs when it is switched on or the AI integration needs it. */}}
{{- define "wrapper.workerEnabled" -}}
{{- if or .Values.taskprocessing.enabled .Values.ai.enabled -}}true{{- end -}}
{{- end -}}

{{- define "wrapper.nextcloudImage" -}}
{{- $i := .Values.nextcloud.image -}}
{{- printf "%s/%s:%s" ($i.registry | default "docker.io") ($i.repository | default "nextcloud") $i.tag -}}
{{- end -}}

{{/* Address of Nextcloud inside the project. */}}
{{- define "wrapper.internalUrl" -}}
{{- printf "http://nextcloud:%v" (.Values.nextcloud.service.port | default 8080) -}}
{{- end -}}

{{/* Labels of one component of this chart. */}}
{{- define "wrapper.labels" -}}
app.kubernetes.io/name: {{ . }}
app.kubernetes.io/instance: {{ . }}
{{- end -}}

{{/* Annotations of a Route; path: true drops the certificate request. */}}
{{- define "wrapper.routeAnnotations" -}}
{{- $a := dict -}}
{{- $path := .path -}}
{{- range $k, $v := .root.Values.route.annotations -}}
{{- if not (and $path (hasPrefix "cert-manager.io/" $k)) -}}
{{- $_ := set $a $k $v -}}
{{- end -}}
{{- end -}}
{{- if .root.Values.hsts.enabled -}}
{{- $_ := set $a "haproxy.router.openshift.io/hsts_header" .root.Values.hsts.header -}}
{{- end -}}
{{- if $path -}}
{{- $_ := set $a "haproxy.router.openshift.io/rewrite-target" "/" -}}
{{- end -}}
{{- toYaml $a -}}
{{- end -}}

{{/* A Route on the Nextcloud host that serves one path from a Service. */}}
{{- define "wrapper.pathRoute" -}}
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: {{ .name }}
  labels:
    {{- include "wrapper.labels" .name | nindent 4 }}
    {{- include "wrapper.objectLabels" (dict "root" .root "app" .name "component" "route" "skip" (list "app.kubernetes.io/name")) | nindent 4 }}
  annotations:
    {{- include "wrapper.routeAnnotations" (dict "root" .root "path" true) | nindent 4 }}
spec:
  host: {{ include "wrapper.host" .root }}
  path: {{ .path }}
  port:
    targetPort: {{ .port }}
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
  to:
    kind: Service
    name: {{ .name }}
    weight: 100
  wildcardPolicy: None
{{- end -}}

{{/* The pod runs on the node of Nextcloud to share its ReadWriteOnce volume. */}}
{{- define "wrapper.sameNode" -}}
podAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
  - topologyKey: kubernetes.io/hostname
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: nextcloud
        app.kubernetes.io/component: app
{{- end -}}

{{/* Security context of a container that runs with the UID of the project. */}}
{{- define "wrapper.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
{{- end -}}

{{/* One ExternalSecret: name, keys read from the store, static keys. */}}
{{- define "wrapper.externalSecret" -}}
{{- $es := .root.Values.secrets.externalSecret -}}
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: {{ .name }}
  labels:
    {{- include "wrapper.objectLabels" (dict "root" .root "app" .app "component" "credentials") | nindent 4 }}
spec:
  refreshInterval: {{ $es.refreshInterval }}
  secretStoreRef:
    kind: {{ $es.storeKind }}
    name: {{ required "secrets.externalSecret.storeName is required" $es.storeName }}
  target:
    name: {{ .name }}
    creationPolicy: Owner
    deletionPolicy: Retain
    {{- with .static }}
    template:
      engineVersion: v2
      mergePolicy: Merge
      data:
        {{- toYaml . | nindent 8 }}
    {{- end }}
  data:
  {{- range $key := .keys }}
  - secretKey: {{ $key }}
    remoteRef:
      key: {{ index $es.items $key }}
      property: login.password
      conversionStrategy: Default
      decodingStrategy: None
      metadataPolicy: None
      nullBytePolicy: Ignore
  {{- end }}
{{- end -}}

{{/*
Topology view of the OpenShift console. Per workload: the group it is drawn in, the icon,
a product logo where one exists, the source repository, and the workloads it calls (drawn as arrows). An arrow to a workload that is switched off
is not drawn.
*/}}
{{- define "wrapper.topology" -}}
nextcloud-taskprocessing: {group: nextcloud, runtime: php, to: [StatefulSet/nextcloud-mariadb, Deployment/nextcloud-redis], icon: "https://raw.githubusercontent.com/nextcloud/promo/master/nextcloud-icon.svg", vcs: "https://github.com/nextcloud/server"}
nextcloud-notify-push: {group: nextcloud, runtime: rust, to: [Deployment/nextcloud-redis, StatefulSet/nextcloud-mariadb], vcs: "https://github.com/nextcloud/notify_push"}
nextcloud-redis: {group: nextcloud, runtime: redis, vcs: "https://github.com/redis/redis"}
onlyoffice: {group: nextcloud-office, runtime: nodejs, icon: "https://avatars.githubusercontent.com/ONLYOFFICE", vcs: "https://github.com/ONLYOFFICE/DocumentServer"}
nextcloud-whiteboard: {group: nextcloud-office, runtime: nodejs, to: [Deployment/nextcloud, Deployment/nextcloud-redis], icon: "https://raw.githubusercontent.com/nextcloud/promo/master/nextcloud-icon.svg", vcs: "https://github.com/nextcloud/whiteboard"}
nextcloud-signaling: {group: nextcloud-talk, runtime: golang, to: [Deployment/nextcloud-nats, Deployment/nextcloud-turn], vcs: "https://github.com/strukturag/nextcloud-spreed-signaling"}
nextcloud-nats: {group: nextcloud-talk, runtime: golang, icon: "https://raw.githubusercontent.com/cncf/artwork/main/projects/nats/icon/color/nats-icon-color.svg", vcs: "https://github.com/nats-io/nats-server"}
nextcloud-turn: {group: nextcloud-talk, vcs: "https://github.com/coturn/coturn"}
nextcloud-talk-recording: {group: nextcloud-talk, runtime: python, to: [Deployment/nextcloud-signaling], vcs: "https://github.com/nextcloud/nextcloud-talk-recording"}
nextcloud-exporter: {group: nextcloud-monitoring, runtime: golang, to: [Deployment/nextcloud], icon: "https://raw.githubusercontent.com/cncf/artwork/main/projects/prometheus/icon/color/prometheus-icon-color.svg", vcs: "https://github.com/xperimental/nextcloud-exporter"}
nextcloud-redis-exporter: {group: nextcloud-monitoring, runtime: golang, to: [Deployment/nextcloud-redis], icon: "https://raw.githubusercontent.com/cncf/artwork/main/projects/prometheus/icon/color/prometheus-icon-color.svg", vcs: "https://github.com/oliver006/redis_exporter"}
nextcloud-mariadb: {group: nextcloud, runtime: mariadb, vcs: "https://github.com/MariaDB/server"}
nextcloud-blackbox-exporter: {group: nextcloud-monitoring, runtime: golang, to: [Deployment/nextcloud], icon: "https://raw.githubusercontent.com/cncf/artwork/main/projects/prometheus/icon/color/prometheus-icon-color.svg", vcs: "https://github.com/prometheus/blackbox_exporter"}
{{- end -}}

{{- define "wrapper.topologyLabels" -}}
{{- $t := index (include "wrapper.topology" . | fromYaml) . | default dict -}}
{{- with $t.group }}
app.kubernetes.io/part-of: {{ . }}
{{- end }}
{{- with $t.runtime }}
app.openshift.io/runtime: {{ . }}
{{- end }}
{{- end -}}

{{- define "wrapper.topologyAnnotations" -}}
{{- $t := index (include "wrapper.topology" . | fromYaml) . | default dict -}}
{{- $to := list -}}
{{- range $t.to }}
{{- $p := splitList "/" . -}}
{{- $to = append $to (dict "apiVersion" "apps/v1" "kind" (first $p) "name" (last $p)) -}}
{{- end }}
{{- if $to }}
app.openshift.io/connects-to: {{ $to | toJson | squote }}
{{- end }}
{{- with $t.icon }}
app.openshift.io/custom-icon: {{ . }}
{{- end }}
{{- with $t.vcs }}
app.openshift.io/vcs-uri: {{ . }}
{{- end }}
{{- end -}}

{{/*
Labels of one object. .app is the component it belongs to, as named in wrapper.topology:
the object joins the group of that component. A key the object sets itself is left out
with skip: the name under wrapper.labels, the group under wrapper.topologyLabels.
*/}}
{{- define "wrapper.objectLabels" -}}
{{- $t := index (include "wrapper.topology" . | fromYaml) .app | default dict -}}
{{- $l := dict "app" .app "app.kubernetes.io/name" (.name | default .app) "app.kubernetes.io/component" .component "app.kubernetes.io/part-of" ($t.group | default "nextcloud") "app.kubernetes.io/version" .root.Chart.AppVersion -}}
{{- range .skip }}{{- $_ := unset $l . }}{{- end -}}
{{- toYaml $l -}}
{{- end -}}

{{/*
Annotations that let the LoadBalancer Services of one project share a public address.
Read from global, so a chart and its subcharts ask for the same address.
*/}}
{{- define "wrapper.publicAddress" -}}
{{- $p := (.Values.global).publicAddress | default dict -}}
{{- if $p.shared }}
metallb.io/allow-shared-ip: {{ $p.sharingKey | default .Release.Namespace | quote }}
{{- with $p.ip }}
metallb.io/loadBalancerIPs: {{ . | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Services that share an address must all use the policy Cluster. */}}
{{- define "wrapper.trafficPolicy" -}}
{{- if ((.root.Values.global).publicAddress).shared -}}Cluster{{- else -}}{{ .policy }}{{- end -}}
{{- end -}}
