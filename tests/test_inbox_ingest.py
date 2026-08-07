"""Tests for jarvis/inbox_ingest.py (server-side throttled inbox ingester).

No LLM/network: embeddings are mocked, and the ingest path is sidecar-driven
(never calls extract_metadata/classify).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from jarvis.store import Store


def _store(tmp_path: Path) -> Store:
    with patch("jarvis.store.chromadb.PersistentClient"):
        pass
    return Store(chroma_dir=tmp_path / "chroma", db_path=tmp_path / "meta.db")


def _make_inbox(tmp_path: Path, name: str = "note.txt") -> Path:
    d = tmp_path / "inbox" / "dev123"
    d.mkdir(parents=True, exist_ok=True)
    txt = d / name
    txt.write_text("A raw deep scan snippet about tailscale setup.", encoding="utf-8")
    sc = txt.with_suffix(".json")
    sc.write_text(json.dumps({
        "version": 2, "source": "deep", "source_id": "/mac/path/note.txt",
        "timestamp": "2026-07-22T03:21:45.007015", "tier": "raw",
        "route": "unclassified", "tags": ["deep"], "tag_seeds": ["deep"],
    }), encoding="utf-8")
    return txt


def test_ingest_preserves_sidecar_metadata(tmp_path):
    txt = _make_inbox(tmp_path)
    s = _store(tmp_path)
    try:
        with patch("jarvis.embed.get_embedding", lambda *a, **k: [0.1] * 8):
            added = __import__("jarvis.inbox_ingest", fromlist=["ingest_inbox_file"]).ingest_inbox_file(s, txt)
        assert added >= 1
        row = s.conn.execute("SELECT * FROM memories WHERE source='deep'").fetchone()
        assert row is not None
        assert row["source_id"] == "/mac/path/note.txt"
        assert row["timestamp"] == "2026-07-22T03:21:45.007015"
        assert row["tier"] == "raw"
        assert row["route"] == "unclassified"
        tags = json.loads(row["tags"])
        assert "deep" in tags
        assert "dev123" in tags  # device_id added
    finally:
        s.close()


def test_ingest_idempotent_on_content(tmp_path):
    txt = _make_inbox(tmp_path)
    s = _store(tmp_path)
    try:
        with patch("jarvis.embed.get_embedding", lambda *a, **k: [0.1] * 8):
            mod = __import__("jarvis.inbox_ingest", fromlist=["ingest_inbox_file"])
            first = mod.ingest_inbox_file(s, txt)
            second = mod.ingest_inbox_file(s, txt)
        assert first >= 1
        assert second == 0
    finally:
        s.close()


def test_blank_or_missing_ignored(tmp_path):
    d = tmp_path / "in" / "d"
    d.mkdir(parents=True, exist_ok=True)
    blank = d / "blank.txt"
    blank.write_text("\n   \n", encoding="utf-8")
    s = _store(tmp_path)
    try:
        mod = __import__("jarvis.inbox_ingest", fromlist=["ingest_inbox_file"])
        with patch("jarvis.embed.get_embedding", lambda *a, **k: [0.1] * 8):
            assert mod.ingest_inbox_file(s, blank) == 0
    finally:
        s.close()


def test_ingest_status_exposes_snapshot():
    """ingest_status() returns a thread-safe dict with the expected keys."""
    mod = __import__("jarvis.inbox_ingest", fromlist=["ingest_status"])
    st = mod.ingest_status()
    for key in ("active", "enabled", "inbox"):
        assert key in st


def test_process_batch_empty_dir_updates_status(tmp_path):
    """An empty/absent inbox records a cleared (done, remaining=0) status without
    ever opening a real Store — pure telemetry, no DB handle."""
    mod = __import__("jarvis.inbox_ingest", fromlist=["process_batch", "ingest_status"])
    empty = tmp_path / "no-inbox-here"
    empty.mkdir()
    res = mod.process_batch(inbox_dir=empty, batch=5, cursor_path=None)
    assert res["processed"] == 0
    assert res["done"] is True
    st = mod.ingest_status()
    assert st["active"] is True
    assert st["done"] is True
    assert st["remaining"] == 0


def test_process_batch_advances_cursor_across_calls(tmp_path, monkeypatch):
    """process_batch must drain the WHOLE backlog across calls via the persisted
    cursor (regression for the Round 7 fix 'ingester advances via cursor')."""
    from unittest.mock import patch

    from jarvis import inbox_ingest
    from jarvis.store import Store

    inbox_root = tmp_path / "inbox"
    dev = inbox_root / "dev1"
    dev.mkdir(parents=True)
    for i in range(3):
        (dev / f"n{i}.txt").write_text(
            f"distinct note number {i} about tailscale mesh", encoding="utf-8")

    def _mkstore():
        with patch("jarvis.store.chromadb.PersistentClient"):
            return Store(chroma_dir=tmp_path / "chroma", db_path=tmp_path / "meta.db")

    monkeypatch.setattr(inbox_ingest, "Store", _mkstore)
    cursor = tmp_path / "cursor.txt"

    def _persist(res):
        # the background ingester loop writes the returned cursor to disk each cycle
        if res.get("cursor"):
            cursor.write_text(res["cursor"], encoding="utf-8")
        return res

    with patch("jarvis.embed.get_embedding", lambda *a, **k: [0.1] * 8):
        r1 = _persist(inbox_ingest.process_batch(inbox_dir=inbox_root, batch=1, cooldown=0, cursor_path=cursor))
        assert r1["processed"] == 1 and r1["added"] == 1 and r1["remaining"] == 2 and r1["done"] is False
        assert cursor.exists() and cursor.read_text() != ""

        r2 = _persist(inbox_ingest.process_batch(inbox_dir=inbox_root, batch=1, cooldown=0, cursor_path=cursor))
        assert r2["processed"] == 1 and r2["remaining"] == 1

        r3 = _persist(inbox_ingest.process_batch(inbox_dir=inbox_root, batch=5, cooldown=0, cursor_path=cursor))
        assert r3["done"] is True and r3["remaining"] == 0

        # idempotent: once drained, a further call adds nothing
        r4 = _persist(inbox_ingest.process_batch(inbox_dir=inbox_root, batch=5, cooldown=0, cursor_path=cursor))
        assert r4["done"] is True and r4["remaining"] == 0

    # exactly the distinct contents landed in the store, no duplicates from re-runs
    s = _mkstore()
    try:
        n = s.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert n == 3
        dups = s.conn.execute(
            "SELECT content_hash, COUNT(*) AS c FROM memories GROUP BY content_hash HAVING c > 1"
        ).fetchone()
        assert dups is None
    finally:
        s.close()

