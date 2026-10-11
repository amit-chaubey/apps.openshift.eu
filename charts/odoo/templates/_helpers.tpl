{{/* Labels of one object. A key the object sets itself is left out with skip. */}}
{{- define "odoo.labels" -}}
{{- $l := dict "app" (.app | default .root.Chart.Name) "app.kubernetes.io/name" (.name | default .root.Chart.Name) "app.kubernetes.io/component" .component "app.kubernetes.io/part-of" .root.Release.Name "app.kubernetes.io/version" .root.Chart.AppVersion -}}
{{- range .skip }}{{- $_ := unset $l . }}{{- end -}}
{{- toYaml $l -}}
{{- end -}}
