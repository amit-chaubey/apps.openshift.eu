{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-bundle-google-workspace.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
