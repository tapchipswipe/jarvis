"""
Tests for the durable push queue (store) + push_memories helpers + push.py.

Covers:
  - push_queue enqueue/dedup/due/fail/success/stats
  - log_sync writes to sync_log
  - push_backoff schedule
  - build_sidecar / _enqueue_new_memories
  - stage_bundle produces <device_id>/... txt+json pairs and a tar.gz
  - push_bundle success and failure (fallback) paths
"""
from __future__ import annotations

import tarfile
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from jarvis import push_memories as pm
from jarvis.sync.push import PUSH_BACKOFFS, push_backoff, push_bundle, stage_bundle


def _store(tmp_path):
    from jarvis.store import Store as S
    with patch("jarvis.store.chromadb.PersistentClient"):
        return S(chroma_dir=tmp_path / "chroma", db_path=tmp_path / "meta.db")


# ── store push queue ─────────────────────────────────────────────────────────

def test_enqueue_dedup_by_push_key(tmp_path):
    store = _store(tmp_path)
    try:
        store.enqueue_push("k1", "hello", {"tier": "raw"})
        store.enqueue_push("k1", "hello", {"tier": "raw"})  # duplicate ignored
        assert store.push_queue_stats()["total"] == 1
    finally:
        store.close()


def test_push_queue_lifecycle(tmp_path):
    store = _store(tmp_path)
    try:
        store.enqueue_push("k1", "hello", {"tier": "raw"})
        due = store.push_queue_due()
        assert len(due) == 1
        qid = due[0]["id"]

        # Fail -> item stays with attempts + future next_attempt_at
        nxt = (datetime.now(timezone.utc).isoformat())
        store.push_queue_fail(qid, "boom", 1, nxt)
        # Not due now (next_attempt_at is now, but re-check with a past now)
        assert store.push_queue_due(now="2000-01-01T00:00:00") == []
        retry = store.push_queue_due(now="2999-01-01T00:00:00")
        assert len(retry) == 1
        assert retry[0]["attempts"] == 1

        store.push_queue_success(qid)
        assert store.push_queue_stats()["total"] == 0
    finally:
        store.close()


def test_log_sync_writes_row(tmp_path):
    store = _store(tmp_path)
    try:
        store.log_sync("device_push", "a", "b", 5, 2)
        rows = store.conn.execute("SELECT * FROM sync_log").fetchall()
        assert len(rows) == 1
        assert rows[0]["items_added"] == 5
        assert rows[0]["items_skipped"] == 2
    finally:
        store.close()


# ── backoff ──────────────────────────────────────────────────────────────────

def test_push_backoff_schedule():
    assert push_backoff(0) == 0
    assert push_backoff(1) == PUSH_BACKOFFS[0]
    assert push_backoff(2) == PUSH_BACKOFFS[1]
    # After the last entry, it stays at the max backoff.
    assert push_backoff(99) == PUSH_BACKOFFS[-1]


# ── push_memories helpers ────────────────────────────────────────────────────

def test_build_sidecar(tmp_path):
    store = _store(tmp_path)
    try:
        util_mem = store.conn.execute(
            "SELECT * FROM memories WHERE source = 'manual' LIMIT 1"
        ).fetchall()
        if not util_mem:
            pytest.skip("no manual memories seeded")
        row = util_mem[0]
        sidecar = pm.build_sidecar(row, "dev1")
        assert sidecar["source_device"] == "dev1"
        assert sidecar["tier"] == row["tier"]
        assert sidecar["content_hash"]
    finally:
        store.close()


def test_enqueue_new_memories_dedups(tmp_path):
    store = _store(tmp_path)
    try:
        store.add("m1", "manual", "1", "2026-01-01T10:00:00", "content-a", [], {}, [0.1] * 8)
        store.add("m2", "manual", "2", "2026-01-01T10:00:00", "content-b", [], {}, [0.1] * 8)
        first = pm._enqueue_new_memories(store, "dev1")
        assert first == 2
        second = pm._enqueue_new_memories(store, "dev1")
        assert second == 0  # already queued
    finally:
        store.close()


# ── batch staging / push ─────────────────────────────────────────────────────

def test_stage_bundle_creates_txt_json_and_tar(tmp_path):
    entries = [
        {"content": "alpha", "sidecar": {"tier": "raw", "source": "manual"}},
        {"content": "beta", "sidecar": {"tier": "session", "source": "system"}},
    ]
    bundle = stage_bundle(entries, device_id="dev1")
    try:
        assert bundle.exists()
        with tarfile.open(bundle, "r:gz") as tf:
            names = tf.getnames()
        # device_id/<name>.txt and .json for each entry
        assert any(n.startswith("dev1/") for n in names)
        assert sum(1 for n in names if n.endswith(".txt")) == 2
        assert sum(1 for n in names if n.endswith(".json")) == 2
    finally:
        bundle.unlink(missing_ok=True)


def test_stage_bundle_full_hash_avoids_16hex_collision(monkeypatch):
    """Two distinct contents sharing a 16-hex cid prefix must not collide.

    Under the old `[:16]` truncation, two contents whose digests share the
    first 16 hex chars produced the same on-disk filename, so the second
    entry silently overwrote the first (a dropped memory in the bundle).
    With the full digest as the cid, they must produce two distinct files.
    """
    from jarvis.sync import push as push_mod

    def _fake_sha256(data=b""):
        class _F:
            def __init__(self, data=b""):
                self._data = data

            def hexdigest(self):
                s = self._data.decode()
                # Two contents share the first 16 hex chars ("a"*16) but
                # differ in the 17th, mirroring a real 16-hex-prefix collision.
                tail = "1" if s == "content-one" else "2"
                return "a" * 16 + tail + "0" * 47

        return _F(data)

    monkeypatch.setattr(push_mod.hashlib, "sha256", _fake_sha256)
    entries = [
        {"content": "content-one", "sidecar": {"tier": "raw", "source": "s"}},
        {"content": "content-two", "sidecar": {"tier": "raw", "source": "s"}},
    ]
    bundle = stage_bundle(entries, device_id="dev1")
    try:
        with tarfile.open(bundle, "r:gz") as tf:
            names = tf.getnames()
        txts = sorted(n for n in names if n.endswith(".txt"))
        # Both distinct contents present -> two distinct txt files (no overwrite).
        assert len(txts) == 2
        # The full digest (not a 16-char prefix) is used in the filename.
        assert any("a" * 16 + "1" in n for n in txts)
        assert any("a" * 16 + "2" in n for n in txts)
    finally:
        bundle.unlink(missing_ok=True)


def test_push_bundle_success():
    with patch("jarvis.sync.push.scp_put") as m_scp, \
         patch("jarvis.sync.push.ssh_run") as m_ssh:
        bundle = stage_bundle([{"content": "x", "sidecar": {"tier": "raw", "source": "s"}}], "dev1")
        try:
            assert push_bundle(bundle, inbox="C:/inbox") is True
            m_scp.assert_called_once()
            m_ssh.assert_called_once()
        finally:
            bundle.unlink(missing_ok=True)


def test_push_bundle_failure_falls_back():
    with patch("jarvis.sync.push.scp_put", side_effect=OSError("no net")):
        bundle = stage_bundle([{"content": "x", "sidecar": {"tier": "raw", "source": "s"}}], "dev1")
        try:
            assert push_bundle(bundle, inbox="C:/inbox") is False
        finally:
            bundle.unlink(missing_ok=True)


# ── inbox / daemon path isolation (Workstream E) ─────────────────────────────

def test_inbox_root_honors_env_override(monkeypatch):
    from jarvis.collectors import inbox
    monkeypatch.setenv("JARVIS_INBOX", "/custom/profile/inbox")
    assert str(inbox._get_inbox_root()) == "/custom/profile/inbox"


def test_inbox_root_platform_default(monkeypatch):
    from jarvis.collectors import inbox
    monkeypatch.delenv("JARVIS_INBOX", raising=False)
    monkeypatch.setattr(inbox.platform, "system", lambda: "Windows")
    assert str(inbox._get_inbox_root()) == "C:/data/jarvis/inbox"


def test_processed_path_defaults_next_to_inbox(monkeypatch):
    from jarvis.collectors import inbox
    monkeypatch.delenv("JARVIS_PROCESSED_PATH", raising=False)
    monkeypatch.setenv("JARVIS_INBOX", "/x/inbox")
    assert str(inbox._get_processed_path()) == "/x/processed.json"


def test_daemon_config_honors_inbox_and_logdir_env(monkeypatch):
    from jarvis.sync.daemon import _load_config
    monkeypatch.delenv("SB_DAEMON_PORT", raising=False)
    monkeypatch.delenv("SB_DAEMON_BIND", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_PORT", raising=False)
    monkeypatch.setenv("JARVIS_INBOX", "/profile/inbox")
    monkeypatch.setenv("JARVIS_LOG_DIR", "/profile/logs")
    cfg = _load_config()
    assert cfg["inbox_dir"] == "/profile/inbox"
    assert cfg["log_dir"] == "/profile/logs"