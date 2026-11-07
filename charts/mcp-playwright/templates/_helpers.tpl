{{/* Labels on every object of the chart; component is set per object */}}
{{- define "mcp-playwright.labels" -}}
app: {{ .Release.Name }}
app.kubernetes.io/name: mcp-playwright
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "mcp-playwright.selectorLabels" -}}
app.kubernetes.io/name: mcp-playwright
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
