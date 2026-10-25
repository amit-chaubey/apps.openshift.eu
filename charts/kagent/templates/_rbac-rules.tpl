{{/*
Rules of the upstream writer and getter Roles. Template names are global and the parent
chart's are read last, so these replace upstream's wildcard rules, which a tenant cannot
grant. The kagent.dev rules need the platform's aggregated edit role to cover every kagent kind.
*/}}
{{- define "kagent.writer.rules" -}}
- apiGroups:
  - kagent.dev
  resources:
  - agents
  - modelconfigs
  - modelproviderconfigs
  - remotemcpservers
  - mcpservers
  - toolservers
  - memories
  - sandboxagents
  - agentharnesses
  verbs:
  - create
  - update
  - patch
  - delete
- apiGroups:
  - kagent.dev
  resources:
  - agents/finalizers
  - modelconfigs/finalizers
  - modelproviderconfigs/finalizers
  - remotemcpservers/finalizers
  - mcpservers/finalizers
  - toolservers/finalizers
  - memories/finalizers
  - sandboxagents/finalizers
  - agentharnesses/finalizers
  verbs:
  - update
- apiGroups:
  - ""
  resources:
  - configmaps
  - secrets
  - services
  - serviceaccounts
  - persistentvolumeclaims
  verbs:
  - create
  - update
  - patch
  - delete
- apiGroups:
  - ""
  resources:
  - events
  verbs:
  - create
  - patch
- apiGroups:
  - apps
  resources:
  - deployments
  - statefulsets
  verbs:
  - create
  - update
  - patch
  - delete
- apiGroups:
  - batch
  resources:
  - jobs
  verbs:
  - create
  - update
  - patch
  - delete
{{- end -}}

{{- define "kagent.getter.rules" -}}
- apiGroups:
  - kagent.dev
  resources:
  - agents
  - modelconfigs
  - modelproviderconfigs
  - remotemcpservers
  - mcpservers
  - toolservers
  - memories
  - sandboxagents
  - agentharnesses
  verbs:
  - get
  - list
  - watch
- apiGroups:
  - kagent.dev
  resources:
  - agents/status
  - modelconfigs/status
  - modelproviderconfigs/status
  - remotemcpservers/status
  - mcpservers/status
  - toolservers/status
  - memories/status
  - sandboxagents/status
  - agentharnesses/status
  verbs:
  - get
  - update
  - patch
- apiGroups:
  - ""
  resources:
  - configmaps
  - secrets
  - services
  - serviceaccounts
  - persistentvolumeclaims
  - pods
  - pods/log
  - endpoints
  - events
  verbs:
  - get
  - list
  - watch
- apiGroups:
  - apps
  resources:
  - deployments
  - replicasets
  - statefulsets
  verbs:
  - get
  - list
  - watch
- apiGroups:
  - batch
  resources:
  - jobs
  verbs:
  - get
  - list
  - watch
- apiGroups:
  - rbac.authorization.k8s.io
  resources:
  - roles
  - rolebindings
  verbs:
  - get
  - list
  - watch
{{- end -}}
