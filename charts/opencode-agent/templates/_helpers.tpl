{{- define "opencode-agent.person" -}}
{{- default .Release.Name .Values.person.name -}}
{{- end -}}

{{- define "opencode-agent.name" -}}
{{- printf "%s-agent" (include "opencode-agent.person" .) -}}
{{- end -}}

{{- define "opencode-agent.serviceAccountName" -}}
{{- default (include "opencode-agent.name" .) .Values.serviceAccount.name -}}
{{- end -}}

{{/* Labels that are the same on every object of the chart. */}}
{{- define "opencode-agent.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* kagent pod selector, see the Service kagent renders for every Agent */}}
{{- define "opencode-agent.selectorLabels" -}}
app: kagent
kagent: {{ include "opencode-agent.name" . }}
{{- end -}}

{{/* "true" when the chart renders the ServiceAccount; kagent creates the one named after the Agent */}}
{{- define "opencode-agent.ownServiceAccount" -}}
{{- if and .Values.serviceAccount.name (ne .Values.serviceAccount.name (include "opencode-agent.name" .)) }}true{{ end -}}
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the UI login; its redirect goes to the Route */}}
{{- define "opencode-agent.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "opencode-agent.name" .)) | toJson -}}
{{- end -}}

{{/* OpenShift user name of the only person admitted to the UI */}}
{{- define "opencode-agent.ownerEmail" -}}
{{- default .Values.person.email .Values.oauth.ownerEmail -}}
{{- end -}}

{{/* Audience of the projected ServiceAccount token: the value, else the upstream host */}}
{{- define "opencode-agent.audience" -}}
{{- if .Values.gateway.audience -}}
{{- .Values.gateway.audience -}}
{{- else if .Values.gateway.upstream -}}
{{- (urlParse .Values.gateway.upstream).host | splitList ":" | first -}}
{{- end -}}
{{- end -}}

{{/*
Plain value of one generated secret: the given value, else the value already in
the cluster, else a new random one. Takes value, existing (Secret data), key, length.
*/}}
{{- define "opencode-agent.secretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}

{{/* Data of the Secret of that name in the release namespace, or an empty dict */}}
{{- define "opencode-agent.existing" -}}
{{- $found := lookup "v1" "Secret" (include "opencode-agent.namespace" .root) .name -}}
{{- if $found -}}
{{- toJson (default dict $found.data) -}}
{{- else -}}
{{- toJson dict -}}
{{- end -}}
{{- end -}}

{{- define "opencode-agent.opencodeImage" -}}
{{- if .Values.builds.enabled -}}
{{- printf "%s/%s/%s-opencode:%s" .Values.builds.registry (include "opencode-agent.namespace" .) (include "opencode-agent.name" .) (include "opencode-agent.opencodeTag" .) -}}
{{- else -}}
{{- default (printf "%s:%s" .Values.opencode.baseImage .Values.opencode.version) .Values.opencode.image -}}
{{- end -}}
{{- end -}}

{{- define "opencode-agent.a2aImage" -}}
{{- if .Values.builds.enabled -}}
{{- printf "%s/%s/%s-opencode-a2a:%s" .Values.builds.registry (include "opencode-agent.namespace" .) (include "opencode-agent.name" .) (include "opencode-agent.a2aTag" .) -}}
{{- else -}}
{{- required "a2a.image is required when builds.enabled is false" .Values.a2a.image -}}
{{- end -}}
{{- end -}}

{{- define "opencode-agent.repoDir" -}}
{{- if .Values.git.url -}}
{{- printf "/workspace/%s" (.Values.git.url | trimSuffix "/" | base | trimSuffix ".git") -}}
{{- else -}}
/workspace
{{- end -}}
{{- end -}}

{{- define "opencode-agent.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Namespaces whose pods may call A2A on 8080, the A2A gateway first */}}
{{- define "opencode-agent.a2aNamespaces" -}}
{{- $names := list -}}
{{- with .Values.a2a.gatewayNamespace -}}
{{- $names = append $names . -}}
{{- end -}}
{{- $names = concat $names .Values.networkPolicy.a2aNamespaces | uniq -}}
{{- toJson $names -}}
{{- end -}}

{{- define "opencode-agent.opencodeDockerfile" -}}
FROM {{ .Values.kubernetesMcpServer.image }} AS mcp
FROM {{ .Values.tools.goImage }} AS odf
RUN apk add --no-cache git && git clone --depth 1 {{ .Values.tools.odfRepository }} /src
RUN cd /src && CGO_ENABLED=0 GOTOOLCHAIN=auto go build -p 2 -trimpath -ldflags='-s -w' -o /odf ./cmd/odf
FROM {{ .Values.opencode.baseImage }}:{{ .Values.opencode.version }}
COPY install-tools.sh verify-tools.sh /tmp/agent-tools/
RUN sh /tmp/agent-tools/install-tools.sh
COPY --from=mcp /app/kubernetes-mcp-server /tools/kubernetes-mcp-server
COPY --from=odf /odf /usr/local/bin/odf
# npm and uv caches follow HOME and XDG_CACHE_HOME on the volume; kubectl-mdba needs TERM
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false UV_PYTHON_PREFERENCE=system TERM=xterm \
    ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections
USER 1001:0
RUN sh /tmp/agent-tools/verify-tools.sh
{{- end -}}

{{/* Every peer as name=<gateway origin>/a2a/<namespace>/<name>/ */}}
{{- define "opencode-agent.a2aPeers" -}}
{{- $origin := include "opencode-agent.gatewayOrigin" . -}}
{{- $peers := list -}}
{{- range .Values.a2a.peers -}}
{{- $peer := kindIs "string" . | ternary (dict "name" .) . -}}
{{- $ns := default (include "opencode-agent.namespace" $) $peer.namespace -}}
{{- $peers = append $peers (printf "%s=%s/a2a/%s/%s/" $peer.name $origin $ns $peer.name) -}}
{{- end -}}
{{- join "," $peers -}}
{{- end -}}

{{- define "opencode-agent.gatewayOrigin" -}}
{{- with .Values.gateway.upstream -}}
{{- $url := urlParse . -}}
{{- printf "%s://%s" $url.scheme $url.host -}}
{{- end -}}
{{- end -}}

{{- define "opencode-agent.a2aDockerfile" -}}
FROM {{ .Values.a2a.baseImage }}
# a source archive has no git metadata for setuptools-scm
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENCODE_A2A={{ .Values.a2a.version }}
RUN pip install --no-cache-dir {{ .Values.a2a.package | quote }}
CMD ["opencode-a2a", "serve"]
{{- end -}}

{{/* A changed Dockerfile gets a new image tag and a new BuildConfig */}}
{{- define "opencode-agent.opencodeHash" -}}
{{- list (include "opencode-agent.opencodeDockerfile" .) (.Files.Get "files/install-tools.sh") (.Files.Get "files/verify-tools.sh") | join "\n" | sha256sum | trunc 8 -}}
{{- end -}}

{{- define "opencode-agent.a2aHash" -}}
{{- include "opencode-agent.a2aDockerfile" . | sha256sum | trunc 8 -}}
{{- end -}}

{{- define "opencode-agent.opencodeTag" -}}
{{- printf "%s-%s" .Values.opencode.version (include "opencode-agent.opencodeHash" .) -}}
{{- end -}}

{{/* An image tag has no "+": a local version label becomes "-" */}}
{{- define "opencode-agent.a2aTag" -}}
{{- printf "%s-%s" (.Values.a2a.version | replace "+" "-") (include "opencode-agent.a2aHash" .) -}}
{{- end -}}

{{/* Tenant whose callers the A2A adapter admits by JWT; empty when off */}}
{{- define "opencode-agent.a2aTenant" -}}
{{- $t := .Values.a2a.tenantAuth -}}
{{- if and $t.enabled (or $t.kubernetesIssuer $t.oidcIssuers) -}}
{{- default (first (splitList "-" (include "opencode-agent.namespace" .))) $t.tenant -}}
{{- end -}}
{{- end -}}

{{/* A2A_JWT_ISSUERS: the cluster ServiceAccount issuer and the OIDC issuers */}}
{{- define "opencode-agent.a2aJwtIssuers" -}}
{{- $t := .Values.a2a.tenantAuth -}}
{{- $sa := "/var/run/secrets/kubernetes.io/serviceaccount" -}}
{{- $issuers := list -}}
{{- with $t.kubernetesIssuer -}}
{{- $issuers = append $issuers (dict "issuer" . "kind" "kubernetes" "ca_file" (printf "%s/ca.crt" $sa) "token_file" (printf "%s/token" $sa)) -}}
{{- end -}}
{{- range $t.oidcIssuers -}}
{{- $issuers = append $issuers (dict "issuer" . "kind" "oidc") -}}
{{- end -}}
{{- toJson $issuers -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for an app of a team, else the release namespace */}}
{{/*
mcp.servers as opencode.json "mcp" entries, name to {type, url, enabled, headers}; a
header value is a {file:} reference to what mcp-headers.sh writes, never the credential
*/}}
{{- define "opencode-agent.mcpServers" -}}
{{- $out := dict -}}
{{- $vault := dict -}}
{{- range .Values.vaultSecrets }}{{ $_ := set $vault .name . }}{{ end -}}
{{- range .Values.mcp.servers -}}
{{- $name := required "mcp.servers[].name is required" .name -}}
{{- if not (regexMatch "^[a-zA-Z0-9][a-zA-Z0-9_-]*$" $name) }}{{ fail (printf "mcp.servers name %q: letters, digits, - and _ only" $name) }}{{ end -}}
{{- $url := .url -}}
{{- if .path -}}
{{- if not $.Values.gateway.upstream }}{{ fail (printf "mcp.servers %s: path needs gateway.upstream" $name) }}{{ end -}}
{{- $url = printf "http://127.0.0.1:%v%s" $.Values.tokenProxy.port .path -}}
{{- end -}}
{{- if not $url }}{{ fail (printf "mcp.servers %s: path or url is required" $name) }}{{ end -}}
{{- if .agentToken -}}
{{- $url = printf "http://127.0.0.1:%v/_target/%s" $.Values.tokenProxy.port (include "opencode-agent.mcpTargetId" (list $ .)) -}}
{{- end -}}
{{- $s := dict "type" "remote" "url" $url "enabled" (ne .enabled false) -}}
{{- with .auth -}}
{{- $v := index $vault (.secretRef | default "") -}}
{{- if not $v }}{{ fail (printf "mcp.servers %s: auth.secretRef %q is not a vaultSecrets name" $name (.secretRef | default "")) }}{{ end -}}
{{- $format := .format | default "basic" -}}
{{- if not (has $format (list "basic" "bearer" "raw")) }}{{ fail (printf "mcp.servers %s: auth.format %q: basic, bearer or raw" $name $format) }}{{ end -}}
{{- $keys := list (required "mcp.servers auth.tokenKey is required" .tokenKey) -}}
{{- if eq $format "basic" }}{{ $keys = append $keys (required "mcp.servers auth.userKey is required with format basic" .userKey) }}{{ end -}}
{{- range $keys }}{{ if not (hasKey $v.data .) }}{{ fail (printf "mcp.servers auth: key %s is not in vaultSecrets %s data" . $v.name) }}{{ end }}{{ end -}}
{{- $header := .header | default "Authorization" -}}
{{- if and $.Values.gateway.upstream (hasPrefix (printf "http://127.0.0.1:%v/" $.Values.tokenProxy.port) $url) (eq (lower $header) "authorization") -}}
{{- fail (printf "mcp.servers %s: the token proxy sets Authorization to the agent token; choose another auth.header" $name) -}}
{{- end -}}
{{- $_ := set $s "headers" (dict $header (printf "{file:/var/run/mcp-headers/%s}" $name)) -}}
{{- end -}}
{{- $_ := set $out $name $s -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* token proxy target id of an mcp.servers entry with agentToken: <namespace>.<name> */}}
{{- define "opencode-agent.mcpTargetId" -}}
{{- $root := index . 0 -}}{{- $s := index . 1 -}}
{{- printf "%s.%s" (include "opencode-agent.namespace" $root) (lower $s.name | replace "_" "-") -}}
{{- end -}}

{{/*
mcp.servers entries with agentToken as the token proxy's targets file, id to URL: the proxy
sends the agent's own token to that URL only
*/}}
{{- define "opencode-agent.mcpTargets" -}}
{{- $out := dict -}}
{{- range .Values.mcp.servers -}}
{{- if .agentToken -}}
{{- if not $.Values.tokenProxy.agentToken.tokenUrl }}{{ fail (printf "mcp.servers %s: agentToken needs tokenProxy.agentToken.tokenUrl" .name) }}{{ end -}}
{{- if or .path (not .url) }}{{ fail (printf "mcp.servers %s: agentToken needs url, not path" .name) }}{{ end -}}
{{- if not (regexMatch "^(https://|http://[a-z0-9.-]+\\.svc(\\.cluster\\.local)?(:[0-9]+)?(/|$))" .url) }}{{ fail (printf "mcp.servers %s: agentToken url must be https, or http to a Service" .name) }}{{ end -}}
{{- $n := .name -}}
{{- with .auth }}{{ if eq (lower (.header | default "Authorization")) "authorization" }}{{ fail (printf "mcp.servers %s: the token proxy sets Authorization to the agent token; choose another auth.header" $n) }}{{ end }}{{ end -}}
{{- $_ := set $out (include "opencode-agent.mcpTargetId" (list $ .)) .url -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{- define "opencode-agent.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}
