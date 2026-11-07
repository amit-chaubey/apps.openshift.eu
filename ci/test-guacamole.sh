#!/bin/bash
# Render assertions for charts/guacamole
set -u
cd "$(dirname "$0")/.."
C=charts/guacamole
fail=0
render() { helm template t "$C" --namespace test "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }
# check <label> <python expression>; obj(kind, name) returns one rendered object,
# env(container) the env of a Deployment container as a dict of plain values
check() {
  echo "$OUT" | python3 -c '
import json, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
def objs(kind): return [d for d in docs if d["kind"] == kind]
def obj(kind, name=None):
    for d in objs(kind):
        if name is None or d["metadata"]["name"] == name: return d
    return {}
def pod(deploy="t-guacamole"): return obj("Deployment", deploy)["spec"]["template"]["spec"]
def ctr(name, deploy="t-guacamole"):
    for c in pod(deploy)["containers"]:
        if c["name"] == name: return c
    return {}
def env(name, deploy="t-guacamole"): return {e["name"]: e.get("value") for e in ctr(name, deploy).get("env", [])}
def mounts(name): return [m["name"] for m in ctr(name).get("volumeMounts", [])]
def sql(): return obj("ConfigMap", "t-guacamole-init")["data"]["init.sql"]
def kiosk(): return ctr("kiosk", "t-kiosk")
def kmounts(): return {m["mountPath"]: m for m in kiosk().get("volumeMounts", [])}
def pol(): return json.loads(obj("ConfigMap", "t-kiosk")["data"]["policies.json"])["policies"]
def start(): return obj("ConfigMap", "t-kiosk")["data"]["start.sh"]
def rules(): return obj("EgressFirewall", "default")["spec"]["egress"]
sys.exit(0 if eval(sys.argv[1]) else 1)
' "$2" || { echo "FAIL $1: $2"; fail=1; }
}

NAMESPACED="['ConfigMap','Deployment','ExternalSecret','Job','NetworkPolicy','ObjectBucketClaim','PersistentVolumeClaim','Cluster','Route','Secret','Service']"

OUT=$(render)
check default "[c['name'] for c in pod()['containers']] == ['guacamole', 'guacd']"
check default "'recordings' in mounts('guacamole') and 'recordings' in mounts('guacd')"
check default "env('guacamole')['RECORDING_ENABLED'] == 'true'"
check default "env('guacamole')['RECORDING_SEARCH_PATH'] == '/var/lib/guacamole/recordings'"
check default "env('guacamole')['GUACD_HOSTNAME'] == 'localhost'"
check default "env('guacamole')['WEBAPP_CONTEXT'] == 'ROOT'"
check default "env('guacamole')['POSTGRESQL_ENABLED'] == 'true'"
check default "[e['valueFrom']['secretKeyRef'] for e in ctr('guacamole')['env'] if e['name'] == 'POSTGRESQL_PASSWORD'] == [{'name': 't-guacamole-db-app', 'key': 'password'}]"
check default "'OPENID_ENABLED' not in env('guacamole')"
check default "ctr('guacamole')['image'] == 'guacamole/guacamole:1.6.0' and ctr('guacd')['image'] == 'guacamole/guacd:1.6.0'"
# guacd authenticates nobody: only the web container in the same pod may reach it
check default "'127.0.0.1' in ctr('guacd')['command']"
check default "'fsGroup' not in pod().get('securityContext', {}) and 'runAsUser' not in pod().get('securityContext', {})"
check default "obj('Deployment')['spec']['strategy']['type'] == 'Recreate'"
check default "obj('Service', 't-guacamole')['spec']['ports'][0]['port'] == 8080"
check default "obj('Cluster', 't-guacamole-db')['spec']['instances'] == 1"
check default "obj('PersistentVolumeClaim', 't-guacamole-recordings')['spec']['accessModes'] == ['ReadWriteOnce']"
check default "obj('Route')['spec']['tls']['termination'] == 'edge'"
check default "set(obj('Secret', 't-guacamole')['data']) == {'adminPassword', 'vncPassword'}"
check default "obj('Job', 't-guacamole-init')['metadata']['annotations']['helm.sh/hook'] == 'post-install,post-upgrade'"
# a finished Job is removed, a failed one is kept for its logs
check default "obj('Job', 't-guacamole-init')['metadata']['annotations']['helm.sh/hook-delete-policy'] == 'before-hook-creation,hook-succeeded'"
check default "'/opt/guacamole/bin/initdb.sh --postgresql' in ' '.join(obj('Job')['spec']['template']['spec']['initContainers'][0]['command'])"
check default "'guacamole_user' in obj('ConfigMap', 't-guacamole-init')['data']['init.sh']"
check default "'UPDATE guacamole_user' in sql() and \"name = 'guacadmin'\" in sql()"
check default "obj('NetworkPolicy')['spec']['ingress'] == [{'from': [{'namespaceSelector': {'matchLabels': {'network.openshift.io/policy-group': 'ingress'}}}], 'ports': [{'protocol': 'TCP', 'port': 8080}]}]"
check default "not objs('ObjectBucketClaim') and not objs('ExternalSecret')"
check default "all(d['kind'] in $NAMESPACED for d in docs)"
check default "all('namespace' not in d['metadata'] or d['metadata']['namespace'] == 'test' for d in docs)"

CONN='connections=[{"name":"desk","protocol":"vnc","hostname":"kiosk","port":5900,"parameters":{"disable-copy":"true","disable-paste":"true","recording-include-keys":"false","color-depth":"24"},"secretParameters":{"password":"vncPassword"},"users":["guest@example.com","o'"'"'brien@example.com"]}]'
FULL="--set oidc.enabled=true --set oidc.issuerUrl=https://login.example.com/realms/x --set oidc.clientId=desk --set route.hostname=desk.example.com --set recording.s3.enabled=true --set recording.s3.storageClassName=objects"

OUT=$(render $FULL --set-json "$CONN")
check full "[c['name'] for c in pod()['containers']] == ['guacamole', 'guacd', 'rclone']"
check full "all('recordings' in mounts(c) for c in ['guacamole', 'guacd', 'rclone'])"
check full "ctr('rclone')['image'] == 'rclone/rclone:1.71'"
check full "'rclone copy' in ' '.join(ctr('rclone')['args']) and 'rclone sync' not in ' '.join(ctr('rclone')['args'])"
check full "'sleep 300' in ' '.join(ctr('rclone')['args'])"
# the bucket exists and its user may not create one (RGW TooManyBuckets)
check full "env('rclone')['RCLONE_CONFIG_S3_NO_CHECK_BUCKET'] == 'true'"
check full "{e['valueFrom']['secretKeyRef']['name'] for e in ctr('rclone')['env'] if 'valueFrom' in e and 'secretKeyRef' in e['valueFrom']} == {'t-guacamole-recordings'}"
check full "{e['valueFrom']['configMapKeyRef']['name'] for e in ctr('rclone')['env'] if 'valueFrom' in e and 'configMapKeyRef' in e['valueFrom']} == {'t-guacamole-recordings'}"
check full "obj('ObjectBucketClaim', 't-guacamole-recordings')['spec']['storageClassName'] == 'objects'"
# the in-cluster endpoint is signed by the service CA; the system roots stay
check full "env('rclone')['SSL_CERT_DIR'] == '/etc/rclone/ca'"
check full "'bucket-ca' in mounts('rclone') and 'bucket-ca' not in mounts('guacamole')"
check full "[v['configMap'] for v in pod()['volumes'] if v['name'] == 'bucket-ca'] == [{'name': 'openshift-service-ca.crt', 'optional': True}]"
check full "env('guacamole')['OPENID_ENABLED'] == 'true'"
check full "env('guacamole')['OPENID_USERNAME_CLAIM_TYPE'] == 'preferred_username'"
check full "env('guacamole')['OPENID_ISSUER'] == 'https://login.example.com/realms/x'"
check full "env('guacamole')['OPENID_CLIENT_ID'] == 'desk'"
check full "env('guacamole')['OPENID_REDIRECT_URI'] == 'https://desk.example.com/'"
check full "env('guacamole')['OPENID_AUTHORIZATION_ENDPOINT'] == 'https://login.example.com/realms/x/protocol/openid-connect/auth'"
check full "env('guacamole')['OPENID_JWKS_ENDPOINT'] == 'https://login.example.com/realms/x/protocol/openid-connect/certs'"
# the issuer first: no login form, straight to the identity provider
check full "env('guacamole')['EXTENSION_PRIORITY'] == 'openid'"
check full "obj('Route')['spec']['host'] == 'desk.example.com' and obj('Route')['metadata']['annotations']['cert-manager.io/issuer-kind'] == 'ClusterIssuer'"
for p in "('disable-copy', 'true')" "('disable-paste', 'true')" "('recording-include-keys', 'false')" "('color-depth', '24')" "('hostname', 'kiosk')" "('port', '5900')" "('recording-path', '\${HISTORY_PATH}/\${HISTORY_UUID}')" "('create-recording-path', 'true')" "('recording-name', '\${GUAC_DATE}-\${GUAC_TIME}')" "('password', :'secret_0_0')"; do
  check "full parameter $p" "\"$p\" in sql()"
done
check full "\"\\\\set secret_0_0 \`cat /secrets/vncPassword\`\" in sql()"
check full "\"'guest@example.com'\" in sql() and \"'o''brien@example.com'\" in sql()"
check full "\"'READ'\" in sql() and 'DELETE FROM guacamole_system_permission' in sql()"
check full "\"connection_name = 'desk'\" in sql()"
check full "all(d['kind'] in $NAMESPACED for d in docs)"
hasnt full "kind: Namespace"

OUT=$(render $FULL --set recording.s3.caConfigMap=)
check "no bucket ca" "'SSL_CERT_DIR' not in env('rclone') and 'bucket-ca' not in [v['name'] for v in pod()['volumes']]"

# an explicit endpoint wins over the Keycloak default
OUT=$(render $FULL --set oidc.authorizationEndpoint=https://idp.example.com/auth --set oidc.jwksEndpoint=https://idp.example.com/jwks --set oidc.primary=false)
check endpoints "env('guacamole')['OPENID_AUTHORIZATION_ENDPOINT'] == 'https://idp.example.com/auth'"
check endpoints "env('guacamole')['OPENID_JWKS_ENDPOINT'] == 'https://idp.example.com/jwks'"
check endpoints "env('guacamole')['EXTENSION_PRIORITY'] == '*, openid'"

# administrators: every system permission, READ on every connection, marked
# so that a name removed from the list loses exactly what the chart gave it
OUT=$(render $FULL --set-json "$CONN" --set-json 'admins=["boss@example.com","o'"'"'neil@example.com"]')
check admins "\"'boss@example.com'\" in sql() and \"'o''neil@example.com'\" in sql()"
for p in ADMINISTER AUDIT CREATE_CONNECTION CREATE_CONNECTION_GROUP CREATE_SHARING_PROFILE CREATE_USER CREATE_USER_GROUP; do
  check "admins $p" "\"'$p'\" in sql().split('-- administrators')[1]"
done
check admins "'FROM guacamole_entity e, guacamole_connection c' in sql().split('-- administrators')[1]"
# the mark is set, and read back by the revoke statements
check admins "sql().count(\"'guacamole-chart-admin'\") >= 4"
# administrators keep READ on a managed connection that does not list them
check admins "\"name IN ('guest@example.com', 'o''brien@example.com', 'boss@example.com', 'o''neil@example.com')\" in sql()"
check admins "sql().index('-- connection desk') < sql().index('-- administrators')"
# an empty list still revokes what an earlier list granted
OUT=$(render $FULL --set-json "$CONN")
check "admins none" "'-- administrators' in sql() and \"'guacamole-chart-admin'\" in sql()"
OUT=$(render $FULL --set-json "$CONN" --set-json 'admins=["guest@example.com"]')
has "admins and users" "guest@example.com is in admins and in the users of connection desk"
OUT=$(render $FULL --set-json 'admins=["guacadmin"]')
has "admins guacadmin" "guacadmin is the break-glass administrator"

# no clipboard unless a connection asks for it
OUT=$(render --set-json 'connections=[{"name":"plain","protocol":"vnc","hostname":"h"}]')
check "clipboard default" "\"('disable-copy', 'true')\" in sql() and \"('disable-paste', 'true')\" in sql()"
OUT=$(render --set-json 'connections=[{"name":"open","protocol":"vnc","hostname":"h","parameters":{"disable-copy":"false","disable-paste":"false"}}]')
check "clipboard explicit" "\"('disable-copy', 'false')\" in sql() and \"('disable-paste', 'false')\" in sql() and \"('disable-copy', 'true')\" not in sql() and \"('disable-paste', 'true')\" not in sql()"

OUT=$(render --set recording.enabled=false --set-json "$CONN")
check norec "[c['name'] for c in pod()['containers']] == ['guacamole', 'guacd']"
check norec "'RECORDING_ENABLED' not in env('guacamole') and not objs('PersistentVolumeClaim')"
check norec "'recording-path' not in sql()"

OUT=$(render --set oidc.enabled=true --set oidc.clientId=desk --set route.hostname=desk.example.com)
has "oidc without issuer" "oidc.issuerUrl is required"
OUT=$(render --set oidc.enabled=true --set oidc.issuerUrl=https://login.example.com/realms/x --set route.hostname=desk.example.com)
has "oidc without client" "oidc.clientId is required"
OUT=$(render --set oidc.enabled=true --set oidc.issuerUrl=https://login.example.com/realms/x --set oidc.clientId=desk)
has "oidc without hostname" "route.hostname is required"
OUT=$(render --set recording.s3.enabled=true)
has "s3 without class" "recording.s3.storageClassName is required"

OUT=$(render --set secrets.adminPassword=adm1n --set secrets.vncPassword=vncpw)
check "given secrets" "obj('Secret', 't-guacamole')['data'] == {'adminPassword': 'YWRtMW4=', 'vncPassword': 'dm5jcHc='}"
hasnt "given secrets" "adm1n"

OUT=$(render --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault-test)
check external "not [d for d in objs('Secret') if d['metadata']['name'] == 't-guacamole']"
check external "obj('ExternalSecret', 't-guacamole')['spec']['target']['name'] == 't-guacamole'"
check external "sorted(x['secretKey'] for x in obj('ExternalSecret')['spec']['data']) == ['adminPassword', 'vncPassword']"
check external "sorted(x['remoteRef']['key'] for x in obj('ExternalSecret')['spec']['data']) == ['guacamole-admin-password', 'guacamole-vnc-password']"

# hardening: guacd probes on its loopback address (a tcpSocket probe would
# dial the pod IP, where guacd does not listen), a deadline on the init Job,
# and the schema plus init.sql in one transaction (no committed guacadmin)
OUT=$(render)
check hardening "ctr('guacd')['readinessProbe']['exec']['command'] == ['nc', '-z', '127.0.0.1', '4822'] and 'tcpSocket' not in ctr('guacd')['readinessProbe']"
check hardening "ctr('guacd')['livenessProbe']['exec']['command'] == ['nc', '-z', '127.0.0.1', '4822'] and ctr('guacd')['livenessProbe']['timeoutSeconds'] == 5"
check hardening "obj('Job', 't-guacamole-init')['spec']['activeDeadlineSeconds'] == 600"
T=$(mktemp -d)
echo "$OUT" | python3 -c 'import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "t-guacamole-init":
        open(sys.argv[1] + "/init.sh", "w").write(d["data"]["init.sh"])' "$T"
mkdir "$T/bin"
printf '#!/bin/sh\nexit 0\n' > "$T/bin/pg_isready"
# psql stand-in: answers the schema probe with $FRESH, logs every other call
printf '#!/bin/sh\ncase "$*" in *-tAc*) echo "$FRESH" ;; *) echo "$*" >> "$LOG" ;; esac\n' > "$T/bin/psql"
chmod +x "$T/bin/pg_isready" "$T/bin/psql"
for fresh in t f; do
  : > "$T/log"
  PATH="$T/bin:$PATH" FRESH=$fresh LOG="$T/log" sh "$T/init.sh" >/dev/null || { echo "FAIL init.sh fresh=$fresh: exit"; fail=1; }
  want="-v ON_ERROR_STOP=1 --single-transaction -q -f /work/schema.sql -f /scripts/init.sql"
  [ $fresh = f ] && want="-v ON_ERROR_STOP=1 --single-transaction -q -f /scripts/init.sql"
  [ "$(cat "$T/log")" = "$want" ] || { echo "FAIL init.sh fresh=$fresh: psql calls: $(cat "$T/log")"; fail=1; }
done
rm -rf "$T"

# kiosk desktop: off by default, nothing of it rendered
OUT=$(render)
check "kiosk off" "not [d for d in docs if d['metadata']['name'].startswith('t-kiosk')] and not objs('EgressFirewall')"

KIOSK='--set kiosk.enabled=true --set kiosk.homepage=https://code.example.com/ --set kiosk.profileSize=2Gi'
KURLS='kiosk.allowedUrls=["https://code.example.com/*","https://login.example.com/*"]'
KDNS='kiosk.egressFirewall.allowedDnsNames=["code.example.com","login.example.com"]'
KCIDR='kiosk.egressFirewall.allowedCidrs=["10.10.10.17/32"]'
OUT=$(render $KIOSK --set-json "$KURLS" --set-json "$KDNS" --set-json "$KCIDR")
check kiosk "pol()['DisableDeveloperTools'] is True and pol()['BlockAboutConfig'] is True"
check kiosk "pol()['DisablePasswordReveal'] is True and pol()['PasswordManagerEnabled'] is True"
check kiosk "pol()['PrintingEnabled'] is False"
# Firefox has no DownloadRestrictions (a Chromium policy): no file picker, so
# Save Page As and password export cannot pick a target, and downloads go
# without a prompt to a read-only directory, where they fail
check kiosk "'DownloadRestrictions' not in pol()"
check kiosk "pol()['AllowFileSelectionDialogs'] is False"
check kiosk "pol()['DownloadDirectory'] == '/downloads' and pol()['PromptForDownloadLocation'] is False"
check kiosk "kmounts()['/downloads']['readOnly'] is True and [v for v in pod('t-kiosk')['volumes'] if v['name'] == kmounts()['/downloads']['name']][0]['emptyDir'] == {'sizeLimit': '1Mi'}"
check kiosk "'/tmp/downloads' not in start()"
check kiosk "pol()['WebsiteFilter'] == {'Block': ['<all_urls>'], 'Exceptions': ['https://code.example.com/*', 'https://login.example.com/*']}"
check kiosk "pol()['ExtensionSettings'] == {'*': {'installation_mode': 'blocked'}}"
check kiosk "pol()['DisableFirefoxAccounts'] is True and pol()['DisableProfileImport'] is True"
check kiosk "all(pol()[p] is True for p in ['BlockAboutAddons', 'BlockAboutProfiles', 'BlockAboutSupport'])"
check kiosk "pol()['Preferences']['signon.showAutoCompleteFooter'] == {'Value': False, 'Status': 'locked'}"
check kiosk "pol()['Preferences']['browser.contextual-password-manager.enabled'] == {'Value': False, 'Status': 'locked'}"
check kiosk "pol()['Preferences']['pref.privacy.disable_button.view_passwords'] == {'Value': True, 'Status': 'locked'}"
# a dropped connection (egress firewall) to a render-blocking resource must fail fast
check kiosk "pol()['Preferences']['network.http.connection-timeout'] == {'Value': 5, 'Status': 'locked'}"
check kiosk "rules()[-1] == {'type': 'Deny', 'to': {'cidrSelector': '0.0.0.0/0'}}"
check kiosk "rules()[:-1] == [{'type': 'Allow', 'to': {'dnsName': 'code.example.com'}}, {'type': 'Allow', 'to': {'dnsName': 'login.example.com'}}, {'type': 'Allow', 'to': {'cidrSelector': '10.10.10.17/32'}}]"
check kiosk "obj('EgressFirewall')['apiVersion'] == 'k8s.ovn.org/v1' and len(objs('EgressFirewall')) == 1"
check kiosk "obj('NetworkPolicy', 't-kiosk')['spec']['podSelector'] == {'matchLabels': {'app': 't-kiosk'}}"
check kiosk "obj('NetworkPolicy', 't-kiosk')['spec']['ingress'] == [{'from': [{'podSelector': {'matchLabels': {'app': 't-guacamole'}}}], 'ports': [{'protocol': 'TCP', 'port': 5900}]}]"
check kiosk "obj('Service', 't-kiosk')['spec']['ports'][0]['port'] == 5900 and obj('Service', 't-kiosk')['spec']['selector'] == {'app': 't-kiosk'}"
check kiosk "[e['valueFrom']['secretKeyRef'] for e in kiosk()['env'] if e['name'] == 'VNC_PASSWORD'] == [{'name': 't-guacamole', 'key': 'vncPassword'}]"
check kiosk "env('kiosk', 't-kiosk')['HOME'] == '/tmp' and env('kiosk', 't-kiosk')['HOMEPAGE'] == 'https://code.example.com/' and env('kiosk', 't-kiosk')['GEOMETRY'] == '1600x900'"
check kiosk "kmounts()['/usr/lib64/firefox/distribution/policies.json']['subPath'] == 'policies.json'"
check kiosk "'/profile' in kmounts() and '/dev/shm' in kmounts() and '/tmp' in kmounts()"
# probes open no VNC connection: Xvnc blacklists an address after unauthenticated connects
check kiosk "'tcpSocket' not in kiosk()['readinessProbe'] and 'tcpSocket' not in kiosk()['livenessProbe']"
check kiosk "kiosk()['readinessProbe']['exec']['command'] == ['sh', '-c', 'kill -0 \"\$(cat /tmp/xvnc.pid)\" && kill -0 \"\$(cat /tmp/firefox.pid)\"']"
check kiosk "kiosk()['livenessProbe']['exec']['command'] == ['sh', '-c', 'kill -0 \"\$(cat /tmp/xvnc.pid)\"']"
check kiosk "'echo \$xvnc > /tmp/xvnc.pid' in start() and 'echo \$ff > /tmp/firefox.pid' in start()"
# no window manager: Firefox is sized to the desktop on its command line
check kiosk "'--width \"\${GEOMETRY%x*}\" --height \"\${GEOMETRY#*x}\"' in start()"
# no typed value offered again, no tab of an earlier session
check kiosk "pol()['DisableFormHistory'] is True"
check kiosk "pol()['Preferences']['browser.sessionstore.resume_from_crash'] == {'Value': False, 'Status': 'locked'}"
check kiosk "pol()['Preferences']['browser.startup.page'] == {'Value': 1, 'Status': 'locked'}"
check kiosk "'rm -rf /profile/formhistory.sqlite* /profile/sessionstore.jsonlz4 /profile/sessionstore-backups' in start()"
check kiosk "all(f not in start() for f in ['logins.json', 'key4.db', 'cookies'])"
check kiosk "start().index('sessionstore-backups') < start().index('firefox --kiosk')"
check kiosk "'fsGroup' not in pod('t-kiosk').get('securityContext', {}) and 'runAsUser' not in pod('t-kiosk').get('securityContext', {}) and 'runAsUser' not in kiosk().get('securityContext', {})"
check kiosk "obj('Deployment', 't-kiosk')['spec']['strategy']['type'] == 'Recreate' and obj('Deployment', 't-kiosk')['spec']['replicas'] == 1"
check kiosk "kiosk()['image'] == 'image-registry.openshift-image-registry.svc:5000/test/t-kiosk:latest'"
check kiosk "obj('PersistentVolumeClaim', 't-kiosk-profile')['spec']['resources']['requests']['storage'] == '2Gi'"
check kiosk "'FROM registry.fedoraproject.org/fedora:42' in obj('BuildConfig', 't-kiosk')['spec']['source']['dockerfile']"
check kiosk "'firefox' in obj('BuildConfig', 't-kiosk')['spec']['source']['dockerfile'] and 'tigervnc-server-minimal' in obj('BuildConfig', 't-kiosk')['spec']['source']['dockerfile']"
check kiosk "obj('BuildConfig', 't-kiosk')['spec']['output']['to'] == {'kind': 'ImageStreamTag', 'name': 't-kiosk:latest'} and obj('ImageStream', 't-kiosk')"
for s in 'Xvnc :1' '-geometry' '-rfbport 5900' '-SecurityTypes VncAuth' 'firefox --kiosk' '--profile /profile' 'HOMEPAGE'; do
  check "kiosk start.sh $s" "'$s' in start()"
done
check kiosk "all(d['kind'] in $NAMESPACED + ['BuildConfig', 'ImageStream', 'EgressFirewall'] for d in docs)"

check kiosk "[r['type'] for r in rules()].count('Deny') == 1 and all(r['type'] == 'Allow' for r in rules()[:-1])"
for s in '-SendCutText=0' '-AcceptCutText=0' '-SendPrimary=0' '-SetPrimary=0'; do
  check "kiosk start.sh $s" "'$s' in start()"
done
# the password is in the passwd file only, not in the environment of Xvnc or Firefox
check kiosk "start().index('vncpasswd -f') < start().index('unset VNC_PASSWORD') < start().index('Xvnc :1') < start().index('firefox --kiosk')"

# the firewall must let Guacamole reach the issuer
OIDC="--set oidc.enabled=true --set oidc.issuerUrl=https://login.example.com/realms/x --set oidc.clientId=desk --set route.hostname=desk.example.com"
OUT=$(render $KIOSK $OIDC --set-json 'kiosk.egressFirewall.allowedDnsNames=["code.example.com"]')
has "kiosk oidc issuer blocked" "kiosk.egressFirewall.allowedDnsNames must hold login.example.com"
OUT=$(render $KIOSK $OIDC --set-json "$KDNS")
check "kiosk oidc issuer allowed" "rules()[1] == {'type': 'Allow', 'to': {'dnsName': 'login.example.com'}}"
OUT=$(render $KIOSK $OIDC --set-json "$KDNS" --set oidc.jwksEndpoint=https://keys.example.com:8443/jwks)
has "kiosk oidc jwks blocked" "kiosk.egressFirewall.allowedDnsNames must hold keys.example.com"
OUT=$(render $KIOSK $OIDC --set kiosk.egressFirewall.enabled=false)
check "kiosk oidc no firewall" "not objs('EgressFirewall') and obj('Deployment', 't-kiosk')"

# a prebuilt image: no build objects
OUT=$(render $KIOSK --set kiosk.image=quay.io/example/kiosk:1)
check "kiosk image" "not objs('BuildConfig') and not objs('ImageStream') and kiosk()['image'] == 'quay.io/example/kiosk:1'"
# replicas 0 stops the desktop and keeps its profile
OUT=$(render $KIOSK --set kiosk.replicas=0)
check "kiosk replicas" "obj('Deployment', 't-kiosk')['spec']['replicas'] == 0 and obj('PersistentVolumeClaim', 't-kiosk-profile')"
OUT=$(render $KIOSK --set kiosk.connectTimeout=10)
check "kiosk connect timeout" "pol()['Preferences']['network.http.connection-timeout'] == {'Value': 10, 'Status': 'locked'}"
OUT=$(render $KIOSK --set kiosk.connectTimeout=0)
has "kiosk connect timeout 0" "connectTimeout"
OUT=$(render $KIOSK --set kiosk.egressFirewall.enabled=false)
check "kiosk no firewall" "not objs('EgressFirewall') and obj('Deployment', 't-kiosk')"
check "kiosk empty lists" "pol()['WebsiteFilter'] == {'Block': ['<all_urls>'], 'Exceptions': []}"
# Firefox match patterns take no port: such an exception never matches
OUT=$(render $KIOSK --set-json 'kiosk.allowedUrls=["http://10.0.0.1:8000/*"]')
has "kiosk url with port" "kiosk.allowedUrls entry http://10.0.0.1:8000/* has a port"
OUT=$(render $KIOSK --set-json 'kiosk.allowedUrls=["http://10.0.0.1/*","https://*.example.com/a:b/*"]')
check "kiosk url without port" "pol()['WebsiteFilter']['Exceptions'] == ['http://10.0.0.1/*', 'https://*.example.com/a:b/*']"
OUT=$(render --set kiosk.enabled=true)
check "kiosk defaults" "rules() == [{'type': 'Deny', 'to': {'cidrSelector': '0.0.0.0/0'}}] and env('kiosk', 't-kiosk')['HOMEPAGE'] == 'about:blank'"

# watermark extension: off by default
OUT=$(render)
check "watermark off" "not obj('ConfigMap', 't-guacamole-extensions') and 'extensions' not in mounts('guacamole')"
# on: the jar as binaryData, linked by the image from the template GUACAMOLE_HOME
OUT=$(render --set watermark.enabled=true)
check watermark "set(obj('ConfigMap', 't-guacamole-extensions')['binaryData']) == {'watermark.jar'} and 'data' not in obj('ConfigMap', 't-guacamole-extensions')"
check watermark "[m for m in ctr('guacamole')['volumeMounts'] if m['name'] == 'extensions'] == [{'name': 'extensions', 'mountPath': '/etc/guacamole/extensions/watermark.jar', 'subPath': 'watermark.jar', 'readOnly': True}]"
check watermark "[v['configMap']['name'] for v in pod()['volumes'] if v['name'] == 'extensions'] == ['t-guacamole-extensions']"
check watermark "'GUACAMOLE_HOME' not in env('guacamole')"
check watermark "__import__('base64').b64decode(obj('ConfigMap', 't-guacamole-extensions')['binaryData']['watermark.jar']) == open('$C/files/watermark.jar', 'rb').read()"
# a subPath mount never refreshes: a changed jar must roll the pod
check watermark "len(obj('Deployment', 't-guacamole')['spec']['template']['metadata']['annotations']['checksum/extensions']) == 64"
OUT=$(render)
check "watermark off" "'checksum/extensions' not in (obj('Deployment', 't-guacamole')['spec']['template']['metadata'].get('annotations') or {})"
OUT=$(render --set watermark.enabled=true --set recording.enabled=false)
check "watermark norec" "[v['name'] for v in pod()['volumes']] == ['extensions'] and mounts('guacamole') == ['extensions']"
# the committed jar is the build of the committed sources
T=$(mktemp -d)
ci/build-watermark.sh "$T/watermark.jar" >/dev/null || { echo "FAIL watermark build"; fail=1; }
cmp -s "$T/watermark.jar" "$C/files/watermark.jar" || { echo "FAIL watermark jar differs from a fresh build of $C/files/watermark"; fail=1; }
ci/build-watermark.sh "$T/again.jar" >/dev/null
cmp -s "$T/watermark.jar" "$T/again.jar" || { echo "FAIL watermark build not deterministic"; fail=1; }
python3 - "$T/watermark.jar" <<'EOF' || { echo "FAIL watermark manifest"; fail=1; }
import json, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
m = json.loads(z.read("guac-manifest.json"))
assert m["guacamoleVersion"] == "1.6.0" and m["name"] and m["namespace"], m
assert m["js"] == ["watermark.js"] and m["css"] == ["watermark.css"], m
assert sorted(z.namelist()) == ["guac-manifest.json", "watermark.css", "watermark.js"], z.namelist()
js = z.read("watermark.js").decode()
assert "getCurrentUsername" in js and "60000" in js and "pointer-events" not in js
assert "$locationChangeSuccess" in js and "catch" in js
css = z.read("watermark.css").decode()
assert "position: fixed" in css and "pointer-events: none" in css
EOF
rm -rf "$T"

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
