#!/bin/bash
set -e

# Derive a port from the worktree name to avoid collisions
WORKTREE_NAME=$(basename "$WORKTREE_PATH")
PORT_OFFSET=$(echo "$WORKTREE_NAME" | cksum | awk '{print $1 % 1000}')
PORT=$((3000 + PORT_OFFSET))

echo "Starting Jarvis worktree: $WORKTREE_NAME on port $PORT"

# Start the local dev server for this worktree if possible
# Adjust this to match your actual app entrypoint
if [ -f "$WORKTREE_PATH/jarvis/cli.py" ] || [ -f "$REPO_PATH/jarvis/cli.py" ]; then
  echo "Run script placeholder for $WORKTREE_NAME — start your app here"
else
  echo "No runnable entrypoint found in worktree $WORKTREE_NAME"
fi
