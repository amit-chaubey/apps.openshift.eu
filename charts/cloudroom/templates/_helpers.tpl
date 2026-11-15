{{- define "cloudroom.name" -}}
{{- .Release.Name -}}
{{- end -}}

{{/* Labels that are the same on every object of the chart. */}}
{{- define "cloudroom.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "cloudroom.selectorLabels" -}}
app: {{ include "cloudroom.name" . }}
app.kubernetes.io/name: cloudroom
app.kubernetes.io/component: server
{{- end -}}

{{/* Audience of the projected ServiceAccount token: the value, else the upstream host */}}
{{- define "cloudroom.audience" -}}
{{- if .Values.gateway.audience -}}
{{- .Values.gateway.audience -}}
{{- else if .Values.gateway.upstream -}}
{{- (urlParse .Values.gateway.upstream).host | splitList ":" | first -}}
{{- end -}}
{{- end -}}

{{- define "cloudroom.tokenSecret" -}}
{{- printf "%s-token" (include "cloudroom.name" .) -}}
{{- end -}}

{{- define "cloudroom.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Loopback URL of the token proxy */}}
{{- define "cloudroom.proxy" -}}
{{- printf "http://127.0.0.1:%v" .Values.tokenProxy.port -}}
{{- end -}}

{{/* NAME=value lines of the agents' environment: the gateway first, then core.agentEnv */}}
{{- define "cloudroom.agentEnv" -}}
{{- $env := dict -}}
{{- $g := .Values.gateway -}}
{{- if $g.upstream -}}
{{- $proxy := include "cloudroom.proxy" . -}}
{{- $_ := set $env "ANTHROPIC_BASE_URL" (printf "%s%s" $proxy $g.anthropicPath) -}}
{{- $_ := set $env "OPENAI_BASE_URL" (printf "%s%s" $proxy $g.openaiPath) -}}
{{- $_ := set $env "OPENAI_API_KEY" "token-proxy" -}}
{{- with $g.claudeCode.opusModel }}{{ $_ := set $env "ANTHROPIC_DEFAULT_OPUS_MODEL" . }}{{ end -}}
{{- with $g.claudeCode.sonnetModel }}{{ $_ := set $env "ANTHROPIC_DEFAULT_SONNET_MODEL" . }}{{ end -}}
{{- with $g.claudeCode.haikuModel }}{{ $_ := set $env "ANTHROPIC_DEFAULT_HAIKU_MODEL" . }}{{ end -}}
{{- if and .Values.harnesses.opencode.enabled $g.opencode.models -}}
{{- $_ := set $env "OPENCODE_CONFIG" "/etc/cloudroom-chart/opencode.json" -}}
{{- end -}}
{{- end -}}
{{- $env = mergeOverwrite $env (deepCopy .Values.core.agentEnv) -}}
{{- range $k := keys $env | sortAlpha }}
{{ $k }}={{ index $env $k }}
{{- end -}}
{{- end -}}

{{/* OpenCode config of the gateway provider */}}
{{- define "cloudroom.opencodeConfig" -}}
{{- $o := .Values.gateway.opencode -}}
{{- $models := dict -}}
{{- range $o.models }}{{ $_ := set $models . dict }}{{ end -}}
{{- $provider := dict "npm" "@ai-sdk/openai-compatible" "name" $o.providerName "options" (dict "baseURL" (printf "%s%s" (include "cloudroom.proxy" .) .Values.gateway.openaiPath) "apiKey" "token-proxy") "models" $models -}}
{{- $default := default (printf "%s/%s" $o.providerId (first $o.models)) $o.defaultModel -}}
{{- dict "$schema" "https://opencode.ai/config.json" "provider" (dict $o.providerId $provider) "model" $default | toPrettyJson -}}
{{- end -}}

{{- define "cloudroom.imageTag" -}}
{{- printf "%s-%s" .Values.core.version (include "cloudroom.dockerfile" . | sha256sum | trunc 8) -}}
{{- end -}}

{{- define "cloudroom.image" -}}
{{- if .Values.builds.enabled -}}
{{- printf "%s/%s/%s:%s" .Values.builds.registry .Release.Namespace (include "cloudroom.name" .) (include "cloudroom.imageTag" .) -}}
{{- else -}}
{{- printf "%s:%s" (required "image.repository is required when builds.enabled is false" .Values.image.repository) (required "image.tag is required when builds.enabled is false" .Values.image.tag) -}}
{{- end -}}
{{- end -}}

{{/* The core release, the harness CLIs and the container-mode layout of docs/storage.md */}}
{{- define "cloudroom.dockerfile" -}}
{{- $v := .Values.core.version -}}
{{- $uid := int .Values.uid -}}
{{- $pkgs := list -}}
{{- $cli := list -}}
{{- with .Values.harnesses -}}
{{- if .claudeCode.enabled }}{{ $pkgs = append $pkgs (list "@anthropic-ai/claude-code" .claudeCode.version) }}{{ $cli = append $cli "claude" }}{{ end -}}
{{- if .opencode.enabled }}{{ $pkgs = append $pkgs (list "opencode-ai" .opencode.version) }}{{ $cli = append $cli "opencode" }}{{ end -}}
{{- if .codex.enabled }}{{ $pkgs = append $pkgs (list "@openai/codex" .codex.version) }}{{ $cli = append $cli "codex" }}{{ end -}}
{{- end -}}
{{- $npm := list -}}
{{- $allow := list -}}
{{- range $pkgs }}{{ $npm = append $npm (printf "%s@%s" (index . 0) (index . 1)) }}{{ $allow = append $allow (index . 0) }}{{ end -}}
FROM {{ .Values.builds.baseImage }}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {{ join " " .Values.builds.packages }} && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL --retry 5 -o /tmp/core.tgz {{ .Values.core.releaseUrl }}/core-v{{ $v }}/cloudroom-core-{{ $v }}-linux-x86_64.tar.gz \
 && echo "{{ .Values.core.sha256 }}  /tmp/core.tgz" | sha256sum -c - \
 && install -d -m 0755 /usr/local/lib/cloudroom && tar -xzf /tmp/core.tgz -C /usr/local/lib/cloudroom && rm /tmp/core.tgz \
 && /usr/local/lib/cloudroom/cloudroom --version
{{- if $npm }}
# the CLIs fetch their native binaries in their install scripts
RUN npm install --global --prefix /usr/local --allow-scripts={{ join "," $allow }} {{ join " " $npm }} && npm cache clean --force
{{- end }}
RUN groupadd -g {{ $uid }} cloudroom-agent \
 && useradd -u {{ $uid }} -g {{ $uid }} -M -d /home/cloudroom-agent -s /bin/bash cloudroom-agent \
 && useradd -o -u {{ $uid }} -g {{ $uid }} -M -d /home/cloudroom-agent -s /bin/bash cloudroom \
 && install -d -m 0755 /etc/cloudroom /var/cache/cloudroom /var/lib/cloudroom \
 && install -d -m 0711 -o {{ $uid }} -g {{ $uid }} /run/cloudroom \
 && install -d -m 0700 -o {{ $uid }} -g {{ $uid }} /code /home/cloudroom-agent /var/lib/cloudroom/history /var/cache/cloudroom/npm \
 && printf '{"agent_uid":{{ $uid }},"agent_gid":{{ $uid }},"cache_dir":"/var/cache/cloudroom/npm","cgroup_root":null,"agent_sudo":true}\n' > /etc/cloudroom/storage.json \
 && chmod 0644 /etc/cloudroom/storage.json
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false
USER {{ $uid }}:{{ $uid }}
{{- range $cli }}
RUN HOME=/tmp/check {{ . }} --version
{{- end }}
WORKDIR /code
CMD ["/usr/local/lib/cloudroom/cloudroom"]
{{- end -}}
