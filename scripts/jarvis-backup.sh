#!/bin/bash
# ============================================================
# jarvis-backup.sh — rolling backup of the Lightspeed box store.
#
# Runs on the Mac (the box is the single source of truth after the
# Round 7 thin-client cutover). It does a CRASH-CONSISTENT snapshot of the box
# store into a rolling backup dir, kept on the SAME box so it never interferes
# with the running `jarvis server`, plus a daily network copy down to the Mac
# for off-box redundancy (3-2-1).
#
# Consistency: `jarvis backup` snapshots every SQLite file (meta.db,
# embed_cache.db, chroma.sqlite3) via the SQLite *online-backup* API — valid
# even while the server is writing — and file-copies Chroma's HNSW index
# binaries (moment-in-time). For a FULLY consistent HNSW index too, set
# JARVIS_BACKUP_STRICT=1: the script briefly pauses the JarvisServer scheduled
# task, snapshots, then always restarts it (maintenance-window friendly).
#
# For a hardened 3-2-1, an age-encrypted archive is produced (below) when `age`
# and a recipient public key are present. Generate once:
#   age-keygen -o ~/.config/jarvis/backup-key.age     # keep this PRIVATE
#   # the sibling .pub file is the recipient used for encryption.
# Alternative: push the plain snapshot to TrueNAS/extra disk for a 3rd copy.
#
# Register (Mac crontab):
#   0 4 * * * /bin/bash /Users/lucasdespot/jarvis/scripts/jarvis-backup.sh >> /Users/lucasdespot/jarvis/logs/jarvis-backup.log 2>&1
# ============================================================
set -uo pipefail

BOX_USER="despo"
BOX_HOST="100.102.0.99"
BOX_ROLLBACK_ROOT="C:/data/jarvis-rollback"
MAC_DST="${JARVIS_BACKUP_DIR:-$HOME/jarvis/backups}"
KEEP_BOX=14      # number of on-box rolling snapshots to keep
KEEP_MAC=7       # number of off-box daily snapshots to keep
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10"
TS=$(date +%Y%m%d-%H%M%S)
STRICT="${JARVIS_BACKUP_STRICT:-0}"

mkdir -p "$MAC_DST"
echo "[$TS] === Jarvis backup === (strict=$STRICT)"

# 1. Health gate — do not take a backup snapshot while the brain is unhealthy.
if ! curl -m 8 -fsS -k https://"$BOX_HOST":8766/api/health >/dev/null 2>&1; then
    echo "[$TS] ABORT: box health check failed — skipping backup."
    exit 2
fi

# 2. (strict) pause the server so Chroma's HNSW index is captured consistently.
if [ "$STRICT" = "1" ]; then
    echo "[$TS] Strict: pausing JarvisServer for a consistent HNSW snapshot..."
    ssh $SSH_OPTS "$BOX_USER@$BOX_HOST" "Stop-ScheduledTask -TaskName JarvisServer" 2>/dev/null \
      || echo "[$TS] WARN: Stop-ScheduledTask failed (task may already be stopped)"
    sleep 3
fi

# 3. Crash-consistent snapshot via the running server's IN-PROCESS endpoint
#    (avoids invoking a second Python on the Windows Store-Python box, which
#    refuses non-interactive execution). Requires the shared token.
if [ -z "${JARVIS_TOKEN:-}" ] && [ -f "$HOME/.config/jarvis/token" ]; then
    JARVIS_TOKEN=$(cat "$HOME/.config/jarvis/token")
fi
BOX_TS_DIR="$BOX_ROLLBACK_ROOT/$TS"
if ! curl -m 180 -fsS -k -X POST "https://$BOX_HOST:8766/api/admin/backup" \
      -H "Content-Type: application/json" \
      ${JARVIS_TOKEN:+-H "X-Jarvis-Token: $JARVIS_TOKEN"} \
      -d "{\"dst\": \"$BOX_TS_DIR\"}" -o "$MAC_DST/backup-report.json"; then
    echo "[$TS] ERROR: on-box snapshot failed (no report)."
    if [ "$STRICT" = "1" ]; then
        echo "[$TS] Restarting JarvisServer after failed snapshot..."
        ssh $SSH_OPTS "$BOX_USER@$BOX_HOST" "Start-ScheduledTask -TaskName JarvisServer" 2>/dev/null
    fi
    exit 3
fi
echo "[$TS] snapshot report: $(head -c 300 "$MAC_DST/backup-report.json")"

# 4. (strict) restart the server and confirm health — ALWAYS, even on error.
if [ "$STRICT" = "1" ]; then
    echo "[$TS] Restarting JarvisServer..."
    ssh $SSH_OPTS "$BOX_USER@$BOX_HOST" "Start-ScheduledTask -TaskName JarvisServer" 2>/dev/null
    for _ in 1 2 3 4 5 6; do
        sleep 6
        if curl -m 8 -fsS -k https://"$BOX_HOST":8766/api/health >/dev/null 2>&1; then
            echo "[$TS] Server healthy again."; break
        fi
    done
    curl -m 8 -fsS -k https://"$BOX_HOST":8766/api/health >/dev/null 2>&1 || \
        echo "[$TS] WARN: server not healthy after restart — check Task Scheduler."
fi

# Prune old on-box snapshots (keep newest KEEP_BOX by name).
ssh $SSH_OPTS "$BOX_USER@$BOX_HOST" \
  "Get-ChildItem '$BOX_ROLLBACK_ROOT' -Directory | Sort-Object Name -Descending | Select-Object -Skip $KEEP_BOX | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue" 2>/dev/null
echo "[$TS] On-box snapshot -> $BOX_TS_DIR"

# 3. Network copy of the day's snapshot down to the Mac (keep KEEP_MAC).
DAY_TAG=$(date +%Y%m%d)
scp $SSH_OPTS -r -q "$BOX_USER@$BOX_HOST:$BOX_TS_DIR" "$MAC_DST/store-$DAY_TAG" 2>/dev/null || \
  scp $SSH_OPTS -r "$BOX_USER@$BOX_HOST:$BOX_TS_DIR" "$MAC_DST/store-$DAY_TAG"
# prune old Mac snapshots
ls -1dt "$MAC_DST"/store-* 2>/dev/null | tail -n +$((KEEP_MAC+1)) | xargs rm -rf 2>/dev/null
echo "[$TS] Off-box copy -> $MAC_DST/store-$DAY_TAG"

# 4. 3-2-1 hardening (OPT-IN): wrap today's snapshot in an age-encrypted archive
#    for off-site storage when `age` + a recipient pubkey are available.
AGE_PUB="${JARVIS_AGE_PUBKEY:-$HOME/.config/jarvis/backup-key.age.pub}"
if command -v age >/dev/null 2>&1 && [ -n "$AGE_PUB" ] && [ -f "$AGE_PUB" ] && \
   [ -d "$MAC_DST/store-$DAY_TAG" ]; then
    AGE_ARCHIVE="$MAC_DST/store-$DAY_TAG.tar.gz.age"
    if tar -C "$MAC_DST" -czf - "store-$DAY_TAG" | age -R "$AGE_PUB" -o "$AGE_ARCHIVE" && [ -s "$AGE_ARCHIVE" ]; then
        echo "[$TS] age-encrypted archive -> $AGE_ARCHIVE"
    else
        echo "[$TS] WARNING: age archive empty/failed — plain snapshot kept."
    fi
fi

echo "[$TS] === Backup complete ==="

