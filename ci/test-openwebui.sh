#!/bin/bash
# Render assertions for charts/openwebui
set -u
cd "$(dirname "$0")/.."
C=charts/openwebui
fail=0
BASE=(--set person.name=sam --set person.email=sam@example.com --set route.host=chat.example.com)
render() { helm template t "$C" --namespace test "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }
# check <label> <python expression>; obj(kind, name) returns one rendered object,
# pod() the pod spec, ctr(name) one container, env(name) its plain env
check() {
  echo "$OUT" | python3 -c '
import base64, json, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
def objs(kind): return [d for d in docs if d["kind"] == kind]
def obj(kind, name=None):
    for d in objs(kind):
        if name is None or d["metadata"]["name"] == name: return d
    return {}
def pod(): return obj("Deployment")["spec"]["template"]["spec"]
def ctr(name):
    for c in pod()["containers"]:
        if c["name"] == name: return c
    return {}
def env(name): return {e["name"]: e.get("value") for e in ctr(name).get("env", [])}
def envref(name, var):
    for e in ctr(name).get("env", []):
        if e["name"] == var: return e.get("valueFrom", {}).get("secretKeyRef")
def vol(name):
    for v in pod().get("volumes", []):
        if v["name"] == name: return v
    return {}
def args(): return ctr("oauth-proxy").get("args", [])
def arg(flag): return [a.split("=", 1)[1] for a in args() if a.split("=", 1)[0] == flag]
def sat(): return vol("api-token")["projected"]["sources"][0]["serviceAccountToken"]
def secret(name, key): return base64.b64decode(obj("Secret", name)["data"][key]).decode()
sys.exit(0 if ('"$2"') else 1)
' 2>/dev/null || { echo "FAIL $1: $2"; fail=1; }
}

OUT=$(render "${BASE[@]}")
check kinds '{d["kind"] for d in docs} == {"Deployment", "PersistentVolumeClaim", "ConfigMap", "Service", "Route", "NetworkPolicy", "Secret", "ServiceAccount"}'
check namespace 'all(d["metadata"]["namespace"] == "test" for d in docs)'
check names 'obj("Deployment")["metadata"]["name"] == "sam-openwebui" and pod()["serviceAccountName"] == "sam-openwebui"'
check "chart labels" 'all(d["metadata"]["labels"]["app.kubernetes.io/name"] == "openwebui" for d in docs)'
check containers '[c["name"] for c in pod()["containers"]] == ["openwebui", "oauth-proxy", "token-proxy"]'
check image 'ctr("openwebui")["image"] == "ghcr.io/open-webui/open-webui:v0.12.3"'
# single replica on the volume: the old pod stops first
check recreate 'obj("Deployment")["spec"]["strategy"] == {"type": "Recreate"} and obj("Deployment")["spec"]["replicas"] == 1'
check pvc 'obj("PersistentVolumeClaim")["spec"]["accessModes"] == ["ReadWriteOnce"] and obj("PersistentVolumeClaim")["spec"]["resources"]["requests"]["storage"] == "10Gi" and "storageClassName" not in obj("PersistentVolumeClaim")["spec"]'
check "pvc kept" 'obj("PersistentVolumeClaim")["metadata"]["annotations"] == {"argocd.argoproj.io/sync-options": "Delete=false,Prune=false", "helm.sh/resource-policy": "keep"}'
check "data mount" 'vol("data")["persistentVolumeClaim"]["claimName"] == "sam-openwebui-data" and ctr("openwebui")["volumeMounts"] == [{"name": "data", "mountPath": "/app/backend/data"}] and env("openwebui")["DATA_DIR"] == "/app/backend/data"'
# Open WebUI trusts the proxy header, so it must not be reachable around the proxy
check loopback 'env("openwebui")["HOST"] == "127.0.0.1" and env("openwebui")["PORT"] == "8080" and "ports" not in ctr("openwebui")'
check "probes in container" 'all("exec" in ctr("openwebui")[p] and "http://127.0.0.1:8080/" in " ".join(ctr("openwebui")[p]["exec"]["command"]) for p in ("startupProbe", "readinessProbe", "livenessProbe"))'
check "trusted header" 'env("openwebui")["WEBUI_AUTH_TRUSTED_EMAIL_HEADER"] == "X-Forwarded-Email" and env("openwebui")["WEBUI_AUTH_TRUSTED_NAME_HEADER"] == "X-Forwarded-User"'
check "signup off" 'env("openwebui")["ENABLE_SIGNUP"] == "false" and env("openwebui")["DEFAULT_USER_ROLE"] == "pending"'
check "signout" 'env("openwebui")["WEBUI_AUTH_SIGNOUT_REDIRECT_URL"] == "/oauth/sign_out"'
check models 'env("openwebui")["OPENAI_API_BASE_URLS"] == "http://127.0.0.1:8099/v1" and env("openwebui")["ENABLE_OPENAI_API"] == "true" and env("openwebui")["ENABLE_OLLAMA_API"] == "false"'
check telemetry 'env("openwebui")["SCARF_NO_ANALYTICS"] == "true" and env("openwebui")["DO_NOT_TRACK"] == "true" and env("openwebui")["ANONYMIZED_TELEMETRY"] == "false" and env("openwebui")["ENABLE_VERSION_UPDATE_CHECK"] == "false"'
check "webui url" 'env("openwebui")["WEBUI_URL"] == "https://chat.example.com" and env("openwebui")["WEBUI_SESSION_COOKIE_SECURE"] == "true"'
check "secret key ref" 'envref("openwebui", "WEBUI_SECRET_KEY") == {"name": "sam-openwebui-secret-key", "key": "secret-key"}'
check "unique env" 'len({e["name"] for e in ctr("openwebui")["env"]}) == len(ctr("openwebui")["env"])'
check "no ports open but the proxy" '[p["containerPort"] for c in pod()["containers"] for p in c.get("ports", [])] == [4180]'
check "security context" 'all(c["securityContext"] == {"allowPrivilegeEscalation": False, "runAsNonRoot": True, "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"}} for c in pod()["containers"]) and "runAsUser" not in json.dumps(pod())'
# login: cluster OAuth server, the instance ServiceAccount as client, owner email only
check "oauth provider" 'arg("--provider") == ["openshift"] and arg("--openshift-service-account") == ["sam-openwebui"]'
check "oauth upstream" 'arg("--upstream") == ["http://127.0.0.1:8080"] and arg("--upstream-timeout") == ["3600s"] and arg("--http-address") == ["0.0.0.0:4180"] and arg("--https-address") == [""]'
check "oauth headers" 'arg("--pass-user-headers") == ["true"] and arg("--pass-basic-auth") == ["false"] and not arg("--config")'
check "oauth emails" 'arg("--authenticated-emails-file") == ["/etc/openwebui/emails.txt"] and arg("--email-domain") == [""] and obj("ConfigMap")["data"]["emails.txt"].split() == ["sam@example.com"]'
check "oauth cookie" 'arg("--cookie-secure") == ["true"] and arg("--cookie-name") == ["_sam_openwebui"] and envref("oauth-proxy", "OAUTH2_PROXY_COOKIE_SECRET") == {"name": "sam-openwebui-cookie-secret", "key": "cookie-secret"}'
check "oauth no groups" 'not arg("--openshift-sar") and not objs("Role") and not objs("RoleBinding")'
check "oauth image" 'ctr("oauth-proxy")["image"] == "image-registry.openshift-image-registry.svc:5000/openshift/oauth-proxy:v4.4"'
check "redirect reference" 'json.loads(obj("ServiceAccount")["metadata"]["annotations"]["serviceaccounts.openshift.io/oauth-redirectreference.primary"]) == {"kind": "OAuthRedirectReference", "apiVersion": "v1", "reference": {"kind": "Route", "name": "sam-openwebui"}}'
check "generated secrets" 'len(secret("sam-openwebui-cookie-secret", "cookie-secret")) == 32 and len(secret("sam-openwebui-secret-key", "secret-key")) == 48'
# token proxy: projected token, no client secret
check "projected token" 'sat()["expirationSeconds"] == 3600 and sat()["path"] == "token" and "audience" not in sat()'
check "token proxy env" 'env("token-proxy")["TOKEN_FILE"] == "/var/run/secrets/api-token/token" and env("token-proxy")["PORT"] == "8099" and env("token-proxy")["UPSTREAM"] == ""'
check "token proxy code" 'obj("ConfigMap")["data"]["token-proxy.py"] == open("charts/opencode-agent/files/token-proxy.py").read()'
check route 'obj("Route")["spec"]["host"] == "chat.example.com" and obj("Route")["spec"]["to"]["name"] == "sam-openwebui" and obj("Route")["spec"]["tls"]["termination"] == "edge" and obj("Route")["metadata"]["annotations"]["haproxy.router.openshift.io/timeout"] == "3600s"'
check "route cert-manager" 'obj("Route")["metadata"]["annotations"]["cert-manager.io/issuer-kind"] == "ClusterIssuer" and obj("Route")["metadata"]["annotations"]["cert-manager.io/issuer-name"] == "letsencrypt"'
check service 'obj("Service")["spec"]["ports"] == [{"name": "http", "port": 4180, "targetPort": "ui", "protocol": "TCP"}] and obj("Service")["spec"]["selector"] == obj("Deployment")["spec"]["selector"]["matchLabels"]'
check "network policy" '[r["ports"][0]["port"] for r in obj("NetworkPolicy")["spec"]["ingress"]] == [4180] and obj("NetworkPolicy")["spec"]["podSelector"]["matchLabels"] == obj("Deployment")["spec"]["selector"]["matchLabels"]'
for k in ClusterRole ClusterRoleBinding Namespace Ingress ExternalSecret KeycloakClient; do hasnt default "kind: $k"; done
for s in OAUTH_CLIENT_SECRET OPENID_PROVIDER_URL oauth2-proxy runAsUser; do hasnt default "$s"; done
[ "$(echo "$OUT" | grep -c 'kind: Secret$')" = 2 ] || { echo "FAIL default: two Secrets expected"; fail=1; }

# no values at all: the gate renders this, the release name names the instance
OUT=$(render)
check "no values" 'obj("Deployment")["metadata"]["name"] == "t-openwebui" and obj("ConfigMap")["data"]["emails.txt"].strip() == "" and "host" not in obj("Route")["spec"] and "WEBUI_URL" not in env("openwebui")'
check "no values route" '"cert-manager.io/issuer-kind" not in obj("Route")["metadata"].get("annotations", {})'

OUT=$(render "${BASE[@]}" --set gateway.upstream=https://api.example.com)
check upstream 'env("token-proxy")["UPSTREAM"] == "https://api.example.com" and sat()["audience"] == "api.example.com"'
OUT=$(render "${BASE[@]}" --set gateway.upstream=https://api.example.com:8443 --set gateway.audience=other)
check "audience override" 'sat()["audience"] == "other"'
OUT=$(render "${BASE[@]}" --set gateway.upstream=https://api.example.com:8443)
check "audience port" 'sat()["audience"] == "api.example.com"'

OUT=$(render "${BASE[@]}" --set 'oauth.allowedGroups={team-admins,ops}')
check "group sar" 'json.loads(arg("--openshift-sar")[0]) == [{"namespace": "test", "resource": "services/proxy", "resourceName": "sam-openwebui", "verb": "login"}]'
check "login role" 'obj("Role", "sam-openwebui-login")["rules"] == [{"apiGroups": [""], "resources": ["services/proxy"], "resourceNames": ["sam-openwebui"], "verbs": ["login"]}]'
check "login binding" '[s["name"] for s in obj("RoleBinding", "sam-openwebui-login")["subjects"]] == ["team-admins", "ops"] and obj("RoleBinding")["roleRef"]["name"] == "sam-openwebui-login"'

OUT=$(render "${BASE[@]}" --set oauth.ownerEmail=Owner@example.org)
check "owner override" 'obj("ConfigMap")["data"]["emails.txt"].split() == ["Owner@example.org"]'

OUT=$(render "${BASE[@]}" --set openwebui.title=Chat --set openwebui.persistentConfig=false --set openwebui.env.ENABLE_SIGNUP=true --set openwebui.env.RAG_TOP_K=5)
check "env override" 'env("openwebui")["WEBUI_NAME"] == "Chat" and env("openwebui")["ENABLE_PERSISTENT_CONFIG"] == "false" and env("openwebui")["ENABLE_SIGNUP"] == "true" and env("openwebui")["RAG_TOP_K"] == "5" and len({e["name"] for e in ctr("openwebui")["env"]}) == len(ctr("openwebui")["env"])'

OUT=$(render "${BASE[@]}" --set storage.accessMode=ReadWriteMany --set storage.className=fast --set storage.size=1Gi)
check "storage values" 'obj("PersistentVolumeClaim")["spec"] == {"accessModes": ["ReadWriteMany"], "storageClassName": "fast", "resources": {"requests": {"storage": "1Gi"}}} and obj("Deployment")["spec"]["strategy"]["type"] == "Recreate"'

OUT=$(render "${BASE[@]}" --set secrets.cookieSecret=placeholder-of-forty-one-characters-long --set secrets.secretKey=k1)
check "secret values" 'len(secret("sam-openwebui-cookie-secret", "cookie-secret")) == 32 and secret("sam-openwebui-secret-key", "secret-key") == "k1"'

OUT=$(render "${BASE[@]}" --set networkPolicy.enabled=false --set route.labels.x=y)
check "no network policy" 'not objs("NetworkPolicy")'
check "route labels" '[d["kind"] for d in docs if "x" in d["metadata"]["labels"]] == ["Route"]'

OUT=$(render "${BASE[@]}" --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault-test --set secrets.externalSecret.items.secretKey=item1)
check external 'sorted(e["metadata"]["name"] for e in objs("ExternalSecret")) == ["sam-openwebui-cookie-secret", "sam-openwebui-secret-key"] and not objs("Secret")'
check "external items" 'obj("ExternalSecret", "sam-openwebui-secret-key")["spec"]["data"][0]["remoteRef"]["key"] == "item1" and obj("ExternalSecret", "sam-openwebui-cookie-secret")["spec"]["data"][0]["remoteRef"]["key"] == "sam-openwebui-cookie-secret"'
check "external template" 'obj("ExternalSecret", "sam-openwebui-secret-key")["spec"]["target"]["template"]["data"] == {"secret-key": "{{ .value }}"} and obj("ExternalSecret", "sam-openwebui-cookie-secret")["spec"]["target"]["template"]["data"] == {"cookie-secret": "{{ .value }}"}'
check "external defaults" 'all((r["remoteRef"]["conversionStrategy"], r["remoteRef"]["decodingStrategy"], r["remoteRef"]["metadataPolicy"], r["remoteRef"]["nullBytePolicy"]) == ("Default", "None", "None", "Ignore") for e in objs("ExternalSecret") for r in e["spec"]["data"])'
OUT=$(render "${BASE[@]}" --set secrets.type=externalSecret)
has "required storeName" "secrets.externalSecret.storeName is required"

# invalid values fail the render
NL=$'a@example.org\nb@example.org'
for v in route.timeout=1d oauth.ownerEmail=owner person.email=sam "person.email=$NL" storage.accessMode=ReadWriteOncePod person.name=Sam openwebui.foo=bar secrets.type=vault; do
  if helm template t "$C" --namespace test "${BASE[@]}" --set-string "$v" >/dev/null 2>&1; then
    echo "FAIL invalid value accepted: ${v%%=*}"; fail=1
  fi
done

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
