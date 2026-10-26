#!/bin/sh
# Copies the image's home into the persistent home, entry by entry, never overwriting.
# .openhands and workspace are volume subdirectories of their own, mounted on top.
set -eu
src=${SRC:-/home/openhands}
dst=${DST:-/mnt/home}
cd "$src"
find . -mindepth 1 \( -path ./.openhands -o -path ./workspace \) -prune -o -print |
while IFS= read -r p; do
  t="$dst/${p#./}"
  if [ -e "$t" ] || [ -L "$t" ]; then
    continue
  fi
  if [ -d "$p" ] && [ ! -L "$p" ]; then
    mkdir "$t"
  elif ! cp -P --preserve=mode,timestamps "$p" "$t" 2>/dev/null; then
    # build-time caches the arbitrary UID cannot read
    echo "skipped ${p#./}"
    continue
  fi
  # arbitrary UID: group 0 needs the owner's rights
  [ -L "$t" ] || chmod g=u "$t"
  echo "seeded ${p#./}"
done
