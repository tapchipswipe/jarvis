"""Tests for jarvis/backup.py (crash-consistent store snapshots).

SQLite files are snapshotted via the online-backup API so they are valid even
while a writer holds the live DB open — no Chroma/LLM/network involved.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from jarvis.backup import snapshot_store


def _make_store(tmp_path: Path) -> Path:
    """Build a fake jarvis data root: meta.db + embed_cache.db + chroma tree."""
    root = tmp_path / "data"
    (root / "chroma" / "col-abc").mkdir(parents=True)

    con = sqlite3.connect(root / "meta.db")
    con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)")
    con.execute("INSERT INTO memories VALUES ('m1', 'hello world')")
    con.commit()
    con.close()

    con = sqlite3.connect(root / "embed_cache.db")
    con.execute("CREATE TABLE cache (k TEXT PRIMARY KEY, v BLOB)")
    con.execute("INSERT INTO cache VALUES ('k1', x'00ff')")
    con.commit()
    con.close()

    con = sqlite3.connect(root / "chroma" / "chroma.sqlite3")
    con.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY)")
    con.execute("INSERT INTO embeddings VALUES ('e1')")
    con.commit()
    con.close()

    (root / "chroma" / "col-abc" / "data_level0.bin").write_bytes(b"\x00\x01\x02")
    (root / "chroma" / "col-abc" / "index_metadata.pickle").write_bytes(b"pk")
    (root / "misc.json").write_text('{"v": 1}', encoding="utf-8")
    return root


def test_snapshot_store_mirrors_layout(tmp_path):
    root = _make_store(tmp_path)
    dst = tmp_path / "snap"
    res = snapshot_store(root, dst)

    # every SQLite file online-backed (consistent) and readable
    for rel in ("meta.db", "embed_cache.db", "chroma/chroma.sqlite3"):
        assert (dst / rel).exists(), rel
        con = sqlite3.connect(dst / rel)
        n = con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        con.close()
        assert n >= 1

    # HNSW binaries carried over best-effort
    assert (dst / "chroma" / "col-abc" / "data_level0.bin").read_bytes() == b"\x00\x01\x02"
    assert res["hnsv_copied"] == 2
    assert "meta.db" in res["sqlite_backed"]
    assert res["bytes"] > 0


def test_snapshot_is_consistent_with_live_writer(tmp_path):
    """A snapshot taken while another connection holds meta.db open and writing
    must still be a valid, readable database containing committed data."""
    root = tmp_path / "data"
    root.mkdir()
    con = sqlite3.connect(root / "meta.db")
    con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)")
    con.execute("INSERT INTO memories VALUES ('m1', 'committed-before')")
    con.commit()

    dst = tmp_path / "snap"
    res = snapshot_store(root, dst)

    # writer keeps going after the snapshot
    con.execute("INSERT INTO memories VALUES ('m2', 'committed-after')")
    con.commit()
    con.close()

    con = sqlite3.connect(dst / "meta.db")
    rows = con.execute("SELECT content FROM memories ORDER BY id").fetchall()
    con.close()
    assert [r[0] for r in rows] == ["committed-before"]
    assert res["sqlite_backed"] == ["meta.db"]


def test_snapshot_missing_files_tolerated(tmp_path):
    root = tmp_path / "data"
    root.mkdir()  # no meta.db, no chroma
    res = snapshot_store(root, tmp_path / "snap")
    assert res["sqlite_backed"] == []
    assert res["hnsv_copied"] == 0
    assert res["strict"] is False


def test_snapshot_strict_flag_recorded(tmp_path):
    root = _make_store(tmp_path)
    res = snapshot_store(root, tmp_path / "snap", strict=True)
    assert res["strict"] is True
