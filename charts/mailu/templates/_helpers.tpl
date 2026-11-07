{{/* Name prefix shared with the objects of the upstream chart. */}}
{{- define "mailu-wrapper.fullname" -}}
{{- required "upstream.fullnameOverride is required" .Values.upstream.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Name of the Secret that holds all four secrets. */}}
{{- define "mailu-wrapper.secretName" -}}
{{- required "upstream.existingSecret is required" .Values.upstream.existingSecret -}}
{{- end -}}

{{/* Hostname of the outgoing address. */}}
{{- define "mailu-wrapper.egressHostname" -}}
{{- .Values.egressService.hostname | default (printf "outbound.%s" .Values.upstream.domain) -}}
{{- end -}}

{{/* Labels that select the pods of one upstream component. */}}
{{- define "mailu-wrapper.selector" -}}
app.kubernetes.io/name: {{ .context.Values.upstream.nameOverride }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
{{- if .component }}
app.kubernetes.io/component: {{ .component }}
{{- end }}
{{- end -}}

{{/*
Value of one Secret key, base64: the given value, else the value already in the
cluster, else a new random one.
*/}}
{{- define "mailu-wrapper.secretValue" -}}
{{- if .value -}}
{{- .value | b64enc -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key -}}
{{- else -}}
{{- randAlphaNum (int .length) | b64enc -}}
{{- end -}}
{{- end -}}

{{/* Public ports of the front pods: name, number, value key. */}}
{{- define "mailu-wrapper.frontPorts" -}}
{{- $all := list (list "smtp" 25 "smtp") (list "smtps" 465 "smtps") (list "smtpd" 587 "submission") (list "imap" 143 "imap") (list "imaps" 993 "imaps") (list "pop3" 110 "pop3") (list "pop3s" 995 "pop3s") (list "sieve" 4190 "manageSieve") (list "http" 80 "http") (list "https" 443 "https") -}}
{{- $out := list -}}
{{- range $all -}}
{{- if index $.Values.mailService.ports (index . 2) -}}
{{- $out = append $out (dict "name" (index . 0) "port" (index . 1)) -}}
{{- end -}}
{{- end -}}
{{- dict "ports" $out | toYaml -}}
{{- end -}}

{{/*
Annotations that let the LoadBalancer Services of one project share a public address.
Read from global, so a chart and its subcharts ask for the same address.
*/}}
{{- define "mailu-wrapper.publicAddress" -}}
{{- $p := (.Values.global).publicAddress | default dict -}}
{{- if $p.shared }}
metallb.io/allow-shared-ip: {{ $p.sharingKey | default .Release.Namespace | quote }}
{{- with $p.ip }}
metallb.io/loadBalancerIPs: {{ . | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Component of an upstream workload: the directory of the template that renders it.
Called from upstream commonAnnotations, so the context is the upstream chart's.
*/}}
{{- define "mailu-wrapper.topologyComponent" -}}
{{- base (dir .Template.Name) -}}
{{- end -}}

{{/* Console icon of the upstream workload being rendered; empty on other objects. */}}
{{- define "mailu-wrapper.topologyIcon" -}}
{{- if eq (base .Template.Name) "deployment.yaml" -}}
{{- $icons := .Values.topology.icon -}}
{{- index $icons (include "mailu-wrapper.topologyComponent" .) | default $icons.default -}}
{{- end -}}
{{- end -}}

{{/*
JSON list of the workloads the upstream workload being rendered depends on; empty on
other objects.
*/}}
{{- define "mailu-wrapper.topologyConnectsTo" -}}
{{- if eq (base .Template.Name) "deployment.yaml" -}}
{{- $out := list -}}
{{- range index .Values.topology.connectsTo (include "mailu-wrapper.topologyComponent" .) | default list -}}
{{- if eq . "redis" -}}
{{- if $.Values.redis.enabled -}}
{{- $out = append $out (dict "apiVersion" "apps/v1" "kind" "StatefulSet" "name" (printf "%s-redis-master" $.Release.Name)) -}}
{{- end -}}
{{- else -}}
{{- $out = append $out (dict "apiVersion" "apps/v1" "kind" "Deployment" "name" (printf "%s-%s" (include "mailu.fullname" $) .)) -}}
{{- end -}}
{{- end -}}
{{- $out | toJson -}}
{{- end -}}
{{- end -}}

{{/* Labels that are the same on every object of the chart. */}}
{{- define "mailu.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
