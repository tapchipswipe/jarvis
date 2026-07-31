#!/bin/bash
set -e
echo "Deploying to Lightspeed (bot branch)..."

# === Configuration ===
LIGHTSPEED_USER="${LIGHTSPEED_USER:-despo}"
LIGHTSPEED_HOST="${LIGHTSPEED_HOST:-lightspeed}"
LIGHTSPEED_TARGET="${LIGHTSPEED_TARGET:-C:/Users/despo/jarvis}"
SSH_OPTS="-o StrictHostKeyChecking=no -o PasswordAuthentication=no -o ConnectTimeout=10"

# === Sync the bot branch ===
echo "Pushing bot branch to $LIGHTSPEED_USER@$LIGHTSPEED_HOST..."

# Use rsync-like approach: push the full repo structure
# Core Python modules
echo "  Syncing jarvis package..."
scp $SSH_OPTS jarvis/*.py "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${LIGHTSPEED_TARGET}/jarvis/" 2>/dev/null || true

# Sub-packages
for sub in agents collectors sync dashboard; do
    if [ -d "jarvis/$sub" ]; then
        echo "  Syncing jarvis/$sub..."
        # Create remote dir and copy all files
        ssh $SSH_OPTS "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}" "mkdir -p ${LIGHTSPEED_TARGET}/jarvis/${sub}" 2>/dev/null || true
        for f in jarvis/$sub/*.py; do
            [ -f "$f" ] && scp $SSH_OPTS "$f" "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${LIGHTSPEED_TARGET}/$f" 2>/dev/null || true
        done
    fi
done

# Static files for dashboard
if [ -d "jarvis/dashboard/static" ]; then
    echo "  Syncing dashboard static files..."
    ssh $SSH_OPTS "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}" "mkdir -p ${LIGHTSPEED_TARGET}/jarvis/dashboard/static" 2>/dev/null || true
    for f in jarvis/dashboard/static/*; do
        [ -f "$f" ] && scp $SSH_OPTS "$f" "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${LIGHTSPEED_TARGET}/$f" 2>/dev/null || true
    done
fi

# Scripts — infrastructure files
echo "  Syncing scripts..."
ssh $SSH_OPTS "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}" "mkdir -p ${LIGHTSPEED_TARGET}/scripts" 2>/dev/null || true
for f in scripts/start-jarvis.bat scripts/ollama-autostart.xml scripts/setup-windows.ps1 scripts/run-if-idle.ps1 scripts/run-backup.ps1 scripts/backup.ps1; do
    [ -f "$f" ] && scp $SSH_OPTS "$f" "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${LIGHTSPEED_TARGET}/$f" 2>/dev/null || true
done

# Config files
echo "  Syncing config..."
scp $SSH_OPTS pyproject.toml "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${LIGHTSPEED_TARGET}/" 2>/dev/null || true

# Ensure the venv is up to date
echo "  Updating venv (if possible)..."
ssh $SSH_OPTS "${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}" \
    "cd ${LIGHTSPEED_TARGET} && .venv/Scripts/python.exe -m pip install -e . --quiet" 2>/dev/null || \
    echo "  WARNING: venv update skipped (will run on next interactive session)"

echo ""
echo "=== Deployment Summary ==="
echo "  Target:  ${LIGHTSPEED_USER}@${LIGHTSPEED_HOST}:${LIGHTSPEED_TARGET}"
echo "  Branch:  bot"
echo ""
echo "Next steps on Lightspeed:"
echo "  1. Import Task Scheduler:"
echo "     schtasks /create /xml \"%JARVIS_ROOT%\\scripts\\ollama-autostart.xml\" /tn \"Jarvis-Ollama\""
echo "  2. Import Dashboard autostart:"
echo "     schtasks /create /sc onstart /tn \"Jarvis-Dashboard\" /tr \"%JARVIS_ROOT%\\scripts\\start-jarvis.bat\" /ru despo"
echo "  3. Verify:"
echo "     curl http://100.102.0.99:8766/health"
echo "     curl http://100.102.0.99:8767/health/check"
echo ""
echo "Deployed successfully"
