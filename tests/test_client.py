"""
Tests for the thin-client layer: cache (outbox/tail) + remote client.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from jarvis.cache import Cache, flush_outbox, BACKOFF


def _cache(tmp_path):
    return Cache(path=tmp_path / "cache.db")


# ── outbox ───────────────────────────────────────────────────────────────────
def test_enqueue_idempotent(tmp_path):
    c = _cache(tmp_path)
    assert c.enqueue("hello world one", source="manual") is True
    assert c.enqueue("hello world one", source="manual") is False  # dup
    assert c.pending_count() == 1
    assert c.enqueue("second different note") is True
    assert c.pending_count() == 2
    c.close()


def test_enqueue_ignores_blank(tmp_path):
    c = _cache(tmp_path)
    assert c.enqueue("   ") is False
    assert c.pending_count() == 0
    c.close()


def test_synced_removes_and_retry_backs_off(tmp_path):
    c = _cache(tmp_path)
    c.enqueue("note a")
    oid = c.due()[0]["id"]
    c.mark_retry(oid)          # attempt 1 -> 5s backoff -> not due yet
    assert c.due() == []
    assert c.pending_count() == 1
    # force due (re-schedule immediately) and confirm it surfaces again
    c.conn.execute("UPDATE outbox SET next_attempt_at = NULL WHERE id = ?", (oid,))
    c.conn.commit()
    assert len(c.due()) == 1
    c.mark_synced(oid)
    assert c.pending_count() == 0
    c.close()
def test_tail_search_marks_stale(tmp_path):
    c = _cache(tmp_path)
    c.store_tail([
        {"id": "m1", "content": "met alice at the cafe", "source": "manual",
         "timestamp": "2026-01-01", "tier": "session", "tags": ["t"]},
    ])
    res = c.tail_search("alice")
    assert len(res) == 1
    assert res[0]["stale"] is True
    assert res[0]["tags"] == ["t"]
    assert c.tail_search("zzz-none") == []
    c.close()


def test_tail_cache_evicts_to_cap(tmp_path):
    c = _cache(tmp_path)
    c.store_tail([{"id": f"m{i}", "content": f"note {i}", "source": "s",
                   "timestamp": "2026", "tier": "raw"} for i in range(10)], cap=5)
    assert len(c.tail_search("note")) == 5
    c.close()


# ── flush / offline ──────────────────────────────────────────────────────────
def test_flush_offline_keeps_items(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MODE", "client")
    monkeypatch.setenv("JARVIS_REMOTE", "http://lightspeed")
    monkeypatch.setenv("JARVIS_TOKEN", "")
    c = _cache(tmp_path)
    c.enqueue("hi there")
    with patch("jarvis.remote.remote_ok", return_value=False):
        res = flush_outbox(c)
    assert res["offline"] is True
    assert c.pending_count() == 1  # never dropped
    c.close()


def test_flush_pushes_and_clears(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MODE", "client")
    monkeypatch.setenv("JARVIS_REMOTE", "http://lightspeed")
    c = _cache(tmp_path)
    c.enqueue("a")
    c.enqueue("b")
    with patch("jarvis.remote.remote_ok", return_value=True), \
         patch("jarvis.remote.remember_batch", return_value={"added": 2, "skipped": 0}) as m:
        res = flush_outbox(c)
    assert res["pushed"] == 2
    assert res["offline"] is False
    assert c.pending_count() == 0
    m.assert_called_once()
    c.close()


# ── remote client ────────────────────────────────────────────────────────────
def test_remote_is_remote_gating(monkeypatch):
    monkeypatch.delenv("JARVIS_MODE", raising=False)
    monkeypatch.delenv("JARVIS_REMOTE", raising=False)
    from jarvis import remote
    assert remote.is_remote() is False
    monkeypatch.setenv("JARVIS_MODE", "client")
    monkeypatch.setenv("JARVIS_REMOTE", "http://lightspeed:8766")
    assert remote.is_remote() is True


def test_remote_remember_batch_posts(monkeypatch):
    monkeypatch.setenv("JARVIS_MODE", "client")
    monkeypatch.setenv("JARVIS_REMOTE", "http://lightspeed:8766")
    from jarvis import remote
    captured = {}
    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b'{"added": 1, "skipped": 0}'
    def fake_open(req, timeout=60):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        return FakeResp()
    with patch.object(remote.urllib.request, "urlopen", fake_open):
        res = remote.remember_batch([{"content": "x"}])
    assert res["added"] == 1
    assert captured["url"].endswith("/api/remember")
    assert captured["method"] == "POST"
    body = json.loads(captured["body"])
    assert body["memories"][0]["content"] == "x"


# ── backoff schedule ─────────────────────────────────────────────────────────
def test_backoff_schedule():
    assert BACKOFF == [5, 15, 60, 300]
