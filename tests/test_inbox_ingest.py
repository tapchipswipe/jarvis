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
