#!/bin/bash
# ============================================================
# jarvis-health-check.sh — Box health / outbox-backlog / disk notifier.
#
# Runs on the Mac (thin client). Every run it:
#   * probes the box `jarvis server` /api/health (+ /api/health/deep)
#   * checks the local thin-client outbox backlog for items never flushed
#   * reports box disk free
# If the box is unreachable or the outbox is backing up, it appends an
# alert line to ~/jarvis/logs/health-alerts.log (a persistent record I can
# surface) and drops a marker file so a dashboard/grep can find it. It does
# NOT page anyone — it is the "early-warning" record layer.
#
# Register (Mac crontab):
#   */20 * * * * /bin/bash /Users/lucasdespot/jarvis/scripts/jarvis-health-check.sh >> /Users/lucasdespot/jarvis/logs/health-check.log 2>&1
# ============================================================
set -uo pipefail

BOX_HOST="100.102.0.99"
PORT=8766
JARVIS_ROOT="${JARVIS_ROOT:-$HOME/jarvis}"
LOG_DIR="$JARVIS_ROOT/logs"
ALERT_LOG="$LOG_DIR/health-alerts.log"
VENV_PY="${JARVIS_VENV_PY:-$JARVIS_ROOT/.venv/bin/python}"
TS=$(date "+%Y-%m-%d %H:%M:%S")
mkdir -p "$LOG_DIR"

notice() { echo "[$TS] $*"; }
alert() {
    echo "[$TS] $*" | tee -a "$ALERT_LOG"
}

# 1. Box health
if HEALTH=$(curl -m 8 -fsS "http://$BOX_HOST:$PORT/api/health" 2>/dev/null); then
    notice "box health: $(printf '%s' "$HEALTH" | head -c 80)"
    DEEP=$(curl -m 15 -fsS "http://$BOX_HOST:$PORT/api/health/deep" 2>/dev/null || true)
    notice "box deep: $(printf '%s' "$DEEP" | head -c 100)"
else
    alert "BOX UNREACHABLE — no response on $BOX_HOST:$PORT/api/health"
fi

# 2. Outbox backlog (thin-client write buffer not yet flushed to box)
OUTBOX=""
if [ -x "$VENV_PY" ]; then
    OUTBOX=$(cd "$JARVIS_ROOT" && JARVIS_MODE=client JARVIS_REMOTE="http://$BOX_HOST:$PORT" \
        "$VENV_PY" -c 'from jarvis.cache import Cache; print(Cache().pending_count())' 2>/dev/null)
fi
if [ -n "$OUTBOX" ] && [ "$OUTBOX" != "0" ]; then
    alert "OUTBOX BACKLOG = $OUTBOX pending item(s) not yet flushed to box"
else
    notice "outbox backlog: ${OUTBOX:-n/a}"
fi

# 3. Box disk free
DISK=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "despo@$BOX_HOST" \
   'Get-PSDrive C | Select-Object -ExpandProperty Free 2>$null' 2>/dev/null | grep -o '[0-9]*' | head -1)
if [ -n "$DISK" ]; then
    GB=$(( DISK / 1024 / 1024 / 1024 ))
    notice "box C: free ~${GB} GB"
else
    notice "box disk: n/a"
fi
