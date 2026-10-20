#!/bin/sh
# Prepares HOME and the work tree, then returns; never overwrites a clone
set -eu

# ssh refuses to start for a UID without a passwd entry; the image leaves passwd group-writable
if ! id -un >/dev/null 2>&1 && [ -w /etc/passwd ]; then
	echo "agent:x:$(id -u):0:agent:${HOME:-/workspace}:/bin/sh" >> /etc/passwd
fi

mkdir -p "$HOME" "${XDG_CONFIG_HOME:-$HOME/.config}" "${XDG_DATA_HOME:-$HOME/.local/share}" \
	"${XDG_STATE_HOME:-$HOME/.local/state}" "${XDG_CACHE_HOME:-$HOME/.cache}"

# Rewritten on every start so a new name or email applies; no token on disk
cat > "$HOME/.gitconfig" <<EOF
[user]
	name = ${GIT_USER_NAME:-}
	email = ${GIT_USER_EMAIL:-}
[init]
	defaultBranch = main
EOF

# The token goes only to the host of GIT_URL, never to another HTTPS host
GIT_HOST_URL=$(printf '%s' "${GIT_URL:-}" | sed -nE 's#^(https://)([^/@]*@)?([^/]+).*#\1\3#p')
if [ -n "$GIT_HOST_URL" ]; then
	cat >> "$HOME/.gitconfig" <<EOF
[credential "$GIT_HOST_URL"]
	helper = "!f() { echo username=oauth2; printf 'password='; cat \"${GIT_TOKEN_FILE:-/dev/null}\"; echo; }; f"
EOF
fi

# Registry per npm scope (opencode.npmScopes); rewritten like .gitconfig
if [ -f "${NPMRC_SOURCE:-/etc/opencode-agent/npmrc}" ]; then
	cp "${NPMRC_SOURCE:-/etc/opencode-agent/npmrc}" "$HOME/.npmrc"
fi

if [ -z "${GIT_URL:-}" ]; then
	exit 0
fi

if [ -d "$REPO_DIR/.git" ]; then
	echo "work tree $REPO_DIR exists, not cloning"
	exit 0
fi

if [ -e "$REPO_DIR" ]; then
	echo "$REPO_DIR exists but is not a git work tree, leaving it alone" >&2
	exit 0
fi

rm -rf "$REPO_DIR.partial"
git clone --branch "${GIT_BRANCH:-main}" "$GIT_URL" "$REPO_DIR.partial"
mv "$REPO_DIR.partial" "$REPO_DIR"
