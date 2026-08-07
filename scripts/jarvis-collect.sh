#!/bin/bash
# ============================================================
# jarvis-collect.sh — thin-client ambient collection (files -> outbox -> server).
#
# Runs on the Mac (thin client, FULL-THIN). The server (Lightspeed) is the single
# writer; this Mac job only *queues* new file text into the disposable outbox and
# then flushes the backlog to the server via `/api/remember`. It never opens a
# local Store/Chroma handle.
#
# Safe by design:
#   * Bounded walk (--max-files), skips unchanged files by mtime/size fingerprint.
#   * Idempotent: equal content is not re-enqueued (outbox key) and the server
#     store.add() dedupes on content-hash, so re-runs never create duplicates.
#   * Offline-safe: if the server is unreachable, items stay in the outbox and
#     are flushed on the next successful run.
#
# Register (LaunchAgent, every 30 min):
#   cp scripts/com.user.jarvis-collect.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.user.jarvis-collect.plist
# Logs: ~/jarvis/logs/collect.log
# ============================================================
set -uo pipefail

BOX_HOST="100.102.0.99"
PORT=8766
JARVIS_ROOT="${JARVIS_ROOT:-$HOME/jarvis}"
LOG_DIR="$JARVIS_ROOT/logs"
VENV_PY="${JARVIS_VENV_PY:-$JARVIS_ROOT/.venv/bin/python}"
MAX_FILES="${JARVIS_COLLECT_MAX_FILES:-1500}"
mkdir -p "$LOG_DIR"
TS=$(date "+%Y-%m-%d %H:%M:%S")

export JARVIS_MODE=client
export JARVIS_REMOTE="http://$BOX_HOST:$PORT"
# LaunchAgent context doesn't source ~/.zshrc, so read the shared token file:
export JARVIS_TOKEN="$(cat "$HOME/.config/jarvis/token" 2>/dev/null)"

if [ ! -x "$VENV_PY" ]; then
    echo "[$TS] no venv at $VENV_PY" >> "$LOG_DIR/collect.log"
    exit 1
fi

cd "$JARVIS_ROOT" || exit 1
"$VENV_PY" -m jarvis.cli collect --max-files "$MAX_FILES" --flush >> "$LOG_DIR/collect.log" 2>&1
