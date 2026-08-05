#!/bin/bash
# Daily memory maintenance for Jarvis.
#
#  1. Promote raw memories older than 7 days to the session tier (decay).
#  2. Re-index any memories missing from the vector store (incremental).
#
# Portable across the macOS local install (~/jarvis) and Lightspeed (/data/jarvis).
# Logs to <root>/logs/maintenance.log.
set -euo pipefail

# ---- Resolve the project root (macOS local vs Lightspeed) ------------------
if [ -d /Users/lucasdespot/jarvis ]; then
    JARVIS_ROOT="/Users/lucasdespot/jarvis"
elif [ -d /data/jarvis ]; then
    JARVIS_ROOT="/data/jarvis"
else
    JARVIS_ROOT="${HOME}/jarvis"
fi

# ---- Find a python interpreter ---------------------------------------------
PY="${JARVIS_ROOT}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
    PY="$(command -v python3 || true)"
fi
if [ -z "${PY}" ]; then
    echo "No python interpreter found; skipping Jarvis maintenance." \
        >> "${JARVIS_ROOT}/logs/maintenance.log" 2>&1
    exit 1
fi

mkdir -p "${JARVIS_ROOT}/logs"
LOG="${JARVIS_ROOT}/logs/maintenance.log"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Jarvis maintenance ===" >> "${LOG}"
echo "--- promote (raw -> session, 7d) ---" >> "${LOG}"
"${PY}" -m jarvis.cli promote --days 7 >> "${LOG}" 2>&1 || echo "promote failed" >> "${LOG}"
echo "--- reindex (missing vectors) ---" >> "${LOG}"
"${PY}" -m jarvis.cli reindex --limit 200 >> "${LOG}" 2>&1 || echo "reindex failed" >> "${LOG}"
echo "--- done ---" >> "${LOG}"

exit 0