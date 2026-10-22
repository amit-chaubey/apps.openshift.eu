#!/bin/sh
# Fails the image build when any tool in checks.txt does not run; called as a non-root UID.
set -eu
export HOME=/tmp KREW_ROOT=/tmp/.krew
id -un >/dev/null 2>&1 || echo "agent:x:$(id -u):0:agent:$HOME:/bin/sh" >> /etc/passwd
failed=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  name=${line%% *}
  if out=$(sh -c "$line" 2>&1); then
    printf '%-16s %s\n' "$name" "$(printf '%s' "$out" | grep -m1 -v '^\s*$' | cut -c1-100)"
  else
    printf '%-16s FAILED: %s\n' "$name" "$(printf '%s' "$out" | head -3 | tr '\n' ' ' | cut -c1-200)"
    failed="$failed $name"
  fi
done < "$(dirname "$0")/checks.txt"
grep -v "^agent:x:$(id -u):" /etc/passwd > /tmp/passwd && cat /tmp/passwd > /etc/passwd && rm -f /tmp/passwd
rm -rf /tmp/.krew /tmp/.cache /tmp/.config /tmp/.local
if [ -n "$failed" ]; then
  echo "tools that do not run:$failed" >&2
  exit 1
fi
