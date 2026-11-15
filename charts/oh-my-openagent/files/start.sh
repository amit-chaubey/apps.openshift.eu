#!/bin/bash
# Start of the terminal container: install the pinned OmO into HOME once, write its
# configuration, then serve one tmux session in the browser terminal.
# Environment: OMO_PACKAGE, OMO_VERSION, OMO_REGISTRY, OMO_WORKDIR, TERMINAL_PORT,
# TERMINAL_FONT_SIZE, GIT_USER_NAME, GIT_USER_EMAIL; configuration files in /etc/omo.
set -euo pipefail

# ssh refuses to start for a UID without a passwd entry; the image leaves passwd group-writable
if ! id -un >/dev/null 2>&1 && [ -w /etc/passwd ]; then
  echo "omo:x:$(id -u):0:OmO:$HOME:/bin/bash" >> /etc/passwd
fi

lib="$HOME/.local/lib/omo"
prefix="$lib/$OMO_VERSION"
mkdir -p "$lib" "$HOME/.local/bin" "$HOME/.omo/agent" "$HOME/$OMO_WORKDIR"

# A version installs into its own directory; a failed install leaves no directory behind
if [ ! -x "$prefix/node_modules/.bin/omo" ]; then
  echo "installing $OMO_PACKAGE@$OMO_VERSION from $OMO_REGISTRY"
  rm -rf "$prefix.partial"
  npm install --prefix "$prefix.partial" --registry "$OMO_REGISTRY" --no-audit --no-fund \
    --no-save --loglevel=warn "$OMO_PACKAGE@$OMO_VERSION"
  rm -rf "$prefix"
  mv "$prefix.partial" "$prefix"
fi
# Other versions are removed once the pinned one is in place
for d in "$lib"/*; do
  [ "$d" = "$prefix" ] || rm -rf "$d"
done
ln -sfn "$prefix/node_modules/.bin/omo" "$HOME/.local/bin/omo"

# The chart owns the provider list and OmO's configuration; settings stay the user's
install -m 0600 /etc/omo/models.json "$HOME/.omo/agent/models.json"
install -m 0600 /etc/omo/omo.jsonc "$HOME/.omo/omo.jsonc"
[ -e "$HOME/.omo/agent/settings.json" ] || install -m 0600 /etc/omo/settings.json "$HOME/.omo/agent/settings.json"

# OmO creates its MCP identity key with mode 0600 and refuses a key with other modes. The
# kubelet's fsGroup ownership change adds group rw to every file of the volume on each mount,
# so the mode is set back here, after the mount and before OmO starts.
key="$HOME/.omo/agent/config-identity.key"
[ ! -f "$key" ] || [ -L "$key" ] || chmod 0600 "$key"

[ -z "${GIT_USER_NAME:-}" ] || git config --global user.name "$GIT_USER_NAME"
[ -z "${GIT_USER_EMAIL:-}" ] || git config --global user.email "$GIT_USER_EMAIL"

# Login shells in the terminal find omo
cat > "$HOME/.omo-path.sh" <<EOF
export PATH="$HOME/.local/bin:$prefix/node_modules/.bin:\$PATH"
EOF
grep -qs 'omo-path.sh' "$HOME/.bashrc" || echo '. "$HOME/.omo-path.sh"' >> "$HOME/.bashrc"
grep -qs 'bashrc' "$HOME/.bash_profile" || echo '[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"' >> "$HOME/.bash_profile"
export PATH="$HOME/.local/bin:$prefix/node_modules/.bin:$PATH"

# Every browser tab attaches to the same tmux session; closing a tab stops nothing
exec ttyd --interface lo --port "$TERMINAL_PORT" --writable \
  --client-option fontSize="$TERMINAL_FONT_SIZE" --client-option titleFixed=OmO \
  tmux new-session -A -s omo /bin/bash /etc/omo/session.sh
