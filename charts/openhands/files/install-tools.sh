#!/bin/sh
# Installs the toolset that cannot be copied from the tools image: Debian packages,
# Python CLIs (their environments are tied to the tools image's Python) and npm CLIs.
# Runs as root at image build time. A command the base image already has is kept.
set -eu

# the build context comes from a ConfigMap; the check runs as another user
chmod 0755 "$(dirname "$0")"
chmod 0644 "$(dirname "$0")"/*

apt-get update
apt-get install -y --no-install-recommends \
  bash ca-certificates curl git jq make netcat-openbsd openssh-client \
  python3 python3-pip python3-venv python3-yaml tar gzip unzip bzip2 xz-utils zstd \
  awscli tmux ncurses-bin openssl bind9-dnsutils rsync tree less \
  postgresql-client mariadb-client openjdk-21-jre-headless skopeo poppler-utils
rm -rf /var/lib/apt/lists/*

[ -n "${TOOLS:-}" ] || TOOLS=$(dirname "$0")/tools.txt
[ -f "$TOOLS" ] || exit 0

export UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin \
  UV_PYTHON_INSTALL_DIR=/opt/uv-python UV_NO_CACHE=1 HOME=/tmp
uv=$(command -v uv)
# requirements such as huggingface_hub[cli] are not file globs
set -f

# tools.txt: "uv <command> <keep> <args...>", "npm <package@version> -", "pip <requirement> -"
while read -r kind cmd keep args; do
  case "$kind" in
    uv)
      # keep: the base image's own command stays, its agent starts it
      if [ "$keep" = true ] && command -v "$cmd" >/dev/null 2>&1; then echo "keep $cmd"; continue; fi
      # shellcheck disable=SC2086
      "$uv" tool install --force $args
      ;;
    npm)
      # shellcheck disable=SC2086
      npm install --global --no-fund --no-audit --omit=dev $cmd $args
      ;;
    pip)
      # constrained to the base image's packages, so its agent keeps its versions
      python3 -m pip freeze > /tmp/constraints.txt
      python3 -m pip install --no-cache-dir -c /tmp/constraints.txt "$cmd"
      rm -f /tmp/constraints.txt
      ;;
  esac
done < "$TOOLS"
npm cache clean --force
chmod -R a+rX /opt/uv-tools
[ -d /opt/uv-python ] && chmod -R a+rX /opt/uv-python
rm -rf /tmp/.cache /tmp/.npm
