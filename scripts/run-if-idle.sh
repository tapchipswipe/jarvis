#!/bin/bash
set -euo pipefail

# Idle-aware runner for consolidation.
# Only runs if:
#   - Current hour is in 1am-6am window (hours 1-5)
#   - System is idle for >= 30 minutes

check_idle() {
    if command -v pmset >/dev/null 2>&1; then
        idle_sec=$(pmset -g | awk '/^ +idle / {print $2}')
        [ -z "$idle_sec" ] && return 1
        [ "$idle_sec" -ge $((30 * 60)) ] && return 0
        return 1
    elif [ -f /proc/uptime ]; then
        idle=$(awk '{print $4}' /proc/uptime)
        # On Linux, idle is reported as fractional seconds since boot
        # Fallback: assume not idle enough unless we have a real idle monitor
        return 1
    fi
    return 1
}

hour=$(date +%H)
if [ "$hour" -lt 1 ] || [ "$hour" -ge 6 ]; then
    exit 0
fi

if ! check_idle; then
    exit 0
fi

exec "$@"
