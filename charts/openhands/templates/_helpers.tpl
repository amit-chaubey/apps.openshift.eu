{{- define "openhands.name" -}}
{{- .Release.Name -}}
{{- end -}}

{{/* Labels that are the same on every object of the chart. */}}
{{- define "openhands.labels" -}}
app: {{ include "openhands.name" . }}
app.kubernetes.io/name: openhands
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "openhands.selectorLabels" -}}
app: {{ include "openhands.name" . }}
app.kubernetes.io/name: openhands
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the UI login; its redirect goes to the Route */}}
{{- define "openhands.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "openhands.name" .)) | toJson -}}
{{- end -}}

{{/* Audience of the projected ServiceAccount token: the value, else the upstream host */}}
{{- define "openhands.audience" -}}
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
{{- define "openhands.secretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}

{{/* Data of the Secret of that name in the release namespace, or an empty dict */}}
{{- define "openhands.existing" -}}
{{- $found := lookup "v1" "Secret" .root.Release.Namespace .name -}}
{{- if $found -}}
{{- toJson (default dict $found.data) -}}
{{- else -}}
{{- toJson dict -}}
{{- end -}}
{{- end -}}

{{- define "openhands.containerfile" -}}
{{- tpl (.Files.Get "files/Containerfile") . -}}
{{- end -}}

{{/* tools.txt for install-tools.sh: "<kind> <command> <keep> <args>" per line */}}
{{- define "openhands.toolsTxt" -}}
{{- range .Values.build.tools -}}
{{ .kind }} {{ .command }} {{ default false .keep }} {{ default "" .args }}
{{ end -}}
{{- end -}}

{{/* checks.txt for verify-tools.sh: base checks, tools image checks, one per tool */}}
{{- define "openhands.checksTxt" -}}
{{ .Files.Get "files/checks-base.txt" }}
{{- if .Values.build.toolsImage }}
{{ .Files.Get "files/checks-tools.txt" }}
{{- end }}
{{- range .Values.build.tools }}
{{ .check }}
{{- end }}
{{- end -}}

{{/* A changed Containerfile, build script or toolset gets a new image tag */}}
{{- define "openhands.buildHash" -}}
{{- list (include "openhands.containerfile" .) (.Files.Get "files/install-tools.sh") (.Files.Get "files/verify-tools.sh") (include "openhands.toolsTxt" .) (include "openhands.checksTxt" .) | join "\n" | sha256sum | trunc 8 -}}
{{- end -}}

{{- define "openhands.imageTag" -}}
{{- printf "%s-%s" .Values.image.tag (include "openhands.buildHash" .) -}}
{{- end -}}

{{- define "openhands.image" -}}
{{- if not .Values.build.enabled -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- else if eq .Values.build.output.kind "DockerImage" -}}
{{- printf "%s:%s" (required "build.output.name is required for DockerImage" .Values.build.output.name) (include "openhands.imageTag" .) -}}
{{- else -}}
{{- printf "image-registry.openshift-image-registry.svc:5000/%s/%s:%s" .Release.Namespace (include "openhands.name" .) (include "openhands.imageTag" .) -}}
{{- end -}}
{{- end -}}

{{- define "openhands.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* LLM settings the seed script writes: the token proxy as base URL, the default model */}}
{{- define "openhands.seedLlm" -}}
{{- $llm := dict -}}
{{- if .Values.gateway.upstream -}}
{{- $_ := set $llm "base_url" (printf "http://127.0.0.1:%v/v1" .Values.tokenProxy.port) -}}
{{- $_ := set $llm "api_key" "token-proxy" -}}
{{- end -}}
{{- with .Values.llm.model -}}
{{- $_ := set $llm "model" . -}}
{{- end -}}
{{- if $llm }}{{ toJson $llm }}{{ end -}}
{{- end -}}

{{/* MCP servers the seed script writes: JSON map of name to server settings */}}
{{- define "openhands.seedMcp" -}}
{{- $servers := dict -}}
{{- range .Values.mcpServers -}}
{{- $_ := set $servers .name (dict "url" (printf "http://127.0.0.1:%v%s" $.Values.tokenProxy.port .path) "transport" "streamable-http" "timeout" (default 60 .timeout) "enabled" (ne .enabled false)) -}}
{{- end -}}
{{- if .Values.kubernetesMcpServer.enabled -}}
{{- $_ := set $servers "kubernetes" (dict "command" "/tools/kubernetes-mcp-server" "args" (list "--config" "/opt/openhands-scripts/kubernetes-mcp-server.toml" "--disable-multi-cluster") "transport" "stdio" "timeout" 60 "passEnv" (list "KUBERNETES_SERVICE_HOST" "KUBERNETES_SERVICE_PORT")) -}}
{{- end -}}
{{- toJson $servers -}}
{{- end -}}
