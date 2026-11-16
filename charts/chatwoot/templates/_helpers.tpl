{{/* Labels that are the same on every object of the chart. */}}
{{- define "chatwoot.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "wrapper.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default (printf "v%s-ce" .Chart.AppVersion) }}
{{- end -}}

{{/* The break-glass super admin: an address under .invalid, which no mail system and no
identity provider can deliver to or verify, so no login of a person can match it. */}}
{{- define "wrapper.adminEmail" -}}
{{ .Values.account.adminEmail | default "superadmin@chatwoot.invalid" | lower }}
{{- end -}}

{{/* Stops a render whose values cannot work. */}}
{{- define "wrapper.validate" -}}
{{- if .Values.draftAgent.enabled }}
{{- if or .Values.agentBridge.remoteUrl .Values.agentBridge.standalone.enabled }}
{{- fail "draftAgent.enabled needs the bridge in this release: no agentBridge.remoteUrl or agentBridge.standalone.enabled" }}
{{- end }}
{{- if not .Values.draftAgent.inboxes }}
{{- fail "draftAgent.inboxes is required with draftAgent.enabled (\"*\" is every inbox)" }}
{{- end }}
{{- if not (regexMatch "^[a-z0-9._+-]+@([a-z0-9-]+\\.)+invalid$" .Values.draftAgent.email) }}
{{- fail "draftAgent.email must be an address under the reserved domain .invalid" }}
{{- end }}
{{- end }}
{{- if .Values.odoo.enabled }}
{{- if .Values.agentBridge.standalone.enabled }}
{{- fail "odoo.enabled and agentBridge.standalone.enabled exclude each other" }}
{{- end }}
{{- if not .Values.route.hostname }}
{{- fail "route.hostname is required with odoo.enabled: Odoo posts its events to that host" }}
{{- end }}
{{- if not (hasPrefix "https://" .Values.odoo.url) }}
{{- fail "odoo.url, an https address, is required with odoo.enabled" }}
{{- end }}
{{- if not .Values.odoo.ticketInbox }}
{{- fail "odoo.ticketInbox is required with odoo.enabled" }}
{{- end }}
{{- if eq .Values.odoo.ticketInbox .Values.odoo.leadInbox }}
{{- fail "odoo.ticketInbox and odoo.leadInbox must differ" }}
{{- end }}
{{- end }}
{{- $b := .Values.agentBridge }}
{{- if and $b.remoteUrl (not (hasPrefix "https://" $b.remoteUrl)) }}
{{- fail "agentBridge.remoteUrl must be an https address" }}
{{- end }}
{{- if and $b.remoteUrl $b.standalone.enabled }}
{{- fail "agentBridge.remoteUrl and agentBridge.standalone.enabled exclude each other" }}
{{- end }}
{{- if $b.standalone.enabled }}
{{- if not (include "wrapper.agentBridge" .) }}
{{- fail "agentBridge.standalone.enabled needs agentBot.enabled or copilotPanel.enabled" }}
{{- end }}
{{- if not (hasPrefix "https://" $b.standalone.chatwootUrl) }}
{{- fail "agentBridge.standalone.chatwootUrl, an https address, is required with agentBridge.standalone.enabled" }}
{{- end }}
{{- if and .Values.copilotPanel.enabled (not (and (hasPrefix "https://" $b.standalone.staff.issuerUrl) $b.standalone.staff.clientIds)) }}
{{- fail "agentBridge.standalone.staff.issuerUrl (https) and staff.clientIds are required for the panel" }}
{{- end }}
{{- end }}
{{- if include "wrapper.asksAgent" . }}
{{- if not (regexMatch "^https?://" .Values.agent.url) }}
{{- fail "agent.url, an http or https address, is required with agentBot.enabled or copilotPanel.enabled" }}
{{- end }}
{{- $auth := .Values.agent.auth }}
{{- if not (has $auth.type (list "none" "serviceAccountToken" "clientAssertion" "clientSecret")) }}
{{- fail "agent.auth.type is none, serviceAccountToken, clientAssertion or clientSecret" }}
{{- end }}
{{- if and (include "wrapper.saToken" .) (not $auth.audience) }}
{{- fail "agent.auth.audience is required with agent.auth.type serviceAccountToken or clientAssertion" }}
{{- end }}
{{- if and (has $auth.type (list "clientAssertion" "clientSecret")) (not (and $auth.tokenUrl $auth.clientId)) }}
{{- fail (printf "agent.auth.tokenUrl and agent.auth.clientId are required with agent.auth.type %s" $auth.type) }}
{{- end }}
{{- end }}
{{- if and .Values.copilotPanel.enabled (not .Values.sso.enabled) (not $b.standalone.enabled) }}
{{- fail "copilotPanel.enabled needs sso.enabled: the panel is served behind the staff login" }}
{{- end }}
{{- if not (has .Values.attachments.storage (list "volume" "objectBucketClaim")) }}
{{- fail "attachments.storage is volume or objectBucketClaim" }}
{{- end }}
{{- if and (eq .Values.attachments.storage "objectBucketClaim") (not .Values.attachments.objectBucketClaim.storageClassName) }}
{{- fail "attachments.objectBucketClaim.storageClassName is required with attachments.storage objectBucketClaim" }}
{{- end }}
{{- if and .Values.sso.enabled (not .Values.sso.issuerUrl) }}
{{- fail "sso.issuerUrl is required with sso.enabled" }}
{{- end }}
{{- if not (regexMatch "^[a-z0-9._+-]+@([a-z0-9-]+\\.)+invalid$" (include "wrapper.adminEmail" .)) }}
{{- fail "account.adminEmail must be an address under the reserved domain .invalid, for example superadmin@chatwoot.invalid" }}
{{- end }}
{{- if and .Values.sso.enabled (not (hasPrefix "https://" .Values.sso.issuerUrl)) }}
{{- fail "sso.issuerUrl must be an https address" }}
{{- end }}
{{- /* the dashboard keeps its session cookie for whole days rounded down */}}
{{- $hours := int .Values.sso.session.chatwootHours }}
{{- if and .Values.sso.enabled (or (lt $hours 25) (gt $hours 720)) }}
{{- fail "sso.session.chatwootHours must be a whole number of hours from 25 to 720: below 25 the dashboard session cookie expires at once and every login loops" }}
{{- end }}
{{- end -}}

{{/* Non-empty when the bot, the panel or the drafts are on. */}}
{{- define "wrapper.agentBridge" -}}
{{- if or .Values.agentBot.enabled .Values.copilotPanel.enabled .Values.draftAgent.enabled }}true{{ end }}
{{- end -}}

{{/* Non-empty when the agent gets a ServiceAccount token (directly or as client assertion). */}}
{{- define "wrapper.saToken" -}}
{{- if has .Values.agent.auth.type (list "serviceAccountToken" "clientAssertion") }}true{{ end }}
{{- end -}}

{{/* Non-empty for a release of the bridge alone. */}}
{{- define "wrapper.standalone" -}}
{{- if .Values.agentBridge.standalone.enabled }}true{{ end }}
{{- end -}}

{{/* Non-empty when this release asks the agent itself (not through a remote bridge). */}}
{{- define "wrapper.asksAgent" -}}
{{- if and (include "wrapper.agentBridge" .) (or .Values.agentBridge.standalone.enabled (not .Values.agentBridge.remoteUrl)) }}true{{ end }}
{{- end -}}

{{/* Non-empty when the bot's bridge Deployment is part of this release. */}}
{{- define "wrapper.bridgeHere" -}}
{{- if or .Values.draftAgent.enabled (and .Values.agentBot.enabled (or .Values.agentBridge.standalone.enabled (not .Values.agentBridge.remoteUrl))) }}true{{ end }}
{{- end -}}

{{/* Address of the bridge's webhook port that Chatwoot calls. */}}
{{- define "wrapper.agentBotUrl" -}}
{{- if .Values.agentBridge.remoteUrl -}}
{{ trimSuffix "/" .Values.agentBridge.remoteUrl }}/
{{- else -}}
http://chatwoot-agent-bridge:8080/
{{- end -}}
{{- end -}}

{{/* Environment of a container that asks the agent (agent_bot.py). */}}
{{- define "wrapper.agentEnv" -}}
{{- $a := .Values.agent }}
- name: A2A_URL
  value: {{ $a.url | quote }}
- name: A2A_TIMEOUT
  value: {{ $a.timeoutSeconds | quote }}
- name: A2A_AUTH
  value: {{ $a.auth.type | quote }}
{{- if include "wrapper.saToken" . }}
- name: A2A_TOKEN_FILE
  value: /var/run/secrets/agent/token
{{- end }}
{{- if eq $a.auth.type "clientSecret" }}
- name: A2A_CLIENT_SECRET_FILE
  value: /var/run/secrets/agent/client-secret
{{- end }}
{{- if has $a.auth.type (list "clientAssertion" "clientSecret") }}
- name: A2A_TOKEN_URL
  value: {{ $a.auth.tokenUrl | quote }}
- name: A2A_CLIENT_ID
  value: {{ $a.auth.clientId | quote }}
- name: A2A_SCOPE
  value: {{ $a.auth.scope | quote }}
{{- end }}
- name: PYTHONUNBUFFERED
  value: "1"
{{- end }}

{{/* The ServiceAccount token the agent gets, renewed by the kubelet. */}}
{{- define "wrapper.agentTokenVolume" -}}
{{- if include "wrapper.saToken" . }}
- name: agent-token
  projected:
    defaultMode: 0440
    sources:
    - serviceAccountToken:
        audience: {{ .Values.agent.auth.audience | quote }}
        expirationSeconds: 3600
        path: token
{{- else if eq .Values.agent.auth.type "clientSecret" }}
- name: agent-token
  secret:
    secretName: chatwoot-agent-client
    defaultMode: 0440
    items:
    - key: agent-client-secret
      path: client-secret
{{- end }}
{{- end }}

{{- define "wrapper.agentTokenMount" -}}
{{- if ne .Values.agent.auth.type "none" }}
- name: agent-token
  mountPath: /var/run/secrets/agent
  readOnly: true
{{- end }}
{{- end }}

{{- define "wrapper.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end }}

{{- define "wrapper.podSecurity" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* ServiceAccount of a pod that reads the public address from the Route. */}}
{{- define "wrapper.serviceAccount" -}}
{{- if .Values.route.hostname }}
automountServiceAccountToken: false
{{- else }}
serviceAccountName: chatwoot-route
{{- end }}
{{- end }}

{{/* Writes /run/chatwoot/frontend-url (and the oauth2-proxy settings) from the Route. */}}
{{- define "wrapper.routeHostInit" -}}
- name: route-host
  image: {{ .Values.loginBridge.image }}
  command: ["python3", "/scripts/route_host.py", "chatwoot", "/run/chatwoot", {{ .Values.route.hostname | quote }}]
  securityContext:
    {{- include "wrapper.restricted" . | nindent 4 }}
  volumeMounts:
  - name: scripts
    mountPath: /scripts
  - name: run
    mountPath: /run/chatwoot
  resources:
    {{- toYaml .Values.loginBridge.resources | nindent 4 }}
{{- end }}

{{/* Environment of web, worker and the prepare Job. */}}
{{- define "wrapper.env" -}}
- name: RAILS_ENV
  value: production
- name: NODE_ENV
  value: production
- name: RAILS_LOG_TO_STDOUT
  value: "true"
- name: HOME
  value: /tmp
{{- if .Values.route.hostname }}
- name: FRONTEND_URL
  value: https://{{ .Values.route.hostname }}
{{- end }}
- name: SECRET_KEY_BASE
  valueFrom:
    secretKeyRef:
      name: chatwoot-credentials
      key: secret-key-base
{{- range $env, $key := dict "HOST" "host" "PORT" "port" "DATABASE" "dbname" "USERNAME" "username" "PASSWORD" "password" }}
- name: POSTGRES_{{ $env }}
  valueFrom:
    secretKeyRef:
      name: chatwoot-db-app
      key: {{ $key }}
{{- end }}
# libpq reads these; the server certificate is checked against the operator's CA
- name: PGSSLMODE
  value: verify-full
- name: PGSSLROOTCERT
  value: /db-ca/ca.crt
- name: REDIS_URL
  value: redis://chatwoot-redis:6379
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: chatwoot-credentials
      key: redis-password
- name: ENABLE_ACCOUNT_SIGNUP
  value: "false"
- name: DISABLE_TELEMETRY
  value: "true"
# relays mobile push notifications through chatwoot.com otherwise
- name: ENABLE_PUSH_RELAY_SERVER
  value: "false"
- name: INSTALLATION_ENV
  value: helm
- name: CHATWOOT_ACCOUNT_ID
  value: {{ .Values.account.id | quote }}
{{- if .Values.sso.enabled }}
- name: CHATWOOT_SESSION_HOURS
  value: {{ .Values.sso.session.chatwootHours | quote }}
{{- end }}
{{- if eq .Values.attachments.storage "objectBucketClaim" }}
- name: ACTIVE_STORAGE_SERVICE
  value: s3_compatible
- name: BUCKET_HOST
  valueFrom:
    configMapKeyRef:
      name: chatwoot-attachments
      key: BUCKET_HOST
- name: BUCKET_PORT
  valueFrom:
    configMapKeyRef:
      name: chatwoot-attachments
      key: BUCKET_PORT
- name: STORAGE_BUCKET_NAME
  valueFrom:
    configMapKeyRef:
      name: chatwoot-attachments
      key: BUCKET_NAME
- name: STORAGE_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: chatwoot-attachments
      key: AWS_ACCESS_KEY_ID
- name: STORAGE_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: chatwoot-attachments
      key: AWS_SECRET_ACCESS_KEY
- name: STORAGE_REGION
  value: {{ .Values.attachments.objectBucketClaim.region | quote }}
- name: STORAGE_FORCE_PATH_STYLE
  value: "true"
{{- if .Values.attachments.objectBucketClaim.caConfigMap }}
# start.sh adds this to the system certificates for the S3 client alone
- name: CHART_S3_EXTRA_CA
  value: /etc/chatwoot/s3-ca/service-ca.crt
{{- end }}
{{- else }}
- name: ACTIVE_STORAGE_SERVICE
  value: local
{{- end }}
{{- if and .Values.agentBot.enabled (not .Values.agentBridge.remoteUrl) }}
# the chart initializer lets webhooks to this address alone reach a private address
- name: CHART_AGENT_BOT_URL
  value: {{ include "wrapper.agentBotUrl" . }}
{{- end }}
{{- if .Values.odoo.enabled }}
# and to the Odoo bridge's webhook port
- name: CHART_ODOO_BRIDGE_URL
  value: http://chatwoot-odoo-bridge:8080/
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/* .job: the prepare Job, which writes no files and must not hold the ReadWriteOnce volume */}}
{{- define "wrapper.volumeMounts" -}}
- name: tmp
  mountPath: /app/tmp
- name: home
  mountPath: /tmp
- name: scripts
  mountPath: /scripts
- name: chart-initializer
  mountPath: /app/config/initializers/zz_chart.rb
  subPath: chart_initializer.rb
- name: db-ca
  mountPath: /db-ca
{{- if not .Values.route.hostname }}
- name: run
  mountPath: /run/chatwoot
{{- end }}
{{- if eq .Values.attachments.storage "volume" }}
{{- if not .job }}
- name: storage
  mountPath: /app/storage
{{- end }}
{{- else if .Values.attachments.objectBucketClaim.caConfigMap }}
- name: s3-ca
  mountPath: /etc/chatwoot/s3-ca
{{- end }}
{{- end }}

{{- define "wrapper.volumes" -}}
- name: tmp
  emptyDir: {}
- name: home
  emptyDir: {}
- name: run
  emptyDir: {}
- name: scripts
  configMap:
    name: chatwoot-scripts
- name: chart-initializer
  configMap:
    name: chatwoot-scripts
    items:
    - key: chart_initializer.rb
      path: chart_initializer.rb
- name: db-ca
  secret:
    secretName: chatwoot-db-ca
    items:
    - key: ca.crt
      path: ca.crt
{{- if eq .Values.attachments.storage "volume" }}
{{- if not .job }}
- name: storage
  persistentVolumeClaim:
    claimName: chatwoot-storage
{{- end }}
{{- else if .Values.attachments.objectBucketClaim.caConfigMap }}
- name: s3-ca
  configMap:
    name: {{ .Values.attachments.objectBucketClaim.caConfigMap }}
{{- end }}
{{- end }}

{{/* The command of a Chatwoot container: the address from the Route when no hostname is set. */}}
{{- define "wrapper.start" -}}
["/bin/sh", "/scripts/start.sh"
{{- range . }}, {{ . | quote }}{{ end -}}
]
{{- end }}

{{/*
Paths of the host that website visitors reach without a login, as oauth2-proxy
skip-auth-route rules (METHOD=regex). Everything else needs a login.
*/}}
{{- define "wrapper.publicRoutes" -}}
{{- $p := .Values.sso.public -}}
{{- $r := list -}}
{{- if $p.widget -}}
{{- $r = concat $r (list
  "GET=^/packs/js/sdk\\.js$"
  "GET=^/vite/assets/[^/]+$"
  "GET=^/widget$"
  "^/api/v1/widget/"
  "GET=^/cable$"
  "GET=^/audio/(widget|dashboard)/[^/]+\\.mp3$"
  "GET=^/assets/images/[^/]+$"
  "GET=^/rails/active_storage/(blobs|representations)/(proxy|redirect)/"
  "GET=^/rails/active_storage/disk/"
) -}}
{{- end -}}
{{- if $p.inboxApi -}}
{{- $r = append $r "^/public/api/v1/inboxes/" -}}
{{- end -}}
{{- if $p.surveys -}}
{{- $r = concat $r (list "GET=^/survey/responses/[^/]+$" "^/public/api/v1/csat_survey/") -}}
{{- end -}}
{{- if $p.helpCenter -}}
{{- $r = append $r "GET=^/hc/" -}}
{{- end -}}
{{- if and .Values.agentBot.enabled .Values.agentBridge.remoteUrl -}}
{{- /* a remote bridge posts the bot's answers with the bot's access token */ -}}
{{- $r = append $r "POST=^/api/v1/accounts/[0-9]+/conversations/[0-9]+/(messages|toggle_status|custom_attributes)$" -}}
{{- end -}}
{{- toJson $r -}}
{{- end }}
