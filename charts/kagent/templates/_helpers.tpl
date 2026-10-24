{{/* Objects of this chart; the upstream objects are named kagent-* */}}
{{- define "tenant.login" -}}
{{- printf "%s-login" .Values.upstream.fullnameOverride -}}
{{- end -}}

{{/* CloudNativePG Cluster of the controller; upstream.controller.volumes mounts <name>-app */}}
{{- define "tenant.postgres" -}}
{{- printf "%s-postgres" .Values.upstream.fullnameOverride -}}
{{- end -}}

{{/* Secret with client-id, client-secret and cookie-secret for oauth2-proxy */}}
{{- define "tenant.oidcSecret" -}}
{{- printf "%s-oidc" .Values.upstream.fullnameOverride -}}
{{- end -}}

{{/* Labels on every object of the chart; app and component are set per object */}}
{{- define "tenant.labels" -}}
app: {{ include "tenant.login" . }}
app.kubernetes.io/name: kagent
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "tenant.loginSelectorLabels" -}}
app.kubernetes.io/name: kagent
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/component: login
{{- end -}}

{{/* Service the Route sends to: the OpenShift login, or upstream oauth2-proxy */}}
{{- define "tenant.frontService" -}}
{{- if eq .Values.login.provider "oidc" -}}
{{- printf "%s-oauth2-proxy" .Release.Name -}}
{{- else -}}
{{- include "tenant.login" . -}}
{{- end -}}
{{- end -}}

{{- define "tenant.frontSelectorLabels" -}}
{{- if eq .Values.login.provider "oidc" -}}
app.kubernetes.io/name: oauth2-proxy
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- else -}}
{{ include "tenant.loginSelectorLabels" . }}
{{- end -}}
{{- end -}}

{{- define "tenant.frontPort" -}}
{{- ternary 4180 .Values.login.port (eq .Values.login.provider "oidc") -}}
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the login; its redirect goes to the Route */}}
{{- define "tenant.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "tenant.login" .)) | toJson -}}
{{- end -}}

{{/* Plain value of a generated secret: the given value, else the one in the cluster, else new */}}
{{- define "tenant.secretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}

{{/* Fails on values that would give the tenant cluster-wide objects */}}
{{- define "tenant.validate" -}}
{{- if not (has .Release.Namespace (default list .Values.upstream.rbac.namespaces)) -}}
{{- fail (printf "upstream.rbac.namespaces must contain the release namespace %q: empty renders cluster-wide RBAC" .Release.Namespace) -}}
{{- end -}}
{{- if and (dig "kmcp" "enabled" false .Values.upstream) (ne (toString (dig "kmcp" "rbac" "create" true .Values.upstream)) "false") -}}
{{- fail "upstream.kmcp needs rbac.create false: the platform grants it access" -}}
{{- end -}}
{{- $mode := dig "controller" "auth" "mode" "unsecure" .Values.upstream -}}
{{- if eq .Values.login.provider "oidc" -}}
{{- if or (not (dig "oauth2-proxy" "enabled" false .Values.upstream)) (ne $mode "trusted-proxy") -}}
{{- fail "login.provider oidc needs upstream.oauth2-proxy.enabled and upstream.controller.auth.mode trusted-proxy" -}}
{{- end -}}
{{- if and (eq .Values.secrets.type "externalSecret") (ne (dig "oauth2-proxy" "config" "existingSecret" "" .Values.upstream) (include "tenant.oidcSecret" .)) -}}
{{- fail (printf "secrets.type externalSecret writes Secret %s: set upstream.oauth2-proxy.config.existingSecret to it" (include "tenant.oidcSecret" .)) -}}
{{- end -}}
{{- else if ne $mode "unsecure" -}}
{{- fail "login.provider openshift needs upstream.controller.auth.mode unsecure: the OpenShift login issues no JWT" -}}
{{- end -}}
{{- $pg := dig "database" "postgres" dict .Values.upstream -}}
{{- if and .Values.database.cnpg.enabled (or (dig "bundled" "enabled" false $pg) (ne (dig "urlFile" "" $pg) "/etc/kagent/postgres/uri")) -}}
{{- fail "database.cnpg.enabled needs upstream.database.postgres.urlFile /etc/kagent/postgres/uri and bundled.enabled false (the defaults)" -}}
{{- end -}}
{{- if and (not .Values.database.cnpg.enabled) (eq (dig "urlFile" "" $pg) "/etc/kagent/postgres/uri") -}}
{{- fail "database.cnpg.enabled false: set upstream.database.postgres.urlFile, url or bundled.enabled, and upstream.controller.volumes and volumeMounts to []" -}}
{{- end -}}
{{- if dig "kagent-tools" "enabled" false .Values.upstream -}}
{{- fail "upstream.kagent-tools binds cluster-admin; team namespaces get tools from the platform" -}}
{{- end -}}
{{- end -}}
