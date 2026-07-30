#!/bin/bash
set -e
echo "Setting up worktree at $WORKTREE_PATH"
echo "Repo root: $REPO_PATH"

# Install Python dependencies if venv or pyproject.toml exists
if [ -f "$REPO_PATH/pyproject.toml" ]; then
  if [ -d "$REPO_PATH/.venv" ]; then
    echo "Using existing .venv at repo root"
  else
    python3 -m venv "$REPO_PATH/.venv" 2>/dev/null || true
    if [ -f "$REPO_PATH/.venv/bin/pip" ]; then
      "$REPO_PATH/.venv/bin/pip" install -e "$REPO_PATH" 2>/dev/null || true
    fi
  fi
fi

# Create local data directory inside the worktree
mkdir -p "$WORKTREE_PATH/data"

echo "Worktree setup complete."
