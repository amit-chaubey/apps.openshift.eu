#!/bin/sh
# Keeps one copy of the session store as it was before the first OpenCode 2.x start.
# OpenCode 2.x migrates opencode.db in place, and 1.x cannot read it afterwards.
# Runs under the work tree lock, so no other OpenCode writes the file meanwhile.
set -eu

DATA=${XDG_DATA_HOME:-$HOME/.local/share}/opencode
BACKUP=${OPENCODE_DB_BACKUP_DIR:-$HOME/backup}

if [ ! -f "$DATA/opencode.db" ]; then
	exit 0
fi
if ls "$BACKUP"/opencode.db.* >/dev/null 2>&1; then
	exit 0
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$BACKUP"
# the WAL holds committed pages that are not in the main file yet
for suffix in "" -wal -shm; do
	if [ -f "$DATA/opencode.db$suffix" ]; then
		cp -p "$DATA/opencode.db$suffix" "$BACKUP/opencode.db.$stamp$suffix.partial"
	fi
done
for suffix in -shm -wal ""; do
	if [ -f "$BACKUP/opencode.db.$stamp$suffix.partial" ]; then
		mv "$BACKUP/opencode.db.$stamp$suffix.partial" "$BACKUP/opencode.db.$stamp$suffix"
	fi
done
echo "session store copied to $BACKUP/opencode.db.$stamp"
