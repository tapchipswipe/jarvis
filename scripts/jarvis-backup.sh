#!/bin/bash
# ============================================================
# jarvis-backup.sh — rolling backup of the Lightspeed box store.
#
# Runs on the Mac (the box is the single source of truth after the
# Round 7 thin-client cutover). It does a WARM copy of the box store
# (meta.db + chroma) into a rolling backup dir, kept on the SAME box
# so it never interferes with the running `jarvis server`, plus a
# daily network copy down to the Mac for off-box redundancy (3-2-1).
#
# NOTE: a warm file copy of Chroma while the server is live is a
# "may be slightly internally inconsistent at a moment in time" copy —
# fine for rollback/recovery, not a point-in-time backup. For a strict
# consistent snapshot, stop `jarvis server` first.
#
# Register (Mac crontab):
#   0 4 * * * /bin/bash /Users/lucasdespot/jarvis/scripts/jarvis-backup.sh >> /Users/lucasdespot/jarvis/logs/jarvis-backup.log 2>&1
# ============================================================
set -uo pipefail

BOX_USER="despo"
BOX_HOST="100.102.0.99"
BOX_SRC="C:/Users/despo/jarvis/data"
BOX_ROLLBACK_ROOT="C:/data/jarvis-rollback"
MAC_DST="${JARVIS_BACKUP_DIR:-$HOME/jarvis/backups}"
KEEP_BOX=14      # number of on-box rolling snapshots to keep
KEEP_MAC=7       # number of off-box daily snapshots to keep
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10"
TS=$(date +%Y%m%d-%H%M%S)

mkdir -p "$MAC_DST"
echo "[$TS] === Jarvis backup ==="

# 1. Health gate — do not take a backup snapshot while the brain is unhealthy.
if ! curl -m 8 -fsS http://"$BOX_HOST":8766/api/health >/dev/null 2>&1; then
    echo "[$TS] ABORT: box health check failed — skipping backup."
    exit 2
fi

# 2. Warm snapshot on the box (rolling).
BOX_TS_DIR="$BOX_ROLLBACK_ROOT/$TS"
ssh $SSH_OPTS "$BOX_USER@$BOX_HOST" \
  "Copy-Item -Recurse -Force '$BOX_SRC' '$BOX_TS_DIR'" || {
    echo "[$TS] ERROR: on-box copy failed."; exit 3; }

# Prune old on-box snapshots (keep newest KEEP_BOX by name).
ssh $SSH_OPTS "$BOX_USER@$BOX_HOST" \
  "Get-ChildItem '$BOX_ROLLBACK_ROOT' -Directory | Sort-Object Name -Descending | Select-Object -Skip $KEEP_BOX | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue" 2>/dev/null
echo "[$TS] On-box warm snapshot -> $BOX_TS_DIR"

# 3. Network copy of the day's snapshot down to the Mac (keep KEEP_MAC).
DAY_TAG=$(date +%Y%m%d)
scp $SSH_OPTS -r -q "$BOX_USER@$BOX_HOST:$BOX_TS_DIR" "$MAC_DST/store-$DAY_TAG" 2>/dev/null || \
  scp $SSH_OPTS -r "$BOX_USER@$BOX_HOST:$BOX_TS_DIR" "$MAC_DST/store-$DAY_TAG"
# prune old Mac snapshots
ls -1dt "$MAC_DST"/store-* 2>/dev/null | tail -n +$((KEEP_MAC+1)) | xargs rm -rf 2>/dev/null
echo "[$TS] Off-box copy -> $MAC_DST/store-$DAY_TAG"
echo "[$TS] === Backup complete ==="

