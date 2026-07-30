#!/bin/bash
set -euo pipefail
BACKUP_NAS_HOST="${BACKUP_NAS_HOST:-truenas}"
BACKUP_NAS_PATH="${BACKUP_NAS_PATH:-/mnt/backups/jarvis}"

# Ensure NAS is reachable
if ! ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$BACKUP_NAS_HOST" "echo ok" >/dev/null 2>&1; then
    echo "NAS unreachable, skipping backup"
    exit 0
fi

/Users/lucasdespot/jarvis/scripts/backup.sh backup
