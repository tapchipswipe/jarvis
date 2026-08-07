"""jarvis/remote.py — thin-client HTTP client for the Jarvis server.

Talks to the Lightspeed server API (same app as the dashboard). Used when
JARVIS_MODE=client. Stdlib only.

HTTPS: when JARVIS_REMOTE is an ``https://`` URL the client uses a custom
loader that (a) skips CA verification (self-signed cert) but (b) *pins* the
server cert's SHA256 fingerprint when JARVIS_TLS_FINGERPRINT (or the file
``~/.config/jarvis/server-fingerprint``) is set — giving real MitM resistance
without a CA. Without a pinned fingerprint an https URL still encrypts the
token in transit but is not pinned.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


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


# ── HTTPS with optional cert-fingerprint pinning ──────────────────────────────

def _pinned_fingerprint() -> str | None:
    """Prefer the env var, then the ~/.config/jarvis/server-fingerprint file."""
    fp = os.environ.get("JARVIS_TLS_FINGERPRINT", "").strip().lower()
    if fp:
        return fp
    try:
        f = Path.home() / ".config" / "jarvis" / "server-fingerprint"
        if f.exists():
            fp = (f.read_text(encoding="utf-8") or "").strip().lower()
            return fp or None
    except (OSError, UnicodeDecodeError):
        pass
    return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that records and, when pinned, verifies the peer cert."""

    def connect(self):
        super().connect()
        asked = _pinned_fingerprint()
        if not asked:
            return
        try:
            der = self.sock.getpeercert(binary_form=True)
            actual = hashlib.sha256(der).hexdigest()
        except Exception:  # noqa: BLE001 - missing cert => treat as mismatch
            self.sock.close()
            raise ssl.SSLError("unable to read peer certificate for pinning")
        if not hmac.compare_digest(actual, asked):
            self.sock.close()
            raise ssl.SSLError(
                f"server cert fingerprint mismatch (got {actual}, expected {asked})")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        ctx = ssl.create_default_context()
        # Self-signed, so we can't chain to a CA; pinning is the trust anchor.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return self.do_open(_PinnedHTTPSConnection, req, context=ctx)


def _opener() -> urllib.request.OpenerDirector:
    if "__opener" not in _opener.__dict__:
        _opener.__opener = urllib.request.build_opener(_PinnedHTTPSHandler())
    return _opener.__opener


def _open(req: urllib.request.Request, timeout: int):
    if server_url().startswith("https://"):
        return _opener().open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _request(method: str, path: str, payload=None, timeout: int = 60):
    url = server_url() + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    with _open(req, timeout) as resp:
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


def memories(limit: int = 50, source: str | None = None, tier: str | None = None,
             since: str | None = None) -> dict:
    """Recent memories list from the box (tags/metadata pre-decoded)."""
    qs = urllib.parse.urlencode({k: v for k, v in (
        ("limit", limit), ("source", source), ("tier", tier), ("since", since),
    ) if v is not None})
    return _request("GET", f"/api/memories?{qs}")


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


def query(question: str, n: int = 8, source: str | None = None,
          history: list | None = None, model: str | None = None,
          timeout: int = 180) -> dict:
    """Grounded Q&A against the box brain (Brain.query — clean answer, not the
    agentic loop; unlike chat this never returns a tool fragment).

    `model` optionally overrides the box's auto-tiered model selection (a tier
    name like 'fast'/'big', or an exact model id). Uses a long timeout (LLM
    answer generation can take a while, especially with a large chat model)."""
    qs = urllib.parse.urlencode({"q": question, "n": n})
    if source:
        qs += "&" + urllib.parse.urlencode({"source": source})
    if history:
        qs += "&history=" + urllib.parse.quote(json.dumps(history))
    if model:
        qs += "&model=" + urllib.parse.quote(model)
    return _request("GET", f"/api/query?{qs}", timeout=timeout)


def digest(kind: str = "morning_brief") -> dict:
    """Ask the box to generate a digest on demand (/api/digest, in-process)."""
    return _request("POST", "/api/digest", {"kind": kind}, timeout=180)


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
