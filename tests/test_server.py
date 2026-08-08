"""
Tests for jarvis/server.py thin-client API endpoints (mounted on dashboard.app).
Runs against a temp store with mocked embeddings/extraction so no network or
real data is touched.
"""
from __future__ import annotations

from pathlib import Path
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


# ── memories list ─────────────────────────────────────────────────────────────
def test_api_memories_endpoint(client):
    client.post("/api/remember", json={"memories": [
        {"content": "recent alpha note body", "source": "manual"},
        {"content": "another beta note body", "source": "manual", "tier": "session"},
    ]})
    r = client.get("/api/memories", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert "memories" in body
    assert len(body["memories"]) >= 2
    # tags are pre-decoded to a list (not a JSON string)
    for m in body["memories"]:
        assert isinstance(m.get("tags"), list)


def test_api_memories_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    assert client.get("/api/memories", params={"limit": 5}).status_code == 403
    assert client.get("/api/memories", params={"limit": 5},
                      headers={"X-Jarvis-Token": "sekret"}).status_code == 200


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


def test_admin_backup_endpoint_success(client, tmp_path, monkeypatch):
    """POST /api/admin/backup must produce a valid snapshot via the in-process
    backup endpoint, reading the store from the (patched) data dir."""
    import sqlite3

    root = tmp_path / "root"
    (root / "data" / "chroma" / "col").mkdir(parents=True)
    con = sqlite3.connect(root / "data" / "meta.db")
    con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)")
    con.execute("INSERT INTO memories VALUES ('m', 'text')")
    con.commit()
    con.close()
    (root / "data" / "chroma" / "col" / "data_level0.bin").write_bytes(b"\x01")

    monkeypatch.setattr("jarvis.paths.data_dir", lambda *p: Path(root, *p))
    snap = tmp_path / "snap"
    r = client.post("/api/admin/backup", json={"dst": str(snap)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dst"] == str(snap)
    assert (snap / "meta.db").exists()
    assert body["strict"] is False


def test_admin_backup_endpoint_requires_token(client, monkeypatch):
    """When JARVIS_TOKEN is set, the backup endpoint must reject a request
    without a token (non-loopback → 403)."""
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    r = client.post("/api/admin/backup", json={"dst": "/tmp/nope"})
    assert r.status_code == 403


def test_api_query_returns_grounded_answer(client, monkeypatch):
    """/api/query is the grounded ask endpoint (Brain.query) — it must return a
    clean {answer, memories, entities} and NOT run the agentic loop."""
    from unittest.mock import patch

    shown = {}
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.embed.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.brain._ollama_chat",
               lambda model, messages: shown.update(m=model) or {"message": {"content": "the grounded answer"}}):
        r = client.post("/api/remember", json={"memories": [{"content": "Alice likes the jarvis project"}]})
        assert r.status_code == 200
        r2 = client.get("/api/query", params={"q": "what does alice like?", "n": 5})
        assert r2.status_code == 200
        body = r2.json()
        assert body["answer"] == "the grounded answer"
        assert "memories" in body and "entities" in body


def test_api_query_requires_token(client, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    assert client.get("/api/query", params={"q": "hi"}).status_code == 403


def test_api_digest_endpoint(client, monkeypatch):
    """POST /api/digest must generate an on-demand digest in-process."""
    from unittest.mock import patch

    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.embed.get_embedding", return_value=[0.1] * 8):
        r0 = client.post("/api/remember", json={"memories": [{"content": "shipped the push queue"}]})
        assert r0.status_code == 200

    with patch("jarvis.task_queue.TaskQueue"), \
         patch("jarvis.brain._ollama_chat",
               lambda model, messages: {"message": {"content": "morning digest text"}}):
        r = client.post("/api/digest", json={"kind": "morning_brief"})
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "morning_brief"
        assert "morning digest text" in body["text"]


def test_api_digest_requires_token(client, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    assert client.post("/api/digest", json={"kind": "morning_brief"}).status_code == 403


# ── server.py shim ─────────────────────────────────────────────────────────────
def test_server_shim_exposes_same_app():
    from jarvis import dashboard, server
    assert server.app is dashboard.app
    assert server.DEFAULT_PORT == dashboard.DEFAULT_PORT
    assert server.DEFAULT_DAEMON_URL == dashboard.DEFAULT_DAEMON_URL


def test_backup_command_snapshots_given_data_dir(tmp_path):
    """`jarvis backup <dst> --data-dir <root>` produces a crash-consistent
    snapshot dir with the SQLite files online-backed."""
    from click.testing import CliRunner

    from jarvis.cli import cli

    data = _store_fixture(tmp_path)
    dst = tmp_path / "snap"
    result = CliRunner().invoke(cli, ["backup", str(dst), "--data-dir", str(data)])
    assert result.exit_code == 0, result.output
    assert (dst / "meta.db").exists()
    assert (dst / "chroma" / "chroma.sqlite3").exists()
    assert "sqlite online-backed" in result.output
    assert "snapshot ->" in result.output


def _store_fixture(root) -> Path:
    import sqlite3

    data = root / "data"
    (data / "chroma" / "col").mkdir(parents=True)
    con = sqlite3.connect(data / "meta.db")
    con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)")
    con.execute("INSERT INTO memories VALUES ('m', 'text')")
    con.commit()
    con.close()
    con = sqlite3.connect(data / "chroma" / "chroma.sqlite3")
    con.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    (data / "chroma" / "col" / "data_level0.bin").write_bytes(b"\x00")
    return data


def test_server_run_delegates_to_run_dashboard(monkeypatch):
    from jarvis import dashboard, server
    calls = {}
    monkeypatch.setattr(dashboard, "run_dashboard",
                        lambda **kw: calls.update(kw))
    server.run(port=9876, daemon_url="http://daemon:8765")
    assert calls == {"port": 9876, "daemon_url": "http://daemon:8765",
                     "ssl_cert": None, "ssl_key": None}


def test_server_run_forwards_tls_args(monkeypatch):
    from jarvis import dashboard, server
    calls = {}
    monkeypatch.setattr(dashboard, "run_dashboard",
                        lambda **kw: calls.update(kw))
    server.run(port=9876, daemon_url="http://daemon:8765",
               ssl_cert="/tmp/c.pem", ssl_key="/tmp/k.pem")
    assert calls["ssl_cert"] == "/tmp/c.pem"
    assert calls["ssl_key"] == "/tmp/k.pem"


# ── /api/query: model + history params ────────────────────────────────────────
def test_api_query_model_param_resolves_force(client, monkeypatch):
    """GET /api/query?model=<tier> must push the override into select_model_for
    (as `force`) and surface the resolved model in the response."""
    seen = {}
    monkeypatch.setattr(
        "jarvis.brain.select_model_for",
        lambda question, force=None: seen.update(force=force) or "resolved-big",
    )

    class _FakeBrain:
        def __init__(self, store, model=None):
            self.model = model
        def query(self, user_query, n_results=8, source_filter=None,
                  verbose=False, history=None):
            return "grounded answer", []

    monkeypatch.setattr("jarvis.brain.Brain", _FakeBrain)

    r = client.get("/api/query", params={"q": "hard question", "model": "big"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen.get("force") == "big"
    assert body["model"] == "resolved-big"


def test_api_query_history_param_is_threaded(client, monkeypatch):
    """GET /api/query with a `history` JSON array must forward it to
    Brain.query(history=...) so follow-ups stay coherent."""
    calls = {}
    monkeypatch.setattr(
        "jarvis.brain.select_model_for",
        lambda question, force=None: "m",
    )

    class _FakeBrain:
        def __init__(self, store, model=None):
            self.model = model
        def query(self, user_query, n_results=8, source_filter=None,
                  verbose=False, history=None):
            calls.update(q=user_query, history=history)
            return "grounded answer", []

    monkeypatch.setattr("jarvis.brain.Brain", _FakeBrain)

    hist = [{"role": "user", "content": "prior turn"}]
    r = client.get("/api/query", params={"q": "follow up",
                                         "history": str(hist).replace("'", '"')})
    assert r.status_code == 200, r.text
    assert calls.get("history") == hist


def test_api_query_malformed_history_falls_back_to_empty(client, monkeypatch):
    """Malformed `history` JSON must NOT 4xx — the handler degrades to an empty
    history list and still answers (graceful fallback, not a hard error)."""
    calls = {}
    monkeypatch.setattr(
        "jarvis.brain.select_model_for",
        lambda question, force=None: "m",
    )

    class _FakeBrain:
        def __init__(self, store, model=None):
            self.model = model
        def query(self, user_query, n_results=8, source_filter=None,
                  verbose=False, history=None):
            calls.update(history=history)
            return "still answered", []

    monkeypatch.setattr("jarvis.brain.Brain", _FakeBrain)

    r = client.get("/api/query", params={"q": "question",
                                         "history": "not-json{{{"})
    assert r.status_code == 200, r.text
    assert calls.get("history") == []


# ── /api/chat: model param ────────────────────────────────────────────────────
def test_api_chat_model_param_honored(client, monkeypatch):
    """POST /api/chat with a `model` must forward it to run_turn (not drop it)."""
    seen = {}
    monkeypatch.setattr(
        "jarvis.agent.run_turn",
        lambda message, session_id, max_steps=8, session_db=None,
        store_db=None, verbose=False, model=None:
        seen.update(message=message, session_id=session_id, model=model)
        or ("chat reply", ["tool step"]),
    )

    r = client.post("/api/chat", json={"message": "hello", "model": "big",
                                       "session_id": "abc"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen.get("model") == "big"
    assert seen.get("session_id") == "abc"
    assert body["answer"] == "chat reply"
    assert body["tool_log"] == ["tool step"]


def test_api_chat_omits_model_by_default(client, monkeypatch):
    """Without a `model`, run_turn receives model=None (auto-tier)."""
    seen = {}
    monkeypatch.setattr(
        "jarvis.agent.run_turn",
        lambda message, session_id, max_steps=8, session_db=None,
        store_db=None, verbose=False, model=None:
        seen.update(model=model) or ("chat reply", []),
    )

    r = client.post("/api/chat", json={"message": "hello", "session_id": "abc"})
    assert r.status_code == 200, r.text
    assert seen.get("model") is None

