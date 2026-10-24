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

{{/* Labels that select the pods of one component of this chart. */}}
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

{{/* A Route on the Nextcloud host that serves one path from a Service; partOf is its group. */}}
{{- define "wrapper.pathRoute" -}}
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: {{ .name }}
  labels:
    app: {{ .name | quote }}
    app.kubernetes.io/name: {{ .name | quote }}
    app.kubernetes.io/instance: {{ .name | quote }}
    app.kubernetes.io/component: route
    app.kubernetes.io/part-of: {{ .partOf | quote }}
    {{- include "nextcloud.commonLabels" .root | nindent 4 }}
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

{{/* One ExternalSecret: name, component, group, keys read from the store, static keys. */}}
{{- define "wrapper.externalSecret" -}}
{{- $es := .root.Values.secrets.externalSecret -}}
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: {{ .name }}
  labels:
    app: {{ .app | quote }}
    app.kubernetes.io/name: {{ .app | quote }}
    app.kubernetes.io/component: credentials
    app.kubernetes.io/part-of: {{ .partOf | quote }}
    {{- include "nextcloud.commonLabels" .root | nindent 4 }}
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

{{/* "true" when the MariaDB root password stays in nextcloud-credentials (before 0.6). */}}
{{- define "wrapper.legacyRootSecret" -}}
{{- $m := lookup "k8s.mariadb.com/v1alpha1" "MariaDB" .Release.Namespace "nextcloud-mariadb" -}}
{{- $ref := (($m).spec).rootPasswordSecretKeyRef | default dict -}}
{{- if or .Values.database.legacyRootSecret (eq ($ref.name | default "") "nextcloud-credentials") -}}true{{- end -}}
{{- end -}}

{{/* Labels that are the same on every object of the chart. */}}
{{- define "nextcloud.commonLabels" -}}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
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
