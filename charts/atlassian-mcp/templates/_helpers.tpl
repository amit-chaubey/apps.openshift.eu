{{/* Labels on every object of the chart; component is set per object */}}
{{- define "atlassian-mcp.labels" -}}
app: {{ .Release.Name }}
app.kubernetes.io/name: atlassian-mcp
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
{{- end -}}

{{- define "atlassian-mcp.selectorLabels" -}}
app.kubernetes.io/name: atlassian-mcp
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "atlassian-mcp.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}
