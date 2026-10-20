{{/* Labels on every object of the chart; component is set per object */}}
{{- define "playwright-mcp.labels" -}}
app: {{ .Release.Name }}
app.kubernetes.io/name: playwright-mcp
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "playwright-mcp.selectorLabels" -}}
app.kubernetes.io/name: playwright-mcp
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
