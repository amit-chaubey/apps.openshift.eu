{{- define "guacamole.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-guacamole" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Name of the kiosk desktop objects: <release>-kiosk. */}}
{{- define "guacamole.kiosk" -}}
{{- printf "%s-kiosk" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels that are the same on every object of the chart. */}}
{{- define "guacamole.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/*
Value of one Secret key, base64: the given value, else the value already in the
cluster, else a new random one.
*/}}
{{- define "guacamole.secretValue" -}}
{{- if .value -}}
{{- .value | b64enc -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key -}}
{{- else -}}
{{- randAlphaNum (int .length) | b64enc -}}
{{- end -}}
{{- end -}}

{{/* A string as a quoted SQL literal. */}}
{{- define "guacamole.sql" -}}
'{{ toString . | replace "'" "''" }}'
{{- end -}}
