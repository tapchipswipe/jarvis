#!/bin/bash
set -euo pipefail
MODELS=("qwen2.5:7b-instruct-q4_K_M")
BACKUP_NAS_HOST="${BACKUP_NAS_HOST:-truenas}"
BACKUP_NAS_PATH="${BACKUP_NAS_PATH:-/mnt/backups/second-brain}"
BACKUP_PASSPHRASE="${BACKUP_PASSPHRASE:-}"

backup() {
    echo "[$(date)] Starting backup..."
    TMPDIR=$(mktemp -d)
    ARCHIVE="$TMPDIR/second-brain-backup.tar.gz"
    ENCRYPTED="$TMPDIR/second-brain-backup.tar.gz.age"

    tar -czf "$ARCHIVE" -C /data second-brain --exclude='chroma' --exclude='*.log'
    if command -v age >/dev/null 2>&1 && [ -n "$BACKUP_PASSPHRASE" ]; then
        age -p -o "$ENCRYPTED" "$ARCHIVE" <<< "$BACKUP_PASSPHRASE"
    elif command -v gpg >/dev/null 2>&1 && [ -n "$BACKUP_PASSPHRASE" ]; then
        echo "$BACKUP_PASSPHRASE" | gpg --batch --yes --passphrase-fd 0 -c -o "$ENCRYPTED" "$ARCHIVE"
    else
        echo "WARNING: No encryption tool or passphrase set, backing up unencrypted"
        cp "$ARCHIVE" "$ENCRYPTED"
    fi

    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$BACKUP_NAS_HOST" "mkdir -p $BACKUP_NAS_PATH"
    scp -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$ENCRYPTED" "$BACKUP_NAS_HOST:$BACKUP_NAS_PATH/backup-$(date +%Y-%m-%d).tar.gz.enc"
    rm -rf "$TMPDIR"
    echo "[$(date)] Backup complete"
}

model_update() {
    echo "[$(date)] Checking model updates..."
    for model in "${MODELS[@]}"; do
        echo "Checking $model..."
        ollama pull "$model" || echo "Failed to pull $model"
    done
    echo "[$(date)] Model update check complete"
}

case "${1:-}" in
    backup)
        backup
        ;;
    model-update)
        model_update
        ;;
    *)
        echo "Usage: $0 {backup|model-update}"
        exit 1
        ;;
esac
