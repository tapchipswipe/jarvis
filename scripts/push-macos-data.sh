#!/bin/bash
# ============================================================
# push-macos-data.sh — Push macOS data to Lightspeed's inbox
# for nightly processing by the Jarvis sync daemon.
#
# This script is intended to be run via crontab (or launchd)
# on the Mac. It collects recent data from Mac sources and
# pushes them to the Lightspeed inbox directory over SCP.
#
# Install in crontab:
#   0 2 * * * /Users/lucasdespot/jarvis/scripts/push-macos-data.sh >> /Users/lucasdespot/jarvis/logs/push-macos-data.log 2>&1
#
# Or run manually:
#   bash scripts/push-macos-data.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JARVIS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$JARVIS_ROOT/logs"
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

# Config
LIGHTSPEED_USER="${LIGHTSPEED_USER:-despo}"
LIGHTSPEED_HOST="${LIGHTSPEED_HOST:-100.102.0.99}"
LIGHTSPEED_INBOX="${LIGHTSPEED_INBOX:-C:/data/jarvis/inbox}"
DEVICE_ID="macbook-pro"
SSH_OPTS="-o StrictHostKeyChecking=no -o PasswordAuthentication=no -o ConnectTimeout=5"

mkdir -p "$LOG_DIR"

echo "[$TIMESTAMP] === Jarvis Mac Data Push ==="

# 1. Check if Lightspeed is reachable
if ! ping -c 1 -W 3 "$LIGHTSPEED_HOST" >/dev/null 2>&1; then
    echo "[$TIMESTAMP] Lightspeed ($LIGHTSPEED_HOST) not reachable, skipping"
    exit 0
fi

# 2. Ensure remote inbox directory exists
ssh $SSH_OPTS "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}" \
    "powershell -Command \"New-Item -ItemType Directory -Force -Path '${LIGHTSPEED_INBOX}/${DEVICE_ID}'\" 2>/dev/null" || {
    echo "[$TIMESTAMP] WARNING: Could not create remote inbox directory"
}

# 3. Collect and push today's notes from Obsidian (if exists)
OBSIDIAN_DIR="$HOME/obsidian"
if [ -d "$OBSIDIAN_DIR" ]; then
    echo "[$TIMESTAMP] Collecting notes from Obsidian..."
    find "$OBSIDIAN_DIR" -name "*.md" -newer "$OBSIDIAN_DIR" -mtime -1 2>/dev/null | while read -r note; do
        basename=$(basename "$note")
        remote_path="${LIGHTSPEED_INBOX}/${DEVICE_ID}/${basename}"
        scp $SSH_OPTS "$note" "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${remote_path}" 2>/dev/null && \
            echo "[$TIMESTAMP]   Pushed: $basename"
    done
fi

# 4. Collect and push recent notes from ~/Documents
DOCUMENTS_DIR="$HOME/Documents"
if [ -d "$DOCUMENTS_DIR" ]; then
    echo "[$TIMESTAMP] Collecting recent documents..."
    find "$DOCUMENTS_DIR" \( -name "*.md" -o -name "*.txt" -o -name "*.csv" \) -newer "$DOCUMENTS_DIR" -mtime -1 2>/dev/null | while read -r doc; do
        basename=$(basename "$doc")
        remote_path="${LIGHTSPEED_INBOX}/${DEVICE_ID}/${basename}"
        scp $SSH_OPTS "$doc" "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${remote_path}" 2>/dev/null && \
            echo "[$TIMESTAMP]   Pushed: $basename"
    done
fi

# 5. Collect system info snapshot
SYSINFO_FILE=$(mktemp)
{
    echo "=== macOS System Snapshot ==="
    echo "Date: $(date)"
    echo "Hostname: $(hostname)"
    echo "OS: $(sw_vers -productName) $(sw_vers -productVersion) $(sw_vers -buildVersion)"
    echo "Kernel: $(uname -a)"
    echo ""
    echo "=== Memory ==="
    vm_stat | head -10
    echo ""
    echo "=== Disk ==="
    df -h / /Users 2>/dev/null
    echo ""
    echo "=== Top Processes ==="
    ps aux --sort=-%cpu | head -15
    echo ""
    echo "=== Active Network Interfaces ==="
    ifconfig | grep -E "^[a-z]|inet " | head -20
    echo ""
    echo "=== Recent Activity (last 2 hours) ==="
    log show --last 2h --predicate 'eventMessage contains "backup" OR eventMessage contains "sync"' 2>/dev/null | tail -20 || echo "log command not available"
} > "$SYSINFO_FILE"

sysinfo_name="system-snapshot-$(date +%Y%m%d-%H%M%S).txt"
scp $SSH_OPTS "$SYSINFO_FILE" "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${LIGHTSPEED_INBOX}/${DEVICE_ID}/${sysinfo_name}" 2>/dev/null && \
    echo "[$TIMESTAMP]   Pushed system snapshot: $sysinfo_name"
rm -f "$SYSINFO_FILE"

# 6. Push any pending items from the local inbox
LOCAL_INBOX="$JARVIS_ROOT/inbox"
if [ -d "$LOCAL_INBOX" ]; then
    find "$LOCAL_INBOX" -name "*.md" -o -name "*.txt" -o -name "*.json" 2>/dev/null | while read -r item; do
        basename=$(basename "$item")
        remote_path="${LIGHTSPEED_INBOX}/${DEVICE_ID}/${basename}"
        scp $SSH_OPTS "$item" "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${remote_path}" 2>/dev/null && \
            echo "[$TIMESTAMP]   Pushed inbox item: $basename" && \
            mv "$item" "$LOCAL_INBOX/processed/" 2>/dev/null || true
    done
fi

echo "[$TIMESTAMP] === Push Complete ==="