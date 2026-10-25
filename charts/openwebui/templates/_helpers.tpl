{{- define "openwebui.person" -}}
{{- default .Release.Name .Values.person.name -}}
{{- end -}}

{{- define "openwebui.name" -}}
{{- printf "%s-openwebui" (include "openwebui.person" .) -}}
{{- end -}}

{{/* Labels on every object of the chart; app and component are set per object */}}
{{- define "openwebui.labels" -}}
app: {{ include "openwebui.name" . }}
app.kubernetes.io/name: openwebui
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "openwebui.selectorLabels" -}}
app.kubernetes.io/name: openwebui
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the login; its redirect goes to the Route */}}
{{- define "openwebui.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "openwebui.name" .)) | toJson -}}
{{- end -}}

{{- define "openwebui.ownerEmail" -}}
{{- default .Values.person.email .Values.oauth.ownerEmail -}}
{{- end -}}

{{/* Audience of the projected ServiceAccount token: the value, else the upstream host */}}
{{- define "openwebui.audience" -}}
{{- if .Values.gateway.audience -}}
{{- .Values.gateway.audience -}}
{{- else if .Values.gateway.upstream -}}
{{- (urlParse .Values.gateway.upstream).host | splitList ":" | first -}}
{{- end -}}
{{- end -}}

{{/*
Plain value of one generated secret: the given value, else the value already in
the cluster, else a new random one. Takes value, existing (Secret data), key, length.
*/}}
{{- define "openwebui.secretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}

{{/* Data of the Secret of that name in the release namespace, or an empty dict */}}
{{- define "openwebui.existing" -}}
{{- $found := lookup "v1" "Secret" (include "openwebui.namespace" .root) .name -}}
{{- if $found -}}
{{- toJson (default dict $found.data) -}}
{{- else -}}
{{- toJson dict -}}
{{- end -}}
{{- end -}}

{{/* TOOL_SERVER_CONNECTIONS: one MCP connection per gateway endpoint, for every user */}}
{{- define "openwebui.toolServers" -}}
{{- $endpoints := .Values.mcp.endpoints -}}
{{- if not $endpoints -}}
{{- $endpoints = list (dict "id" "team" "endpoint" (include "openwebui.namespace" .)) (dict "id" "public" "endpoint" "public") -}}
{{- end -}}
{{- $connections := list -}}
{{- range $endpoints -}}
{{- $connections = append $connections (dict
  "type" "mcp"
  "url" (printf "http://127.0.0.1:%v/mcp/%s" $.Values.tokenProxy.port .endpoint)
  "path" ""
  "auth_type" "none"
  "key" ""
  "config" (dict "enable" true "access_grants" (list (dict "principal_type" "user" "principal_id" "*" "permission" "read")))
  "info" (dict "id" .id "name" .id "description" (printf "MCP servers of %s through the gateway" .endpoint))) -}}
{{- end -}}
{{- toJson $connections -}}
{{- end -}}

{{- define "openwebui.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for an app of a team, else the release namespace */}}
{{- define "openwebui.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}
