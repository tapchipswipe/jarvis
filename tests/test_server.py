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