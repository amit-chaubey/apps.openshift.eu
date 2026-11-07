{{/*
Token check of an MCP server chart: an oauth2-proxy sidecar takes the chart's Service port,
the server listens on 127.0.0.1:<auth.upstreamPort> behind it. Every template reads the
consuming chart's own auth block (.Values.auth); see README.md of this chart.
*/}}

{{/* "true" when the token check is on; argument: the root context */}}
{{- define "mcp-auth.enabled" -}}
{{- if .Values.auth.enabled }}true{{ end -}}
{{- end -}}

{{/* Address the server listens on: 127.0.0.1 behind the sidecar, else every address */}}
{{- define "mcp-auth.serverHost" -}}
{{- if .Values.auth.enabled }}127.0.0.1{{ else }}0.0.0.0{{ end -}}
{{- end -}}

{{/* Port the server listens on; arguments: root, port (the chart's Service port) */}}
{{- define "mcp-auth.serverPort" -}}
{{- if .root.Values.auth.enabled }}{{ .root.Values.auth.upstreamPort }}{{ else }}{{ .port }}{{ end -}}
{{- end -}}

{{/* Issuer without a trailing slash, as in the iss claim of Keycloak */}}
{{- define "mcp-auth.issuer" -}}
{{- trimSuffix "/" .Values.auth.issuerUrl -}}
{{- end -}}

{{/* Audiences as a JSON list, each entry rendered as a template; empty: the release namespace */}}
{{- define "mcp-auth.audiences" -}}
{{- $out := list -}}
{{- range .Values.auth.audiences -}}
{{- $out = append $out (tpl . $) -}}
{{- end -}}
{{- if not $out -}}
{{- $out = list $.Release.Namespace -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* Allowed groups as a JSON list, each entry rendered as a template */}}
{{- define "mcp-auth.allowedGroups" -}}
{{- $out := list -}}
{{- range .Values.auth.allowedGroups -}}
{{- $g := tpl . $ -}}
{{- if $g }}{{ $out = append $out $g }}{{ end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* Secret with the cookie and client secret; arguments: root, name (the server's name) */}}
{{- define "mcp-auth.secretName" -}}
{{- default (printf "%s-auth" .name) .root.Values.auth.existingSecret | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
data of the chart's auth Secret: the value, else the value already in the cluster, else a new
random one, so an upgrade keeps it. Arguments: root, name, namespace
*/}}
{{- define "mcp-auth.secretData" -}}
{{- $a := .root.Values.auth -}}
{{- $found := lookup "v1" "Secret" .namespace (include "mcp-auth.secretName" .) -}}
{{- $old := dict -}}
{{- if $found }}{{ $old = $found.data | default dict }}{{ end -}}
{{- range $key, $value := dict "cookie-secret" $a.cookieSecret "client-secret" $a.clientSecret }}
{{ $key }}: {{ if $value }}{{ $value | b64enc | quote }}{{ else if hasKey $old $key }}{{ index $old $key | quote }}{{ else }}{{ randAlphaNum 32 | b64enc | quote }}{{ end }}
{{- end -}}
{{- end -}}

{{/*
The sidecar container; arguments: root, name (the server's name), port (Service port) and
optionally healthPath, a GET path of the server that passes without a token
*/}}
{{- define "mcp-auth.container" -}}
{{- $a := .root.Values.auth -}}
{{- $issuer := include "mcp-auth.issuer" .root -}}
{{- $audiences := include "mcp-auth.audiences" .root | fromJsonArray -}}
- name: auth
  image: "{{ $a.image.repository }}:{{ $a.image.tag }}"
  imagePullPolicy: {{ $a.image.pullPolicy }}
  args:
  - --http-address=0.0.0.0:{{ .port }}
  - --upstream=http://127.0.0.1:{{ $a.upstreamPort }}/
  - --provider=oidc
  {{- if $issuer }}
  # protected resource metadata (RFC 9728) without a token, so a client finds the issuer
  - --upstream=file:///etc/mcp-auth/#/.well-known/
  - --skip-auth-route=GET=^/\.well-known/oauth-protected-resource$
  - --oidc-issuer-url={{ $issuer }}
  {{- else }}
  # no issuer: no token can be valid, every request but the health paths gets 401
  - --oidc-issuer-url=https://issuer.invalid
  - --skip-oidc-discovery=true
  - --oidc-jwks-url=https://issuer.invalid/certs
  - --login-url=https://issuer.invalid/auth
  - --redeem-url=https://issuer.invalid/token
  {{- end }}
  # a token's aud must name the client id or one of the extra audiences
  - --client-id={{ first $audiences }}
  {{- range rest $audiences }}
  - --oidc-extra-audience={{ . }}
  {{- end }}
  - --oidc-groups-claim={{ $a.groupsClaim }}
  {{- range include "mcp-auth.allowedGroups" .root | fromJsonArray }}
  - --allowed-group={{ . }}
  {{- end }}
  # bearer tokens only: no login redirect, 401 without a valid token
  - --skip-jwt-bearer-tokens=true
  - --api-route=^/
  - --skip-provider-button=true
  # callers are agents without an e-mail address; groups decide
  - --email-domain=*
  - --insecure-oidc-allow-unverified-email=true
  {{- with .healthPath }}
  # the server's own health path, for the probes of the server container
  - --skip-auth-route=GET=^{{ regexQuoteMeta . }}$
  {{- end }}
  {{- range $a.skipAuthRoutes }}
  - --skip-auth-route={{ . }}
  {{- end }}
  # streamable HTTP keeps a response open between events
  - --upstream-timeout={{ $a.upstreamTimeout }}
  - --flush-interval={{ $a.flushInterval }}
  - --ping-path=/oauth2/ping
  - --ready-path=/oauth2/ready
  - --silence-ping-logging=true
  - --cookie-secure=true
  {{- range $a.extraArgs }}
  - {{ . }}
  {{- end }}
  env:
  - name: OAUTH2_PROXY_COOKIE_SECRET
    valueFrom:
      secretKeyRef:
        name: {{ include "mcp-auth.secretName" . }}
        key: cookie-secret
  - name: OAUTH2_PROXY_CLIENT_SECRET
    valueFrom:
      secretKeyRef:
        name: {{ include "mcp-auth.secretName" . }}
        key: client-secret
  ports:
  - name: mcp
    containerPort: {{ .port }}
    protocol: TCP
  readinessProbe:
    httpGet:
      path: /oauth2/ping
      port: mcp
    periodSeconds: 10
  resources:
    {{- toYaml $a.resources | nindent 4 }}
  securityContext:
    allowPrivilegeEscalation: false
    capabilities:
      drop:
      - ALL
    readOnlyRootFilesystem: true
    runAsNonRoot: true
    seccompProfile:
      type: RuntimeDefault
  {{- if $issuer }}
  volumeMounts:
  - name: auth-metadata
    mountPath: /etc/mcp-auth
    readOnly: true
  {{- end }}
{{- end -}}

{{/* Volumes of the sidecar; arguments: root, name (the server's name) */}}
{{- define "mcp-auth.volumes" -}}
{{- if and .root.Values.auth.enabled .root.Values.auth.issuerUrl -}}
- name: auth-metadata
  configMap:
    name: {{ .name }}-auth
{{- end -}}
{{- end -}}

{{/*
Address of the server that callers use, the resource of its metadata: auth.resourceUrl as a
template, else the Service inside the cluster. Arguments: root, name, namespace, port, path
*/}}
{{- define "mcp-auth.resourceUrl" -}}
{{- if .root.Values.auth.resourceUrl -}}
{{- tpl .root.Values.auth.resourceUrl .root -}}
{{- else -}}
{{- printf "http://%s.%s.svc:%v%s" .name .namespace .port .path -}}
{{- end -}}
{{- end -}}

{{/*
data of the chart's auth ConfigMap: protected resource metadata (RFC 9728) that names the
issuer as authorization server. Arguments: root, name, namespace, port, path
*/}}
{{- define "mcp-auth.metadataData" -}}
{{- $doc := dict "resource" (include "mcp-auth.resourceUrl" .) "authorization_servers" (list (include "mcp-auth.issuer" .root)) "bearer_methods_supported" (list "header") -}}
oauth-protected-resource: {{ toJson $doc | quote }}
{{- end -}}

{{/* Port of the issuer, for the egress rule of a NetworkPolicy; argument: the root context */}}
{{- define "mcp-auth.issuerPort" -}}
{{- $u := urlParse (include "mcp-auth.issuer" .) -}}
{{- $hp := splitList ":" $u.host -}}
{{- if gt (len $hp) 1 }}{{ last $hp }}{{ else if eq $u.scheme "http" }}80{{ else }}443{{ end -}}
{{- end -}}

{{/*
Egress rule of a NetworkPolicy that lets the sidecar reach the issuer's discovery document and
keys; empty without the token check or an issuer. Argument: the root context
*/}}
{{- define "mcp-auth.networkPolicyEgress" -}}
{{- if and .Values.auth.enabled .Values.auth.issuerUrl -}}
# the token check reads the issuer's discovery document and signing keys
- ports:
  - protocol: TCP
    port: {{ include "mcp-auth.issuerPort" . }}
{{- end -}}
{{- end -}}
