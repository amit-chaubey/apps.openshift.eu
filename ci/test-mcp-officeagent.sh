#!/bin/bash
# Render assertions for charts/mcp-officeagent and its mcp-auth token check
set -u
cd "$(dirname "$0")/.."
C=charts/mcp-officeagent
[ -d "$C/charts" ] || helm dependency build "$C" >/dev/null
fail=0
render() { helm template office "$C" --namespace test "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }
# arguments of the auth sidecar, one per line
args() { echo "$OUT" | python3 -c 'import sys,yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d["kind"] == "Deployment":
        for c in d["spec"]["template"]["spec"]["containers"]:
            if c["name"] == "auth": print("\n".join(c["args"]))'; }
arg()   { args | grep -qxF -- "$2" || { echo "FAIL $1: sidecar lacks argument: $2"; fail=1; }; }
noarg() { args | grep -qxF -- "$2" && { echo "FAIL $1: sidecar has argument: $2"; fail=1; }; }

# default: no token check, the server answers on the Service port itself
OUT=$(render)
has default "value: http://0.0.0.0:13080"
has default "containerPort: 13080"
has default "targetPort: mcp"
hasnt default "kind: ConfigMap"
hasnt default "kind: Secret"
hasnt default "name: auth"
hasnt default "port: 443"

# auth without an issuer: fail closed, every request but the health paths gets 401
OUT=$(render --set auth.enabled=true)
has noissuer "value: http://127.0.0.1:13081"
has noissuer "image: \"quay.io/oauth2-proxy/oauth2-proxy:v7.15.5@sha256:"
has noissuer "kind: Secret"
has noissuer "name: officeagent-auth"
hasnt noissuer "kind: ConfigMap"
hasnt noissuer "port: 443"
arg noissuer "--oidc-issuer-url=https://issuer.invalid"
arg noissuer "--skip-oidc-discovery=true"
arg noissuer "--client-id=test"
arg noissuer "--skip-auth-route=GET=^/healthz$"
arg noissuer "--api-route=^/"
arg noissuer "--skip-jwt-bearer-tokens=true"

# auth with issuer, audiences as templates and groups
OUT=$(render --set auth.enabled=true --set auth.issuerUrl=https://login.example.com/realms/demo/ \
  --set 'auth.allowedGroups={demo-admins,demo-users}' \
  -f <(printf 'auth:\n  audiences:\n  - "{{ .Release.Namespace }}-nextcloud"\n  - broker\n'))
arg issuer "--oidc-issuer-url=https://login.example.com/realms/demo"
arg issuer "--client-id=test-nextcloud"
arg issuer "--oidc-extra-audience=broker"
arg issuer "--allowed-group=demo-admins"
arg issuer "--allowed-group=demo-users"
arg issuer "--oidc-groups-claim=groups"
arg issuer "--upstream=http://127.0.0.1:13081/"
arg issuer "--upstream=file:///etc/mcp-auth/#/.well-known/"
arg issuer "--http-address=0.0.0.0:13080"
noarg issuer "--skip-oidc-discovery=true"
has issuer "kind: ConfigMap"
has issuer '\"authorization_servers\":[\"https://login.example.com/realms/demo\"]'
has issuer '\"resource\":\"http://officeagent.test.svc:13080/\"'
has issuer "port: 443"
has issuer "path: /oauth2/ping"
has issuer "readOnlyRootFilesystem: true"

# resourceUrl, an existing Secret and allowed groups as templates
OUT=$(render --set auth.enabled=true --set auth.issuerUrl=https://login.example.com/realms/demo \
  --set auth.resourceUrl=https://office.example.com/mcp/officeagent --set auth.existingSecret=mine \
  -f <(printf 'auth:\n  allowedGroups:\n  - "{{ splitList \\"-\\" .Release.Namespace | first }}-admins"\n'))
has resource '\"resource\":\"https://office.example.com/mcp/officeagent\"'
has existing "name: mine"
hasnt existing "kind: Secret"
arg tplgroup "--allowed-group=test-admins"

# a cookie secret given as a value is used as is
OUT=$(render --set auth.enabled=true --set auth.cookieSecret=aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb)
has cookie "cookie-secret: \"$(printf aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb | base64 -w0)\""

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
