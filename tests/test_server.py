"""
Tests for jarvis/server.py thin-client API endpoints (mounted on dashboard.app).
Runs against a temp store with mocked embeddings/extraction so no network or
real data is touched.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from jarvis import dashboard


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from jarvis.store import Store

    def _make():
        return Store(chroma_dir=tmp_path / "chroma", db_path=tmp_path / "meta.db")

    with patch("jarvis.store.chromadb.PersistentClient"):
        _make()  # ensure dirs exist
    monkeypatch.setattr(dashboard, "_get_store", _make)
    return TestClient(dashboard.app)


@pytest.fixture(autouse=True)
def _no_net(monkeypatch):
    monkeypatch.setattr("jarvis.brain.get_embedding", lambda *a, **k: [0.1] * 8)
    monkeypatch.setattr("jarvis.embed.get_embedding", lambda *a, **k: [0.1] * 8)
    monkeypatch.setattr("jarvis.embed.get_embeddings", lambda texts, **k: [[0.1] * 8 for _ in texts])
    monkeypatch.setattr("jarvis.extract.extract_metadata", lambda *a, **k: {"tags": [], "entities": []})


# ── health ───────────────────────────────────────────────────────────────────
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "mode" in body
    assert "uptime" in body
    # liveness must NOT touch the store (pure liveness -> never stalls under load)
    assert "memories" not in body


def test_health_deep(client):
    r = client.get("/api/health/deep")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "memories" in body


# ── remember ────────────────────────────────────────────────────────────────
def test_remember_adds_memories(client):
    r = client.post("/api/remember", json={"memories": [
        {"content": "A note about alice", "source": "manual"},
        {"content": "Another bit about bob", "source": "manual"},
    ]})
    assert r.status_code == 200
    assert r.json()["added"] >= 1


def test_remember_rejects_bad_payload(client):
    r = client.post("/api/remember", json={"memories": "nope"})
    assert r.status_code == 400
    r2 = client.post("/api/remember", content=b"not-json", headers={"Content-Type": "application/json"})
    assert r2.status_code in (400, 422)  # FastAPI rejects non-object body


# ── backfill (field-preserving migration) ───────────────────────────────────
def test_backfill_preserves_fields(client):
    r = client.post("/api/backfill", json={"memories": [
        {"id": "bf1", "content": "scene from the orchard", "source": "manual",
         "source_id": "src-1", "timestamp": "2020-01-01T00:00:00",
         "tags": ["apple"], "metadata": {"key": "val"}, "tier": "session",
         "route": "idea_capture", "superseded": 0},
        {"id": "bf2", "content": "another memory note", "source": "device",
         "tier": "raw", "route": "unclassified"},
        {"content": "   ", "id": "bf-blank"},  # blank -> skipped
    ]})
    assert r.status_code == 200
    assert r.json()["added"] == 2
    assert r.json()["skipped"] == 1
    # verify fields were preserved verbatim, not re-timestamped / re-tiered
    store = dashboard._get_store()
    try:
        row = store.conn.execute("SELECT * FROM memories WHERE id = 'bf1'").fetchone()
        assert row is not None
        assert row["timestamp"] == "2020-01-01T00:00:00"
        assert row["tier"] == "session"
        assert row["route"] == "idea_capture"
        assert row["source_id"] == "src-1"
    finally:
        store.close()


def test_backfill_rejects_bad_payload(client):
    r = client.post("/api/backfill", json={"memories": "nope"})
    assert r.status_code == 400


# ── search ───────────────────────────────────────────────────────────────────
def test_search_requires_query(client):
    r = client.get("/api/search", params={"q": ""})
    assert r.status_code == 400


def test_search_returns_shape(client):
    r = client.get("/api/search", params={"q": "hello", "n": 5})
    assert r.status_code == 200
    body = r.json()
    assert "memories" in body
    assert "entities" in body
    assert "count" in body


# ── sessions ────────────────────────────────────────────────────────────────
def test_sessions_roundtrip(client):
    created = client.post("/api/sessions", json={"title": "Test"}).json()
    sid = created["session_id"]
    assert sid
    lst = client.get("/api/sessions").json()
    assert any(s["id"] == sid for s in lst["sessions"])
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    assert "messages" in msgs


# ── chat ─────────────────────────────────────────────────────────────────────
def test_chat_requires_message(client):
    r = client.post("/api/chat", json={"message": "   "})
    assert r.status_code == 400


def test_chat_runs_without_ollama(client, monkeypatch):
    from jarvis import agent
    monkeypatch.setattr(
        agent, "_chat_with_fallback",
        lambda *a, **k: {"message": {"content": "Hi from Jolene", "role": "assistant"}},
    )
    r = client.post("/api/chat", json={"message": "hello", "session_id": None})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"]
    assert "answer" in body
    assert "tool_log" in body


# ── export ───────────────────────────────────────────────────────────────────
def test_export_json(client):
    r = client.get("/api/export", params={"fmt": "json"})
    assert r.status_code == 200
    assert "memories" in r.json()


def test_export_markdown(client):
    r = client.get("/api/export", params={"fmt": "md"})
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]


# ── token auth ───────────────────────────────────────────────────────────────

# token auth (pure logic; TestClient is loopback so host is trusted)
def test_token_logic(monkeypatch):
    from jarvis.dashboard import _client_token_ok
    monkeypatch.delenv("JARVIS_TOKEN", raising=False)
    assert _client_token_ok("100.64.0.5", "") is True
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    assert _client_token_ok("100.64.0.5", "") is False
    assert _client_token_ok("100.64.0.5", "wrong") is False
    assert _client_token_ok("100.64.0.5", "sekret") is True
    assert _client_token_ok("127.0.0.1", "") is True
    assert _client_token_ok("localhost", "nope") is True


def test_health_ok_from_loopback(client, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    r = client.get("/api/health")
    assert r.status_code == 200


# every mutating + sensitive-read route must be guarded by _host_ok; TestClient's
# host is "testclient" (non-loopback), so with a token configured an unauthenticated
# request must be rejected with 403 — and with the right token must pass.
def test_mutating_routes_reject_without_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    assert client.get("/api/export").status_code == 403
    assert client.post("/api/idea", json={"idea": "x", "source": "t"}).status_code == 403
    assert client.post("/api/sessions", json={"title": "x"}).status_code == 403
    assert client.post("/api/tasks/approve", params={"all": "true"}).status_code == 403
    assert client.post("/api/tasks/reject", params={"id": "nope"}).status_code == 403
    # previously-guarded routes stay guarded too
    assert client.post("/api/remember", json={"memories": [{"content": "x"}]}).status_code == 403
    assert client.get("/api/search", params={"q": "x"}).status_code == 403
    assert client.post("/api/chat", json={"message": "x"}).status_code == 403
    # health stays open (pure liveness for the Mac health checker)
    assert client.get("/api/health").status_code == 200


def test_mutating_routes_pass_with_valid_token(client, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    h = {"X-Jarvis-Token": "sekret"}
    assert client.get("/api/export", headers=h).status_code == 200
    assert client.get("/api/search", params={"q": "hello", "n": 5}, headers=h).status_code == 200


def test_ingest_status_endpoint(client):
    """Inbox-ingester telemetry is open (liveness-grade) and returns the shape."""
    r = client.get("/api/ingest/status")
    assert r.status_code == 200
    body = r.json()
    assert "active" in body and "enabled" in body and "uptime" in body


# ── server.py shim ─────────────────────────────────────────────────────────────
def test_server_shim_exposes_same_app():
    from jarvis import dashboard, server
    assert server.app is dashboard.app
    assert server.DEFAULT_PORT == dashboard.DEFAULT_PORT
    assert server.DEFAULT_DAEMON_URL == dashboard.DEFAULT_DAEMON_URL


def test_server_run_delegates_to_run_dashboard(monkeypatch):
    from jarvis import dashboard, server
    calls = {}
    monkeypatch.setattr(dashboard, "run_dashboard",
                        lambda port, daemon_url: calls.update(port=port, daemon_url=daemon_url))
    server.run(port=9876, daemon_url="http://daemon:8765")
    assert calls == {"port": 9876, "daemon_url": "http://daemon:8765"}

