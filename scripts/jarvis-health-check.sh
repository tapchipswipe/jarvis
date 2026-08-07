#!/bin/bash
# ============================================================
# jarvis-health-check.sh — Box health / outbox-backlog / disk notifier.
#
# Runs on the Mac (thin client). Every run it:
#   * probes the box `jarvis server` /api/health (+ /api/health/deep)
#   * checks the local thin-client outbox backlog for items never flushed
#   * reports box disk free
# If the box is unreachable or the outbox is backing up, it appends an
# alert line to ~/jarvis/logs/health-alerts.log, drops a marker file so a
# dashboard/grep can find it, AND — the new bit — actually reaches you:
#   * a macOS Notification Center banner (when the Mac is awake)
#   * an optional webhook (ntfy/Telegram/Discord/…) when JARVIS_ALERT_WEBHOOK
#     is set
# Notifications are rate-limited (default one per alert-type per 30 min) so a
# long outage pages you once, not every 20 minutes.
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
WEBHOOK="${JARVIS_ALERT_WEBHOOK:-}"
ALERT_MIN_INTERVAL="${JARVIS_ALERT_MIN_INTERVAL:-1800}"  # 30 min
TS=$(date "+%Y-%m-%d %H:%M:%S")
mkdir -p "$LOG_DIR"

notice() { echo "[$TS] $*"; }
alert() {
    echo "[$TS] $*" | tee -a "$ALERT_LOG"
}

# Alert that actually reaches you: log + desktop banner + optional webhook.
# Rate-limited per alert-subject so a persistent condition pages you ONCE.
notify_alert() {
    local subject="$1"
    local body="$2"
    alert "$subject: $body"
    local marker="$LOG_DIR/.alert-$(printf '%s' "$subject" | tr -c 'a-zA-Z0-9' '_')"
    local now last=0
    now=$(date +%s)
    [ -f "$marker" ] && last=$(cat "$marker" 2>/dev/null || echo 0)
    if [ $(( now - last )) -lt "$ALERT_MIN_INTERVAL" ]; then
        return  # still within the quiet window
    fi
    echo "$now" > "$marker"
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display notification \"$body\" with title \"Jarvis Alert: $subject\"" >/dev/null 2>&1 || true
    fi
    if [ -n "$WEBHOOK" ]; then
        curl -m 10 -fsS -X POST "$WEBHOOK" \
            -H "Content-Type: text/plain" \
            --data-binary "[Jarvis Alert: $subject] $body" >/dev/null 2>&1 || true
    fi
}

# 1. Box health
if HEALTH=$(curl -m 8 -fsS "http://$BOX_HOST:$PORT/api/health" 2>/dev/null); then
    notice "box health: $(printf '%s' "$HEALTH" | head -c 80)"
    DEEP=$(curl -m 15 -fsS "http://$BOX_HOST:$PORT/api/health/deep" 2>/dev/null || true)
    notice "box deep: $(printf '%s' "$DEEP" | head -c 100)"
else
    notify_alert "BOX UNREACHABLE" "no response on $BOX_HOST:$PORT/api/health"
fi

# 2. Outbox backlog (thin-client write buffer not yet flushed to box)
OUTBOX=""
if [ -x "$VENV_PY" ]; then
    OUTBOX=$(cd "$JARVIS_ROOT" && JARVIS_MODE=client JARVIS_REMOTE="http://$BOX_HOST:$PORT" \
        "$VENV_PY" -c 'from jarvis.cache import Cache; print(Cache().pending_count())' 2>/dev/null)
fi
if [ -n "$OUTBOX" ] && [ "$OUTBOX" != "0" ]; then
    notify_alert "OUTBOX BACKLOG" "$OUTBOX pending item(s) not yet flushed to box"
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

# 4. Inbox backlog ingester progress (present once the box server runs 966d7b2+)
INGEST=$(curl -m 8 -fsS "http://$BOX_HOST:$PORT/api/ingest/status" 2>/dev/null || true)
if [ -n "$INGEST" ]; then
    notice "box ingest: $(printf '%s' "$INGEST" | head -c 200)"
else
    notice "box ingest: n/a (endpoint not yet on the running pre-restart server)"
fi

