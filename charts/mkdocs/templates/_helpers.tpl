{{- define "mkdocs.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Dockerfile of the build. With a repository its content is the site source,
without one an example site is generated.
*/}}
{{- define "mkdocs.dockerfile" -}}
FROM {{ .Values.build.builderImage }} AS build
{{- if .Values.source.gitRepo }}
COPY --chown=1001:0 . /tmp/src
WORKDIR /tmp/src
RUN pip install --no-cache-dir {{ if .Values.source.requirementsFile }}-r {{ .Values.source.requirementsFile }}{{ else }}mkdocs-material{{ end }}
{{- else }}
RUN pip install --no-cache-dir mkdocs-material && mkdocs new /tmp/src
WORKDIR /tmp/src
{{- end }}
RUN mkdocs build{{ if .Values.source.strict }} --strict{{ end }} -f {{ .Values.source.configFile }} --site-dir /tmp/site
FROM {{ .Values.build.runtimeImage }}
COPY --from=build --chown=1001:0 /tmp/site/ /opt/app-root/src/
CMD ["nginx", "-g", "daemon off;"]
{{- end -}}

{{/*
Value of one Secret key, base64: the given value, else the value already in the
cluster, else a new random one.
*/}}
{{- define "mkdocs.secretValue" -}}
{{- if .value -}}
{{- .value | b64enc -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key -}}
{{- else -}}
{{- randAlphaNum (int .length) | b64enc -}}
{{- end -}}
{{- end -}}

{{/* Labels of one object. A key the object sets itself is left out with skip. */}}
{{- define "mkdocs.labels" -}}
{{- $l := dict "app" (.app | default (include "mkdocs.fullname" .root)) "app.kubernetes.io/name" (.name | default .root.Chart.Name) "app.kubernetes.io/component" .component "app.kubernetes.io/part-of" .root.Release.Name "app.kubernetes.io/version" .root.Chart.AppVersion -}}
{{- range .skip }}{{- $_ := unset $l . }}{{- end -}}
{{- toYaml $l -}}
{{- end -}}
