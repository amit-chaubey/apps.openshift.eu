{{- define "omniroute.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "omniroute.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Value of one Secret key, base64: the given value, else the value already in the
cluster, else a new random one. hex=true gives 64 hex characters.
*/}}
{{- define "omniroute.secretValue" -}}
{{- if .value -}}
{{- .value | b64enc -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key -}}
{{- else if .hex -}}
{{- randAlphaNum 48 | sha256sum | b64enc -}}
{{- else -}}
{{- randAlphaNum (int .length) | b64enc -}}
{{- end -}}
{{- end -}}

{{/* Labels of one object. A key the object sets itself is left out with skip. */}}
{{- define "omniroute.labels" -}}
{{- $l := dict "app" (.app | default (include "omniroute.fullname" .root)) "app.kubernetes.io/name" (.name | default .root.Chart.Name) "app.kubernetes.io/component" .component "app.kubernetes.io/part-of" .root.Release.Name "app.kubernetes.io/version" .root.Chart.AppVersion -}}
{{- range .skip }}{{- $_ := unset $l . }}{{- end -}}
{{- toYaml $l -}}
{{- end -}}

{{/* Labels of the objects of the Redis store. */}}
{{- define "omniroute.redisLabels" -}}
{{- include "omniroute.labels" (dict "root" . "app" (printf "%s-redis" (include "omniroute.fullname" .)) "name" "redis" "component" "cache") -}}
{{- end -}}
