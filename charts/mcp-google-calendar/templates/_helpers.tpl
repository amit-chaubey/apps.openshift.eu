{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-google-calendar.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Name of the RemoteMCPServer */}}
{{- define "mcp-google-calendar.name" -}}
{{- default .Values.mcp.server .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Secret with the OAuth client the RemoteMCPServer names */}}
{{- define "mcp-google-calendar.clientSecret" -}}
{{- default (printf "%s-%s" .Release.Name .Values.oauthClient.secretSuffix) .Values.oauthClient.existingSecret | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-google-calendar.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}
