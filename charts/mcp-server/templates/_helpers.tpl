{{/* Labels on every object of the chart; component is set per object */}}
{{- define "mcp-server.labels" -}}
app: {{ .Release.Name }}
app.kubernetes.io/name: mcp-server
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
{{- end -}}

{{- define "mcp-server.selectorLabels" -}}
app.kubernetes.io/name: mcp-server
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
