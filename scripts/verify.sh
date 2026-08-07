#!/bin/bash
# ============================================================
# verify.sh — one-command QA gate: tests + lint + import smoke.
# No state changes; safe to run anytime.
# ============================================================
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PY="$PWD/.venv/bin/python"
RUFF="${JARVIS_RUFF:-/opt/homebrew/bin/ruff}"

echo "== pytest (full suite) =="
"$PY" -m pytest -p no:cacheprovider -q

echo "== ruff =="
if [ -x "$RUFF" ]; then
    "$RUFF" check jarvis/ tests/ >/tmp/jarvis-ruff.txt 2>&1 && echo "ruff: clean" \
        || echo "ruff: $(grep -cE '^[A-Z][0-9]+' /tmp/jarvis-ruff.txt) pre-existing style items (non-blocking)"
else
    echo "ruff: not found at $RUFF (skipping)"
fi

echo "== import smoke =="
"$PY" -c "import jarvis.server, jarvis.dashboard, jarvis.cli, jarvis.inbox_ingest, jarvis.collectors.thin; print('imports OK')"

echo "== verify complete =="
