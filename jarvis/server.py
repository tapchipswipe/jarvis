"""jarvis/server.py — canonical thin-client server entrypoint.

The FastAPI app and its handlers live in `jarvis.dashboard` (shared with the
dashboard UI served by the same process). This module is the *deployable back-
end surface* for Lightspeed: it re-exposes the app and provides a single `run()`
entry used by the `jarvis server` CLI. It makes the architecture explicit —
the server owns the canonical store, the read/write/search/chat API, and the
Mayor loop — while the Mac acts as a thin client (mode=client).
"""

from __future__ import annotations

from jarvis import dashboard

app = dashboard.app
DEFAULT_PORT = dashboard.DEFAULT_PORT
DEFAULT_DAEMON_URL = dashboard.DEFAULT_DAEMON_URL


def run(port: int = DEFAULT_PORT, daemon_url: str = DEFAULT_DAEMON_URL) -> None:
    """Start the canonical Jarvis server (FastAPI app + Mayor background loop)."""
    dashboard.run_dashboard(port=port, daemon_url=daemon_url)
