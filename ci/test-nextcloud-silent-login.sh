#!/bin/bash
# charts/nextcloud oidc.silentLogin: renders the prepend file and runs it under PHP's web
# server against a stand-in for Nextcloud and user_oidc. Needs helm, python3 and php.
set -u
cd "$(dirname "$0")/.."
C=charts/nextcloud
command -v php >/dev/null || { echo "FAIL silent-login: php not on PATH"; exit 1; }
[ -d "$C/charts" ] || { echo "FAIL silent-login: run helm dependency build for $C first (ci/check.sh does)"; exit 1; }
fail=0
T=$(mktemp -d)
trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$T"' EXIT
API="--api-versions security.openshift.io/v1 --api-versions route.openshift.io/v1"
# silent-login.php of a render, written to $T/<name>.php
prepend() {
  helm template t "$C" --namespace test $API --set route.hostname=nc.example.org "${@:2}" | python3 -c '
import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "nextcloud-setup":
        sys.stdout.write(d["data"]["silent-login.php"])' > "$T/$1.php" || { echo "FAIL silent-login: render $1"; fail=1; }
  php -l "$T/$1.php" >/dev/null || { echo "FAIL silent-login: php syntax of $1"; fail=1; }
}
prepend on
prepend off --set oidc.silentLogin=false
prepend nosso --set sso.enabled=false
[ "$(cat "$T/off.php")" = "<?php" ] || { echo "FAIL off: the file must do nothing"; fail=1; }
[ "$(cat "$T/nosso.php")" = "<?php" ] || { echo "FAIL nosso: the file must do nothing without a provider"; fail=1; }

# the prepend reads the provider id from /var/www/html/config: point it at $T
mkdir -p "$T/config"; echo 7 > "$T/config/.chart-sso-provider"
sed "s#/var/www/html/config/#$T/config/#" "$T/on.php" > "$T/on-t.php"
# stand-in: user_oidc's login redirects to the provider, everything else is Nextcloud
cat > "$T/router.php" <<EOF
<?php
require '$T/' . getenv('PREPEND') . '.php';
\$path = (string) parse_url(\$_SERVER['REQUEST_URI'], PHP_URL_PATH);
if (preg_match('#/apps/user_oidc/login/\d+\$#', \$path)) {
    header('Location: https://idp.example.org/auth?response_type=code&client_id=nc&state=s', true, 303);
    exit;
}
echo "NEXTCLOUD \$path";
EOF
serve() {
  PREPEND=$1 php -S 127.0.0.1:$2 "$T/router.php" >/dev/null 2>&1 &
  for i in $(seq 150); do curl -s -o /dev/null "http://127.0.0.1:$2/" && return; sleep 0.1; done
  echo "FAIL silent-login: test server $1 on port $2 did not start"; exit 1
}
serve on-t 18431
serve off 18432
# req <port> <path> [curl args...]: status, Location and Set-Cookie, then the body
req() { local p=$1 u=$2; shift 2; curl -s -i "$@" "http://127.0.0.1:$p$u" | tr -d '\r'; }
NAV=(-H "Sec-Fetch-Mode: navigate")
expect() { echo "$OUT" | grep -qE -- "$2" || { echo "FAIL $1: expected /$2/ in:"; echo "$OUT" | sed 's/^/    /' | head -8; fail=1; }; }
reject() { echo "$OUT" | grep -qE -- "$2" && { echo "FAIL $1: unexpected /$2/ in:"; echo "$OUT" | sed 's/^/    /' | head -8; fail=1; }; }

OUT=$(req 18431 /login "${NAV[@]}")
expect "first visit" '^HTTP/1.1 302'
expect "first visit" '^Location: /index.php/apps/user_oidc/login/7\?silent=1$'
expect "first visit" '^Set-Cookie: nc_sso_tried=1; .*Max-Age=(299|300); .*secure; HttpOnly; SameSite=Lax'
OUT=$(req 18431 /index.php/login "${NAV[@]}")
expect "index.php path" '^Location: /index.php/apps/user_oidc/login/7\?silent=1$'
OUT=$(req 18431 '/login?redirect_url=/apps/files/?dir%3D/a' "${NAV[@]}")
expect "redirect_url kept" '^Location: /index.php/apps/user_oidc/login/7\?silent=1&redirectUrl=%2Fapps%2Ffiles%2F%3Fdir%3D%2Fa$'
OUT=$(req 18431 /login "${NAV[@]}" -b nc_sso_tried=1)
expect "tried once" 'NEXTCLOUD /login'
OUT=$(req 18431 '/login?direct=1' "${NAV[@]}")
expect "direct=1" 'NEXTCLOUD /login'
OUT=$(req 18431 /login)
expect "no browser navigation" 'NEXTCLOUD /login'
OUT=$(req 18431 /login "${NAV[@]}" -X POST)
expect "POST" 'NEXTCLOUD /login'
OUT=$(req 18431 '/index.php/apps/user_oidc/login/7?silent=1')
expect "silent attempt" '^Location: https://idp.example.org/auth\?response_type=code&client_id=nc&state=s&prompt=none$'
OUT=$(req 18431 /apps/user_oidc/login/7)
expect "login button" '^Location: https://idp.example.org/auth\?response_type=code&client_id=nc&state=s$'
OUT=$(req 18431 '/apps/user_oidc/code?error=login_required&state=s')
expect "no provider session" '^Location: /index.php/login$'
OUT=$(req 18431 '/index.php/apps/user_oidc/code?error=interaction_required&state=s')
expect "interaction required" '^Location: /index.php/login$'
OUT=$(req 18431 '/apps/user_oidc/code?error=access_denied&state=s')
expect "other provider error" 'NEXTCLOUD /apps/user_oidc/code'
OUT=$(req 18431 '/apps/user_oidc/code?code=c&state=s')
expect "code" 'NEXTCLOUD /apps/user_oidc/code'
OUT=$(req 18431 /remote.php/dav/files/u "${NAV[@]}")
expect "WebDAV" 'NEXTCLOUD /remote.php/dav/files/u'
OUT=$(req 18432 /login "${NAV[@]}")
expect "silentLogin false" 'NEXTCLOUD /login'
OUT=$(req 18432 '/apps/user_oidc/login/7?silent=1')
reject "silentLogin false, no prompt" 'prompt=none'
rm "$T/config/.chart-sso-provider"
OUT=$(req 18431 /login "${NAV[@]}")
expect "no provider id yet" 'NEXTCLOUD /login'
reject "no provider id yet" 'Set-Cookie'

[ $fail = 0 ] && echo "PASS nextcloud silent login"
exit $fail
