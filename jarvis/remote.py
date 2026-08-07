"""jarvis/remote.py — thin-client HTTP client for the Jarvis server.

Talks to the Lightspeed server API (same app as the dashboard). Used when
JARVIS_MODE=client. Stdlib only.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


def server_url() -> str:
    return os.environ.get("JARVIS_REMOTE", "").rstrip("/")


def server_token() -> str:
    return os.environ.get("JARVIS_TOKEN", "")


def is_remote() -> bool:
    """True when running as a thin client with a configured server."""
    return os.environ.get("JARVIS_MODE", "local") == "client" and bool(server_url())


def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if server_token():
        h["X-Jarvis-Token"] = server_token()
    return h


def _request(method: str, path: str, payload=None, timeout: int = 60):
    url = server_url() + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def remote_ok(timeout: int = 5) -> bool:
    try:
        _request("GET", "/api/health", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001 - treat any failure as offline
        return False


def health():
    return _request("GET", "/api/health")


def health_deep():
    """Store-aware health: includes the box's active memory count."""
    return _request("GET", "/api/health/deep")


def ingest_status():
    """Inbox-backlog ingester progress on the box (active/enabled/processed/remaining)."""
    return _request("GET", "/api/ingest/status")


def remember_batch(memories: list[dict]) -> dict:
    return _request("POST", "/api/remember", {"memories": memories})


def backfill_batch(memories: list[dict]) -> dict:
    """POST a field-preserving batch to /api/backfill (one-time migration)."""
    return _request("POST", "/api/backfill", {"memories": memories}, timeout=600)


def search(q: str, n: int = 10, source: str | None = None) -> dict:
    qs = urllib.parse.urlencode({"q": q, "n": n})
    if source:
        qs += "&" + urllib.parse.urlencode({"source": source})
    return _request("GET", f"/api/search?{qs}")


def chat(message: str, session_id: str | None = None, max_steps: int = 8, model: str | None = None) -> dict:
    payload = {"message": message, "session_id": session_id, "max_steps": max_steps}
    if model:
        payload["model"] = model
    return _request("POST", "/api/chat", payload, timeout=180)


def list_sessions() -> dict:
    return _request("GET", "/api/sessions")


def create_session(title: str = "Chat") -> dict:
    return _request("POST", "/api/sessions", {"title": title})


def session_messages(session_id: str) -> dict:
    return _request("GET", f"/api/sessions/{session_id}/messages")


def export(fmt: str = "json"):
    return _request("GET", f"/api/export?fmt={fmt}")
