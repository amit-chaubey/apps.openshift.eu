#!/bin/bash
# Render assertions for charts/oh-my-openagent: gateway auth, generated OmO config, image source
set -u
cd "$(dirname "$0")/.."
C=charts/oh-my-openagent
fail=0
render() { helm template t "$C" --namespace test "$@" 2>&1; }
# check <label> <python expression>; ctr(name) one container, envs(c) its env names,
# cm(key) parsed JSON of a key of ConfigMap t, kinds the rendered kinds
check() {
  echo "$OUT" | python3 -c '
import json, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
kinds = [d["kind"] for d in docs]
pod = [d for d in docs if d["kind"] == "Deployment"][0]["spec"]["template"]["spec"]
names = [c["name"] for c in pod["containers"]]
def ctr(name): return [c for c in pod["containers"] if c["name"] == name][0]
def envs(c): return [e["name"] for e in c.get("env", [])]
def cm(key): return json.loads([d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "t"][0]["data"][key])
sys.exit(0 if ('"$2"') else 1)
' 2>/dev/null || { echo "FAIL $1: $2"; fail=1; }
}

G=(--set gateway.upstream=https://api.example.com --set gateway.provider=gw
   --set 'gateway.models[0]=vendor/big' --set 'gateway.models[1].id=small' --set 'gateway.models[1].contextWindow=64000')

OUT=$(render "${G[@]}")
check "token proxy by default" 'names == ["terminal", "oauth-proxy", "token-proxy"]'
check "OmO calls the token proxy" 'cm("models.json")["providers"]["gw"]["baseUrl"] == "http://127.0.0.1:8099/v1"'
check "no key in the pod" '"GATEWAY_API_KEY" not in envs(ctr("terminal")) and "Secret" in kinds and len([k for k in kinds if k == "Secret"]) == 1'
check "models" '[m["id"] for m in cm("models.json")["providers"]["gw"]["models"]] == ["vendor/big", "small"]'
check "default model pinned" 'cm("omo.jsonc")["model_profile"] == "gw/vendor/big" and cm("settings.json")["defaultModel"] == "vendor/big"'
check "telemetry off" 'cm("omo.jsonc")["telemetry"] == {"enabled": False} and cm("omo.jsonc")["device_service"] == {"enabled": False}'
check "token audience" '[v for v in pod["volumes"] if v["name"] == "api-token"][0]["projected"]["sources"][0]["serviceAccountToken"]["audience"] == "api.example.com"'
check "image built in the project" '"BuildConfig" in kinds and ctr("terminal")["image"].startswith("image-registry.openshift-image-registry.svc:5000/test/t-terminal:")'
check "OmO version from values" '{e["name"]: e.get("value") for e in ctr("terminal")["env"]}["OMO_VERSION"] == "5.1.70"'

OUT=$(render "${G[@]}" --set gateway.auth=apiKey --set secrets.gatewayApiKey=k --set gateway.defaultModel=small --set omo.config.model_profile=x/y)
check "api key mode has no token proxy" 'names == ["terminal", "oauth-proxy"]'
check "api key from the Secret" '[e for e in ctr("terminal")["env"] if e["name"] == "GATEWAY_API_KEY"][0]["valueFrom"]["secretKeyRef"] == {"name": "t-gateway-api-key", "key": "api-key"}'
check "key by reference only" 'cm("models.json")["providers"]["gw"]["apiKey"] == "$GATEWAY_API_KEY" and cm("models.json")["providers"]["gw"]["baseUrl"] == "https://api.example.com/v1"'
check "omo.config wins" 'cm("omo.jsonc")["model_profile"] == "x/y" and cm("settings.json")["defaultModel"] == "small"'

OUT=$(render "${G[@]}" --set gateway.auth=apiKey --set secrets.type=externalSecret --set secrets.externalSecret.storeName=v)
check "api key from the vault" 'kinds.count("ExternalSecret") == 2 and "Secret" not in kinds'
check "vault cookie hashed to a length oauth-proxy takes" '"sha256sum | trunc 32" in [d for d in docs if d["metadata"]["name"] == "t-cookie-secret"][0]["spec"]["target"]["template"]["data"]["cookie-secret"]'

OUT=$(render)
check "no provider without a gateway" 'cm("models.json") == {"providers": {}} and "model_profile" not in cm("omo.jsonc") and names == ["terminal", "oauth-proxy"]'

OUT=$(render --set gateway.auth=apiKey --set secrets.gatewayApiKey=k)
echo "$OUT" | grep -q "gateway.upstream is required" || { echo "FAIL api key without upstream"; fail=1; }

OUT=$(render "${G[@]}" --set omo.workdir=../etc)
echo "$OUT" | grep -q "workdir" || { echo "FAIL workdir outside HOME accepted"; fail=1; }

OUT=$(render "${G[@]}" --set builds.enabled=false --set terminal.image=registry.example.com/omo-terminal:1)
check "prebuilt image" '"BuildConfig" not in kinds and ctr("terminal")["image"] == "registry.example.com/omo-terminal:1"'

OUT=$(render --set builds.enabled=false)
echo "$OUT" | grep -q "terminal.image is required" || { echo "FAIL no image without builds"; fail=1; }

# start.sh sets the MCP identity key back to 0600 after the kubelet's fsGroup change,
# and creates nothing when OmO has not written the key yet
keylines=$(grep -E '^key=|"\$key"' "$C/files/start.sh")
T=$(mktemp -d)
mkdir -p "$T/.omo/agent"
HOME=$T bash -euc "$keylines" && [ ! -e "$T/.omo/agent/config-identity.key" ] || { echo "FAIL key step without a key"; fail=1; }
install -m 0660 /dev/null "$T/.omo/agent/config-identity.key"
HOME=$T bash -euc "$keylines" && [ "$(stat -c %a "$T/.omo/agent/config-identity.key")" = 600 ] || { echo "FAIL identity key mode not reset to 0600"; fail=1; }
rm -rf "$T"

[ $fail = 0 ] && echo "PASS oh-my-openagent" || exit 1
