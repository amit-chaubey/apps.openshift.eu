#!/bin/sh
# Prepares the agent home and the core's state directory on the volume, then runs the core.
set -eu
umask 077
home=$CLOUDROOM_ACCOUNT_HOME
state=$CLOUDROOM_STATE_DIR
chart=/etc/cloudroom-chart

mkdir -p "$home" "$state"
chmod 0700 "$home" "$state"
# The core offers a harness once its home folder exists
[ "${HARNESS_CLAUDE:-}" != true ] || mkdir -p "$home/.claude"
[ "${HARNESS_OPENCODE:-}" != true ] || mkdir -p "$home/.local/share/opencode"
[ "${HARNESS_CODEX:-}" != true ] || mkdir -p "$CLOUDROOM_CODEX_HOME"

# Environment of every agent, owned by the chart
cp "$chart/environment" "$state/environment.new"
mv -f "$state/environment.new" "$state/environment"

# With the gateway, Claude Code sends a placeholder key that the token proxy replaces
if [ "${GATEWAY:-}" = true ] && [ "${HARNESS_CLAUDE:-}" = true ]; then
  printf 'token-proxy\n' > "$state/claude-api-key.new"
  mv -f "$state/claude-api-key.new" "$state/claude-api-key"
elif [ "$(cat "$state/claude-api-key" 2>/dev/null)" = token-proxy ]; then
  rm -f "$state/claude-api-key"
fi

exec /usr/local/lib/cloudroom/cloudroom
