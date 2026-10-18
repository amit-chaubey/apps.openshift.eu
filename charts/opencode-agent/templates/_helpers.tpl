{{- define "opencode-agent.person" -}}
{{- default .Release.Name .Values.person.name -}}
{{- end -}}

{{- define "opencode-agent.name" -}}
{{- printf "%s-agent" (include "opencode-agent.person" .) -}}
{{- end -}}

{{- define "opencode-agent.serviceAccountName" -}}
{{- default (include "opencode-agent.name" .) .Values.serviceAccount.name -}}
{{- end -}}

{{/* Labels that are the same on every object of the chart. */}}
{{- define "opencode-agent.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* kagent pod selector, see the Service kagent renders for every Agent */}}
{{- define "opencode-agent.selectorLabels" -}}
app: kagent
kagent: {{ include "opencode-agent.name" . }}
{{- end -}}

{{/* "true" when the chart renders the ServiceAccount; kagent creates the one named after the Agent */}}
{{- define "opencode-agent.ownServiceAccount" -}}
{{- if and .Values.serviceAccount.name (ne .Values.serviceAccount.name (include "opencode-agent.name" .)) }}true{{ end -}}
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the UI login; its redirect goes to the Route */}}
{{- define "opencode-agent.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "opencode-agent.name" .)) | toJson -}}
{{- end -}}

{{/* OpenShift user name of the only person admitted to the UI */}}
{{- define "opencode-agent.ownerEmail" -}}
{{- default .Values.person.email .Values.oauth.ownerEmail -}}
{{- end -}}

{{/* oauth-proxy sends the local part of the user name as the Basic user */}}
{{- define "opencode-agent.opencodeUser" -}}
{{- with include "opencode-agent.ownerEmail" . -}}
{{- splitList "@" . | first -}}
{{- else -}}
opencode
{{- end -}}
{{- end -}}

{{/* Audience of the projected ServiceAccount token: the value, else the upstream host */}}
{{- define "opencode-agent.audience" -}}
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
{{- define "opencode-agent.secretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}

{{/* Data of the Secret of that name in the release namespace, or an empty dict */}}
{{- define "opencode-agent.existing" -}}
{{- $found := lookup "v1" "Secret" .root.Release.Namespace .name -}}
{{- if $found -}}
{{- toJson (default dict $found.data) -}}
{{- else -}}
{{- toJson dict -}}
{{- end -}}
{{- end -}}

{{- define "opencode-agent.opencodeImage" -}}
{{- if .Values.builds.enabled -}}
{{- printf "%s/%s/%s-opencode:%s" .Values.builds.registry .Release.Namespace (include "opencode-agent.name" .) (include "opencode-agent.opencodeTag" .) -}}
{{- else -}}
{{- default (printf "%s:%s" .Values.opencode.baseImage .Values.opencode.version) .Values.opencode.image -}}
{{- end -}}
{{- end -}}

{{- define "opencode-agent.a2aImage" -}}
{{- if .Values.builds.enabled -}}
{{- printf "%s/%s/%s-opencode-a2a:%s" .Values.builds.registry .Release.Namespace (include "opencode-agent.name" .) (include "opencode-agent.a2aTag" .) -}}
{{- else -}}
{{- required "a2a.image is required when builds.enabled is false" .Values.a2a.image -}}
{{- end -}}
{{- end -}}

{{- define "opencode-agent.repoDir" -}}
{{- if .Values.git.url -}}
{{- printf "/workspace/%s" (.Values.git.url | trimSuffix "/" | base | trimSuffix ".git") -}}
{{- else -}}
/workspace
{{- end -}}
{{- end -}}

{{- define "opencode-agent.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Namespaces whose pods may call A2A on 8080, the A2A gateway first */}}
{{- define "opencode-agent.a2aNamespaces" -}}
{{- $names := list -}}
{{- with .Values.a2a.gatewayNamespace -}}
{{- $names = append $names . -}}
{{- end -}}
{{- $names = concat $names .Values.networkPolicy.a2aNamespaces | uniq -}}
{{- toJson $names -}}
{{- end -}}

{{- define "opencode-agent.opencodeDockerfile" -}}
FROM {{ .Values.kubernetesMcpServer.image }} AS mcp
FROM {{ .Values.opencode.baseImage }}:{{ .Values.opencode.version }}
RUN apk add --no-cache git openssh-client
COPY --from=mcp /app/kubernetes-mcp-server /tools/kubernetes-mcp-server
{{- end -}}

{{- define "opencode-agent.a2aDockerfile" -}}
FROM {{ .Values.a2a.baseImage }}
RUN pip install --no-cache-dir opencode-a2a=={{ .Values.a2a.version }}
CMD ["opencode-a2a", "serve"]
{{- end -}}

{{/* A changed Dockerfile gets a new image tag and a new BuildConfig */}}
{{- define "opencode-agent.opencodeHash" -}}
{{- include "opencode-agent.opencodeDockerfile" . | sha256sum | trunc 8 -}}
{{- end -}}

{{- define "opencode-agent.a2aHash" -}}
{{- include "opencode-agent.a2aDockerfile" . | sha256sum | trunc 8 -}}
{{- end -}}

{{- define "opencode-agent.opencodeTag" -}}
{{- printf "%s-%s" .Values.opencode.version (include "opencode-agent.opencodeHash" .) -}}
{{- end -}}

{{- define "opencode-agent.a2aTag" -}}
{{- printf "%s-%s" .Values.a2a.version (include "opencode-agent.a2aHash" .) -}}
{{- end -}}
