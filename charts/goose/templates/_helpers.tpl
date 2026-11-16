{{- define "goose.name" -}}
{{- .Release.Name -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for an app of a team, else the release namespace */}}
{{- define "goose.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* Labels on every object of the chart; component is set per object */}}
{{- define "goose.labels" -}}
app: {{ include "goose.name" . }}
app.kubernetes.io/name: goose
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "goose.selectorLabels" -}}
app.kubernetes.io/name: goose
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{- define "goose.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository (toString .Values.image.tag) -}}
{{- end -}}
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the login; its redirect goes to the Route */}}
{{- define "goose.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "goose.name" .)) | toJson -}}
{{- end -}}

{{/* SAR verb of the login: login is granted by the login Role only, get also by admin and edit */}}
{{- define "goose.loginVerb" -}}
{{- ternary "login" "get" (not (empty .Values.oauth.allowedGroups)) -}}
{{- end -}}

{{/* Audience of the projected ServiceAccount token: the value, else the upstream host */}}
{{- define "goose.audience" -}}
{{- if .Values.gateway.audience -}}
{{- .Values.gateway.audience -}}
{{- else if .Values.gateway.upstream -}}
{{- (urlParse .Values.gateway.upstream).host | splitList ":" | first -}}
{{- end -}}
{{- end -}}

{{- define "goose.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Environment of every goose process: the model through the token proxy, the home on the volume */}}
{{- define "goose.env" -}}
{{- $env := dict
  "HOME" "/home/goose"
  "GOOSE_DISABLE_KEYRING" "1"
  "GOOSE_TELEMETRY_ENABLED" "false"
  "GOOSE_MODE" .Values.llm.mode
-}}
{{- if .Values.gateway.upstream -}}
{{- $_ := set $env "GOOSE_PROVIDER" "openai" -}}
{{- $_ := set $env "OPENAI_HOST" (printf "http://127.0.0.1:%v" .Values.tokenProxy.port) -}}
{{- $_ := set $env "OPENAI_BASE_PATH" "v1/chat/completions" -}}
{{- $_ := set $env "OPENAI_API_KEY" "token-proxy" -}}
{{- end -}}
{{- with .Values.llm.model -}}
{{- $_ := set $env "GOOSE_MODEL" . -}}
{{- end -}}
{{- with .Values.llm.contextLimit -}}
{{- $_ := set $env "GOOSE_CONTEXT_LIMIT" (toString .) -}}
{{- end -}}
{{- range $key, $value := .Values.llm.env -}}
{{- $_ := set $env $key (toString $value) -}}
{{- end -}}
{{- range $key := keys $env | sortAlpha }}
- name: {{ $key }}
  value: {{ index $env $key | quote }}
{{- end }}
- name: GOOSE_SERVER__SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "goose.name" . }}-secret-key
      key: secret-key
{{- end -}}

{{- define "goose.a2aDescription" -}}
{{- default "goose agent" .Values.a2a.description -}}
{{- end -}}

{{/* tenant whose callers the a2a-bridge admits: namespaces <tenant> and <tenant>-* */}}
{{- define "goose.a2aTenant" -}}
{{- default (first (splitList "-" (include "goose.namespace" .))) .Values.a2a.tenantAuth.tenant -}}
{{- end -}}

{{/* JSON list of trusted JWT issuers for the a2a-bridge */}}
{{- define "goose.a2aJwtIssuers" -}}
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

{{/*
Plain value of one generated secret: the given value, else the value already in
the cluster, else a new random one. Takes value, existing (Secret data), key, length.
*/}}
{{- define "goose.secretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}

{{/* Data of the Secret of that name in the release namespace, or an empty dict */}}
{{- define "goose.existing" -}}
{{- $found := lookup "v1" "Secret" (include "goose.namespace" .root) .name -}}
{{- if $found -}}
{{- toJson (default dict $found.data) -}}
{{- else -}}
{{- toJson dict -}}
{{- end -}}
{{- end -}}
