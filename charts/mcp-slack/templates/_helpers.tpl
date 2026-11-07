{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-slack.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Catalog labels of the server */}}
{{- define "mcp-slack.catalogLabels" -}}
{{ .Values.mcp.labelDomain }}/server: {{ .Values.mcp.server | quote }}
{{ .Values.mcp.labelDomain }}/bundle: {{ .Values.mcp.bundle | quote }}
{{ .Values.mcp.labelDomain }}/category: {{ .Values.mcp.category | quote }}
{{- end -}}

{{/* Name of every object */}}
{{- define "mcp-slack.name" -}}
{{- default .Values.mcp.server .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mcp-slack.selectorLabels" -}}
app: {{ include "mcp-slack.name" . }}
app.kubernetes.io/name: mcp-slack
{{- end -}}

{{/* Secret with the bot token */}}
{{- define "mcp-slack.tokenSecret" -}}
{{- default (printf "%s-%s" .Release.Name .Values.slack.secretSuffix) .Values.slack.existingSecret | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-slack.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* SLACK_MCP_ENABLED_TOOLS: slack.enabledTools, else the tools that only read plus the
write tools switched on; the server's own default also offers joining, leaving, marking
read and changing user groups */}}
{{- define "mcp-slack.enabledTools" -}}
{{- if .Values.slack.enabledTools -}}
{{- .Values.slack.enabledTools -}}
{{- else -}}
{{- $t := list "channels_list" "channels_me" "conversations_history" "conversations_replies" "conversations_search_messages" "conversations_unreads" "users_search" "usergroups_list" -}}
{{- if .Values.slack.addMessage }}{{ $t = append $t "conversations_add_message" }}{{ end -}}
{{- if .Values.slack.reactions }}{{ $t = concat $t (list "reactions_add" "reactions_remove") }}{{ end -}}
{{- join "," $t -}}
{{- end -}}
{{- end -}}

{{/* SLACK_MCP_ADD_MESSAGE_TOOL: empty, true or the allowed channel IDs */}}
{{- define "mcp-slack.addMessage" -}}
{{- if .Values.slack.addMessage -}}
{{- if .Values.slack.addMessageChannels -}}
{{- join "," .Values.slack.addMessageChannels -}}
{{- else -}}
true
{{- end -}}
{{- end -}}
{{- end -}}
