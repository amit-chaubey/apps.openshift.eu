{{- define "wrapper.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end }}

{{- define "wrapper.env" -}}
- name: HOME
  value: /home/node
- name: N8N_USER_FOLDER
  value: /home/node
- name: N8N_PORT
  value: "5678"
- name: N8N_PROXY_HOPS
  value: "1"
{{- if .Values.route.hostname }}
- name: N8N_HOST
  value: {{ .Values.route.hostname | quote }}
{{- end }}
- name: GENERIC_TIMEZONE
  value: {{ .Values.timezone | quote }}
- name: TZ
  value: {{ .Values.timezone | quote }}
- name: N8N_DIAGNOSTICS_ENABLED
  value: "false"
- name: N8N_ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: n8n-credentials
      key: encryption-key
- name: DB_TYPE
  value: postgresdb
{{- range $env, $key := dict "HOST" "host" "PORT" "port" "DATABASE" "dbname" "USER" "username" "PASSWORD" "password" }}
- name: DB_POSTGRESDB_{{ $env }}
  valueFrom:
    secretKeyRef:
      name: n8n-db-app
      key: {{ $key }}
{{- end }}
- name: DB_POSTGRESDB_SSL_ENABLED
  value: "true"
- name: DB_POSTGRESDB_SSL_CA_FILE
  value: /db-ca/ca.crt
{{- if .Values.queue.enabled }}
- name: EXECUTIONS_MODE
  value: queue
- name: OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS
  value: "true"
- name: QUEUE_HEALTH_CHECK_ACTIVE
  value: "true"
- name: QUEUE_BULL_REDIS_HOST
  value: n8n-redis
- name: QUEUE_BULL_REDIS_PORT
  value: "6379"
- name: QUEUE_BULL_REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: n8n-credentials
      key: redis-password
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{- define "wrapper.podSpec" -}}
{{- if .Values.route.hostname }}
automountServiceAccountToken: false
{{- else }}
serviceAccountName: n8n
{{- end }}
securityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault
{{- end }}

{{- define "wrapper.volumes" -}}
- name: scripts
  configMap:
    name: n8n-scripts
- name: db-ca
  secret:
    secretName: n8n-db-ca
    items:
    - key: ca.crt
      path: ca.crt
{{- end }}

{{/* Labels of one object. A key the object sets itself is left out with skip. */}}
{{- define "wrapper.labels" -}}
{{- $l := dict "app" (.app | default .root.Chart.Name) "app.kubernetes.io/name" (.name | default .root.Chart.Name) "app.kubernetes.io/component" .component "app.kubernetes.io/part-of" .root.Release.Name "app.kubernetes.io/version" .root.Chart.AppVersion -}}
{{- range .skip }}{{- $_ := unset $l . }}{{- end -}}
{{- toYaml $l -}}
{{- end -}}
