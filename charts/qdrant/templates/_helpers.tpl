{{- define "qdrant.name" -}}
{{- printf "%s-qdrant" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels on every object of the chart; component is set per object */}}
{{- define "qdrant.labels" -}}
app: {{ include "qdrant.name" . }}
app.kubernetes.io/name: qdrant
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "qdrant.selectorLabels" -}}
app.kubernetes.io/name: qdrant
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/* The given value, else the value already in the cluster, else a new random one */}}
{{- define "qdrant.apiKey" -}}
{{- $found := lookup "v1" "Secret" .Release.Namespace (printf "%s-api-key" (include "qdrant.name" .)) -}}
{{- if .Values.secrets.apiKey -}}
{{- .Values.secrets.apiKey -}}
{{- else if and $found (hasKey (default dict $found.data) "api-key") -}}
{{- index $found.data "api-key" | b64dec -}}
{{- else -}}
{{- randAlphaNum 48 -}}
{{- end -}}
{{- end -}}
