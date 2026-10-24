{{/*
Rules of the upstream writer and getter Roles. Template names are global and the parent
chart's are read last, so these replace upstream's wildcard rules, which a tenant cannot
grant. The platform kagent-operator grants the controller's full rules in this namespace.
*/}}
{{- define "kagent.writer.rules" -}}
- apiGroups:
  - kagent.dev
  resources:
  - agents
  - modelconfigs
  - remotemcpservers
  - mcpservers
  verbs:
  - create
  - update
  - patch
  - delete
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
  - remotemcpservers
  - mcpservers
  verbs:
  - get
  - list
  - watch
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
