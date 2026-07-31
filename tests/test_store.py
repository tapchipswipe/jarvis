"""
Tests for jarvis/store.py

Covers:
  - Store init with WAL mode
  - add / search / tier weights
  - Entity table methods (get_or_create_entity, link_memory_entity, add_relationship)
  - Exists checks, stats
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.store import Store, fingerprint, TIER_WEIGHTS


# ── Init / WAL ────────────────────────────────────────────────────────────────

def test_store_init_creates_tables(store):
    """The store fixture already asserts WAL mode."""
    tables = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {t["name"] for t in tables}
    assert "memories" in table_names
    assert "sync_log" in table_names
    assert "decision_log" in table_names
    assert "entities" in table_names
    assert "memory_entities" in table_names
    assert "relationships" in table_names


def test_store_wal_mode(store):
    mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_tier_weights():
    assert TIER_WEIGHTS["raw"] == 0.3
    assert TIER_WEIGHTS["session"] == 0.6
    assert TIER_WEIGHTS["reflection"] == 1.0
    assert TIER_WEIGHTS["arc"] == 1.5


# ── fingerprint ───────────────────────────────────────────────────────────────

def test_fingerprint_deterministic():
    fp1 = fingerprint("source", "id1", "content", "2025-01-01")
    fp2 = fingerprint("source", "id1", "content", "2025-01-01")
    assert fp1 == fp2


def test_fingerprint_different_inputs():
    fp1 = fingerprint("source", "id1", "content", "2025-01-01")
    fp2 = fingerprint("source", "id2", "content", "2025-01-01")
    assert fp1 != fp2


# ── add / exists ──────────────────────────────────────────────────────────────

def test_add_memory(store):
    fid = fingerprint("test", "1", "hello world", "2025-06-01")
    emb = [0.1] * 768
    result = store.add(
        fid, "test", "1", "2025-06-01T10:00:00", "hello world",
        ["tag1"], {"key": "val"}, emb, tier="raw"
    )
    assert result is True
    assert store.exists(fid)


def test_add_memory_duplicate_ignored(store):
    fid = fingerprint("test", "1", "hello world", "2025-06-01")
    emb = [0.1] * 768
    store.add(fid, "test", "1", "2025-06-01T10:00:00", "hello world", ["tag1"], {}, emb, tier="raw")
    result = store.add(fid, "test", "1", "2025-06-01T10:00:00", "hello world", ["tag1"], {}, emb, tier="raw")
    assert result is True


def test_exists_false_for_unknown(store):
    assert store.exists("nonexistent_id_12345") is False


# ── search ────────────────────────────────────────────────────────────────────

def test_search_returns_results(store):
    fid = fingerprint("test", "1", "hello world", "2025-06-01")
    emb = [0.1] * 768
    store.add(fid, "test", "1", "2025-06-01T10:00:00", "hello world", ["tag1"], {}, emb, tier="raw")
    # Configure mock chroma to return the added memory's ID
    store.collection.query.return_value = {
        "documents": [["hello world"]],
        "ids": [[fid]],
        "metadatas": [[{"source": "test"}]],
    }
    results = store.search(emb, n_results=5)
    assert len(results) >= 1
    assert results[0]["content"] == "hello world"


def test_search_with_source_filter(store):
    emb = [0.1] * 768
    email_fid = fingerprint("email", "1", "email content", "2025-06-01")
    cal_fid = fingerprint("calendar", "1", "calendar content", "2025-06-01")
    store.add(email_fid, "email", "1", "2025-06-01T10:00:00", "email content", [], {}, emb, tier="raw")
    store.add(cal_fid, "calendar", "1", "2025-06-01T10:00:00", "calendar content", [], {}, emb, tier="raw")
    # Configure mock chroma to return both IDs
    store.collection.query.return_value = {
        "documents": [["email content", "calendar content"]],
        "ids": [[email_fid, cal_fid]],
        "metadatas": [[{"source": "email"}, {"source": "calendar"}]],
    }
    results = store.search(emb, n_results=10, source_filter="email")
    assert len(results) == 1
    assert results[0]["source"] == "email"


# ── Tier / route queries ──────────────────────────────────────────────────────

def test_get_by_tier(store):
    emb = [0.1] * 768
    store.add(fingerprint("s", "1", "content1", "2025-06-01"), "s", "1", "2025-06-01T10:00:00", "content1", [], {}, emb, tier="raw")
    store.add(fingerprint("s", "2", "content2", "2025-06-01"), "s", "2", "2025-06-01T10:00:00", "content2", [], {}, emb, tier="session")
    raw = store.get_by_tier("raw")
    session = store.get_by_tier("session")
    assert len(raw) == 1
    assert len(session) == 1


def test_get_by_route(store):
    emb = [0.1] * 768
    store.add(fingerprint("s", "1", "c1", "2025-06-01"), "s", "1", "2025-06-01T10:00:00", "c1", [], {}, emb, tier="raw", route="idea_capture")
    store.add(fingerprint("s", "2", "c2", "2025-06-01"), "s", "2", "2025-06-01T10:00:00", "c2", [], {}, emb, tier="raw", route="reference_note")
    idea = store.get_by_route("idea_capture")
    ref = store.get_by_route("reference_note")
    assert len(idea) == 1
    assert len(ref) == 1


def test_get_unclassified(store):
    emb = [0.1] * 768
    store.add(fingerprint("s", "1", "c1", "2025-06-01"), "s", "1", "2025-06-01T10:00:00", "c1", [], {}, emb, tier="raw", route="unclassified")
    store.add(fingerprint("s", "2", "c2", "2025-06-01"), "s", "2", "2025-06-01T10:00:00", "c2", [], {}, emb, tier="raw", route="idea_capture")
    unclassified = store.get_unclassified()
    assert len(unclassified) == 1


def test_get_recent_raw(store):
    from datetime import datetime
    now_ts = datetime.utcnow().isoformat()
    emb = [0.1] * 768
    store.add(fingerprint("s", "1", "c1", now_ts), "s", "1", now_ts, "c1", [], {}, emb, tier="raw")
    store.add(fingerprint("s", "2", "c2", now_ts), "s", "2", now_ts, "c2", [], {}, emb, tier="raw")
    recent = store.get_recent_raw(hours=48)
    assert len(recent) == 2


# ── Entity methods ────────────────────────────────────────────────────────────

def test_get_or_create_entity(store):
    eid = store.get_or_create_entity("john doe", entity_type="person")
    assert eid is not None
    eid2 = store.get_or_create_entity("john doe", entity_type="person")
    assert eid == eid2


def test_link_memory_entity(store):
    eid = store.get_or_create_entity("jane doe", entity_type="person")
    store.link_memory_entity("mem123", eid, confidence=0.9)
    row = store.conn.execute(
        "SELECT * FROM memory_entities WHERE memory_id = ? AND entity_id = ?",
        ("mem123", eid)
    ).fetchone()
    assert row is not None
    assert row["confidence"] == 0.9


def test_add_relationship(store):
    eid1 = store.get_or_create_entity("alice", entity_type="person")
    eid2 = store.get_or_create_entity("bob", entity_type="person")
    store.add_relationship(eid1, eid2, "co_participant", "mem1", 0.55)
    rows = store.conn.execute("SELECT * FROM relationships").fetchall()
    assert len(rows) == 1


def test_add_relationship_dedup(store):
    eid1 = store.get_or_create_entity("alice", entity_type="person")
    eid2 = store.get_or_create_entity("bob", entity_type="person")
    store.add_relationship(eid1, eid2, "co_participant", "mem1", 0.55)
    store.add_relationship(eid1, eid2, "co_participant", "mem2", 0.55)
    rows = store.conn.execute(
        "SELECT * FROM relationships WHERE source_entity = ? AND target_entity = ? AND relation_type = ?",
        (eid1, eid2, "co_participant")
    ).fetchall()
    assert len(rows) == 1


# ── mark_superseded / stats ───────────────────────────────────────────────────

def test_mark_superseded(store):
    emb = [0.1] * 768
    fid = fingerprint("s", "1", "c1", "2025-06-01")
    store.add(fid, "s", "1", "2025-06-01T10:00:00", "c1", [], {}, emb, tier="raw")
    store.mark_superseded(fid)
    row = store.conn.execute("SELECT superseded FROM memories WHERE id = ?", (fid,)).fetchone()
    assert row["superseded"] == 1


def test_stats(store):
    emb = [0.1] * 768
    store.add(fingerprint("s", "1", "c1", "2025-06-01"), "s", "1", "2025-06-01T10:00:00", "c1", [], {}, emb, tier="raw", route="unclassified")
    store.add(fingerprint("s", "2", "c2", "2025-06-01"), "s", "2", "2025-06-01T10:00:00", "c2", [], {}, emb, tier="session", route="idea_capture")
    stats = store.stats()
    assert len(stats) > 0
    total = sum(s["count"] for s in stats)
    assert total == 2
