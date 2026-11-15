{{/* Name of every object: the release name */}}
{{- define "oh-my-openagent.name" -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for an app of a team, else the release namespace */}}
{{- define "oh-my-openagent.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* Labels that are the same on every object of the chart */}}
{{- define "oh-my-openagent.commonLabels" -}}
app: {{ include "oh-my-openagent.name" . }}
app.kubernetes.io/name: oh-my-openagent
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "oh-my-openagent.labels" -}}
{{- include "oh-my-openagent.commonLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Pod selector; not app.kubernetes.io/instance, which Argo CD sets on every object it tracks */}}
{{- define "oh-my-openagent.selectorLabels" -}}
app: {{ include "oh-my-openagent.name" . }}
app.kubernetes.io/name: oh-my-openagent
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the login; its redirect goes to the Route */}}
{{- define "oh-my-openagent.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "oh-my-openagent.name" .)) | toJson -}}
{{- end -}}

{{/* OpenShift user name of the only person admitted */}}
{{- define "oh-my-openagent.ownerEmail" -}}
{{- default .Values.person.email .Values.oauth.ownerEmail -}}
{{- end -}}

{{/* "true" when the pod sends the ServiceAccount token to the gateway through the token proxy */}}
{{- define "oh-my-openagent.tokenProxy" -}}
{{- if and .Values.gateway.upstream (eq .Values.gateway.auth "serviceAccountToken") }}true{{ end -}}
{{- end -}}

{{/* Audience of the projected ServiceAccount token: the value, else the upstream host */}}
{{- define "oh-my-openagent.audience" -}}
{{- if .Values.gateway.audience -}}
{{- .Values.gateway.audience -}}
{{- else if .Values.gateway.upstream -}}
{{- (urlParse .Values.gateway.upstream).host | splitList ":" | first -}}
{{- end -}}
{{- end -}}

{{/* OpenAI API base URL OmO calls: the token proxy, else the gateway itself */}}
{{- define "oh-my-openagent.baseUrl" -}}
{{- if include "oh-my-openagent.tokenProxy" . -}}
{{- printf "http://127.0.0.1:%v%s" .Values.tokenProxy.port .Values.gateway.path -}}
{{- else -}}
{{- printf "%s%s" (.Values.gateway.upstream | trimSuffix "/") .Values.gateway.path -}}
{{- end -}}
{{- end -}}

{{/* Model a new session starts on */}}
{{- define "oh-my-openagent.defaultModel" -}}
{{- if not .Values.gateway.upstream -}}
{{- else if .Values.gateway.defaultModel -}}
{{- .Values.gateway.defaultModel -}}
{{- else if .Values.gateway.models -}}
{{- $m := first .Values.gateway.models -}}
{{- kindIs "string" $m | ternary $m (get (kindIs "map" $m | ternary $m dict) "id") -}}
{{- end -}}
{{- end -}}

{{/* ~/.omo/agent/models.json: the gateway as one OpenAI-compatible provider */}}
{{- define "oh-my-openagent.modelsJson" -}}
{{- $models := list -}}
{{- range .Values.gateway.models -}}
{{- $m := kindIs "string" . | ternary (dict "id" .) . -}}
{{- if not $m.id }}{{ fail "gateway.models: every entry needs an id" }}{{ end -}}
{{- $models = append $models $m -}}
{{- end -}}
{{- /* the token proxy replaces the Authorization header; OmO still wants a key to list the models */ -}}
{{- $key := ternary "token-proxy" "$GATEWAY_API_KEY" (not (empty (include "oh-my-openagent.tokenProxy" .))) -}}
{{- $providers := dict -}}
{{- if .Values.gateway.upstream -}}
{{- $_ := set $providers .Values.gateway.provider (dict "baseUrl" (include "oh-my-openagent.baseUrl" .) "api" "openai-completions" "apiKey" $key "models" $models) -}}
{{- end -}}
{{- dict "providers" $providers | toPrettyJson -}}
{{- end -}}

{{/* ~/.omo/omo.jsonc: telemetry and the device service off, the default model pinned */}}
{{- define "oh-my-openagent.omoJson" -}}
{{- $c := dict "telemetry" (dict "enabled" false) "device_service" (dict "enabled" false) -}}
{{- with include "oh-my-openagent.defaultModel" . -}}
{{- $_ := set $c "model_profile" (printf "%s/%s" $.Values.gateway.provider .) -}}
{{- end -}}
{{- mustMergeOverwrite $c (deepCopy .Values.omo.config) | toPrettyJson -}}
{{- end -}}

{{/* ~/.omo/agent/settings.json, written on the first start only */}}
{{- define "oh-my-openagent.settingsJson" -}}
{{- $s := dict "enableInstallTelemetry" false "quietStartup" true -}}
{{- with include "oh-my-openagent.defaultModel" . -}}
{{- $_ := set $s "defaultProvider" $.Values.gateway.provider -}}
{{- $_ := set $s "defaultModel" . -}}
{{- end -}}
{{- mustMergeOverwrite $s (deepCopy .Values.omo.settings) | toPrettyJson -}}
{{- end -}}

{{/* A changed Containerfile gets a new image tag and a new BuildConfig */}}
{{- define "oh-my-openagent.buildHash" -}}
{{- list (.Files.Get "files/Containerfile") .Values.builds.baseImage | join "\n" | sha256sum | trunc 8 -}}
{{- end -}}

{{- define "oh-my-openagent.terminalImage" -}}
{{- if .Values.terminal.image -}}
{{- .Values.terminal.image -}}
{{- else if .Values.builds.enabled -}}
{{- printf "%s/%s/%s-terminal:%s" .Values.builds.registry (include "oh-my-openagent.namespace" .) (include "oh-my-openagent.name" .) (include "oh-my-openagent.buildHash" .) -}}
{{- else -}}
{{- fail "terminal.image is required when builds.enabled is false" -}}
{{- end -}}
{{- end -}}

{{/*
Plain value of one generated secret: the given value, else the value already in
the cluster, else a new random one. Takes value, existing (Secret data), key, length.
*/}}
{{- define "oh-my-openagent.secretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}

{{/* Data of the Secret of that name in the release namespace, or an empty dict */}}
{{- define "oh-my-openagent.existing" -}}
{{- $found := lookup "v1" "Secret" (include "oh-my-openagent.namespace" .root) .name -}}
{{- if $found -}}
{{- toJson (default dict $found.data) -}}
{{- else -}}
{{- toJson dict -}}
{{- end -}}
{{- end -}}

{{- define "oh-my-openagent.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}
