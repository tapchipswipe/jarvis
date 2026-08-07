#!/bin/bash
# ============================================================
# verify-restore.sh — restore smoke test (idea 4, Round 9)
#
# Decrypts the NEWEST age-encrypted archive, extracts it, and reads the active
# memory count from the restored meta.db, comparing it to the live box's
# /api/health/deep count. Proves the encrypted restore path actually works, not
# just the forward backup. Run weekly (e.g. LaunchAgent or cron).
#
# Exit code: 0 = decrypt+extract+read OK (counts printed; small live>backup
#            drift is expected and only printed), 2/3 = no archive / restore fail.
# ============================================================
set -uo pipefail

KEY="${JARVIS_BACKUP_KEY:-$HOME/.config/jarvis/backup-key.age}"
BACKUP_DIR="${JARVIS_BACKUP_DIR:-$HOME/jarvis/backups}"
BOX="${JARVIS_BOX_URL:-https://100.102.0.99:8766}"
TS=$(date "+%Y-%m-%d %H:%M:%S")

ARCH=$(ls -1t "$BACKUP_DIR"/store-*.tar.gz.age 2>/dev/null | head -1)
[ -z "$ARCH" ] && { echo "[$TS] no archive under $BACKUP_DIR"; exit 2; }
echo "[$TS] archive: $(basename "$ARCH")"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
if ! age -d -i "$KEY" < "$ARCH" | tar -xz -C "$WORK"; then
    echo "[$TS] FAIL — decrypt/extract failed"; exit 3
fi
DB=$(find "$WORK" -name meta.db 2>/dev/null | head -1)
[ -z "$DB" ] && { echo "[$TS] FAIL — no meta.db in archive"; exit 3; }
RESTORED=$(python3 -c "import sqlite3;print(sqlite3.connect('$DB').execute('SELECT COUNT(*) FROM memories WHERE superseded=0').fetchone()[0])") || { echo "[$TS] FAIL — cannot read meta.db"; exit 3; }

LIVE=$(curl -sk -m 10 "$BOX/api/health/deep" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("memories"))' 2>/dev/null) || LIVE="n/a"
echo "[$TS] restored=$RESTORED live=$LIVE"
if [ "$RESTORED" = "$LIVE" ]; then
    echo "[$TS] VERIFY OK — counts match"
else
    echo "[$TS] WARN — counts differ (expected if box ingested since this snapshot)"
fi
echo "[$TS] restore path verified"
