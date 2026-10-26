{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-microsoft-365.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Name of every object */}}
{{- define "mcp-microsoft-365.name" -}}
{{- default .Values.mcp.server .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mcp-microsoft-365.selectorLabels" -}}
app: {{ include "mcp-microsoft-365.name" . }}
app.kubernetes.io/name: mcp-microsoft-365
{{- end -}}

{{/* Secret with the Entra app registration */}}
{{- define "mcp-microsoft-365.clientSecret" -}}
{{- default (printf "%s-%s" .Release.Name .Values.oauthClient.secretSuffix) .Values.oauthClient.existingSecret | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-microsoft-365.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}
