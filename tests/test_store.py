"""
Tests for jarvis/store.py

Covers:
  - Store init with WAL mode
  - add / search / tier weights
  - Entity table methods (get_or_create_entity, link_memory_entity, add_relationship)
  - Exists checks, stats
"""
from __future__ import annotations

from datetime import datetime, timezone

from jarvis.store import TIER_WEIGHTS, fingerprint

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


def test_add_with_failed_embedding_leaves_unembedded(store):
    """store.add must not write a degenerate embedding to Chroma nor set
    embedded_at when the embedding failed (None), so reindex_missing can retry."""
    fid = fingerprint("test", "1", "hello world", "2025-06-01")
    store.collection.add.reset_mock()
    result = store.add(
        fid, "test", "1", "2025-06-01T10:00:00", "hello world",
        ["tag1"], {}, None, tier="raw"
    )
    assert result is True
    assert store.exists(fid)
    # No vector written to Chroma.
    store.collection.add.assert_not_called()
    # embedded_at stays NULL -> get_unembedded() returns it for a retry.
    row = store.conn.execute("SELECT embedded_at FROM memories WHERE id = ?", (fid,)).fetchone()
    assert row["embedded_at"] is None
    assert [m["id"] for m in store.get_unembedded()] == [fid]


def test_add_with_empty_embedding_leaves_unembedded(store):
    """An empty embedding is also treated as a failure (no vector, no embedded_at)."""
    fid = fingerprint("test", "1", "hello world", "2025-06-01")
    store.collection.add.reset_mock()
    store.add(fid, "test", "1", "2025-06-01T10:00:00", "hello world", ["tag1"], {}, [], tier="raw")
    store.collection.add.assert_not_called()
    row = store.conn.execute("SELECT embedded_at FROM memories WHERE id = ?", (fid,)).fetchone()
    assert row["embedded_at"] is None


def test_add_with_valid_embedding_sets_embedded_at(store):
    """A successful embedding is written to Chroma and embedded_at is set."""
    fid = fingerprint("test", "1", "hello world", "2025-06-01")
    emb = [0.1] * 768
    store.collection.add.reset_mock()
    store.add(fid, "test", "1", "2025-06-01T10:00:00", "hello world", ["tag1"], {}, emb, tier="raw")
    store.collection.add.assert_called_once()
    row = store.conn.execute("SELECT embedded_at FROM memories WHERE id = ?", (fid,)).fetchone()
    assert row["embedded_at"] is not None
    assert store.get_unembedded() == []


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


def test_search_re_rank_keeps_similarity_within_tier(store):
    """Re-rank must order by (tier weight desc, similarity desc) so that within
    an equal-weight tier the most relevant (nearest) memory surfaces first
    instead of being dropped by an arbitrary order."""
    emb = [0.1] * 3
    arc_fid = fingerprint("s", "arc", "the arc memory", "2025-06-01")
    raw1_fid = fingerprint("s", "raw1", "close raw memory", "2025-06-01")
    raw2_fid = fingerprint("s", "raw2", "distant raw memory", "2025-06-01")
    store.add(arc_fid, "s", "arc", "2025-06-01T10:00:00", "the arc memory", [], {}, emb, tier="arc")
    store.add(raw1_fid, "s", "raw1", "2025-06-01T10:00:00", "close raw memory", [], {}, emb, tier="raw")
    store.add(raw2_fid, "s", "raw2", "2025-06-01T10:00:00", "distant raw memory", [], {}, emb, tier="raw")
    # Chroma returns results in vector order (most similar first) along with
    # distances. arc carries the highest tier weight; among the two raw
    # memories raw1 is closer (more similar) than raw2.
    store.collection.query.return_value = {
        "documents": [["the arc memory", "close raw memory", "distant raw memory"]],
        "ids": [[arc_fid, raw1_fid, raw2_fid]],
        "metadatas": [[{"source": "s"}, {"source": "s"}, {"source": "s"}]],
        "distances": [[0.05, 0.10, 0.30]],
    }
    results = store.search(emb, n_results=3)
    ids = [r["id"] for r in results]
    # Highest tier weight surfaces first.
    assert ids[0] == arc_fid
    # Within the raw tier (equal weight), similarity order is preserved:
    # the closer memory must outrank the distant one, not an arbitrary order.
    assert ids[1] == raw1_fid
    assert ids[2] == raw2_fid


def test_search_re_rank_tiebreak_falls_back_to_vector_order(store):
    """When distances are unavailable, re-rank still keeps Chroma's returned
    order as the tie-break for equal-weight rows (stable sort)."""
    emb = [0.1] * 3
    raw_a = fingerprint("s", "a", "memory a", "2025-06-01")
    raw_b = fingerprint("s", "b", "memory b", "2025-06-01")
    store.add(raw_a, "s", "a", "2025-06-01T10:00:00", "memory a", [], {}, emb, tier="raw")
    store.add(raw_b, "s", "b", "2025-06-01T10:00:00", "memory b", [], {}, emb, tier="raw")
    # No "distances" key in the query result (older mocks / some backends).
    store.collection.query.return_value = {
        "documents": [["memory a", "memory b"]],
        "ids": [[raw_a, raw_b]],
        "metadatas": [[{"source": "s"}, {"source": "s"}]],
    }
    results = store.search(emb, n_results=2)
    ids = [r["id"] for r in results]
    assert ids == [raw_a, raw_b]


def test_search_recency_tiebreak_newest_wins(store):
    """task_0039: among memories of equal weight and equal similarity (same
    distance), the more recent one must rank first — 'what did I do this
    morning' should surface the fresh memory over an identical-quality old one."""
    emb = [0.1] * 3
    old_fid = fingerprint("s", "old", "same topic memory one", "2025-01-01")
    new_fid = fingerprint("s", "new", "same topic memory two", "2025-06-01")
    old_ts = "2025-01-01T10:00:00"
    new_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    store.add(old_fid, "s", "old", old_ts, "same topic memory one", [], {}, emb, tier="raw")
    store.add(new_fid, "s", "new", new_ts, "same topic memory two", [], {}, emb, tier="raw")
    # Chroma returns old first, both with identical weight & distance so only
    # recency can break the tie.
    store.collection.query.return_value = {
        "documents": [["same topic memory one", "same topic memory two"]],
        "ids": [[old_fid, new_fid]],
        "metadatas": [[{"source": "s"}, {"source": "s"}]],
        "distances": [[0.5, 0.5]],
    }
    results = store.search(emb, n_results=2)
    ids = [r["id"] for r in results]
    assert ids == [new_fid, old_fid]


def test_search_recency_boost_does_not_override_relevance(store):
    """task_0039: recency is only a tertiary tiebreak — a highly-relevant older
    memory must still outrank a barely-relevant newer one."""
    emb = [0.1] * 3
    old_relevant = fingerprint("s", "old-rel", "highly relevant", "2025-01-01")
    new_irrelevant = fingerprint("s", "new-irr", "barely relevant", "2025-06-01")
    old_ts = "2025-01-01T10:00:00"
    new_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    # reflection tier carries weight 1.0, raw tier 0.3.
    store.add(old_relevant, "s", "old-rel", old_ts, "highly relevant", [], {}, emb, tier="reflection")
    store.add(new_irrelevant, "s", "new-irr", new_ts, "barely relevant", [], {}, emb, tier="raw")
    store.collection.query.return_value = {
        "documents": [["highly relevant", "barely relevant"]],
        "ids": [[old_relevant, new_irrelevant]],
        "metadatas": [[{"source": "s"}, {"source": "s"}]],
        "distances": [[0.5, 0.5]],
    }
    results = store.search(emb, n_results=2)
    ids = [r["id"] for r in results]
    assert ids == [old_relevant, new_irrelevant]


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
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
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


def test_mark_superseded_deletes_vector_from_chroma(store):
    """Superseding a memory must also prune its vector from Chroma so stale
    vectors stop crowding the search pre-filter and Chroma stops growing."""
    emb = [0.1] * 768
    fid = fingerprint("s", "1", "c1", "2025-06-01")
    store.add(fid, "s", "1", "2025-06-01T10:00:00", "c1", [], {}, emb, tier="raw")
    store.collection.delete.reset_mock()
    store.mark_superseded(fid)
    row = store.conn.execute("SELECT superseded FROM memories WHERE id = ?", (fid,)).fetchone()
    assert row["superseded"] == 1
    # The vector is removed by the same id used at insert.
    store.collection.delete.assert_called_once_with(ids=[fid])


def test_mark_superseded_best_effort_chroma_delete(store):
    """A failing Chroma delete must never break supersede (SQLite is
    authoritative and the commit already happened)."""
    emb = [0.1] * 768
    fid = fingerprint("s", "1", "c1", "2025-06-01")
    store.add(fid, "s", "1", "2025-06-01T10:00:00", "c1", [], {}, emb, tier="raw")
    store.collection.delete.side_effect = Exception("chroma unavailable")
    store.mark_superseded(fid)  # must not raise
    row = store.conn.execute("SELECT superseded FROM memories WHERE id = ?", (fid,)).fetchone()
    assert row["superseded"] == 1


def test_expire_old_deletes_vectors_from_chroma(store):
    """Expiring a memory must also prune its vector(s) from Chroma."""
    emb = [0.1] * 768
    fid = fingerprint("s", "1", "expiring content", "2025-06-01")
    store.add(
        fid, "s", "1", "2025-06-01T10:00:00", "expiring content", [], {}, emb,
        tier="raw", expires_at="2000-01-01T00:00:00",
    )
    assert store.exists(fid)
    store.collection.delete.reset_mock()
    store._expire_old()
    # SQLite row is gone.
    assert not store.exists(fid)
    # Its vector was pruned by the same id used at insert.
    store.collection.delete.assert_called_once_with(ids=[fid])


def test_expire_old_with_nothing_to_expire_skips_chroma_delete(store):
    """No expired rows -> no Chroma delete call."""
    emb = [0.1] * 768
    store.add(fingerprint("s", "1", "c1", "2025-06-01"), "s", "1",
              "2025-06-01T10:00:00", "c1", [], {}, emb, tier="raw")
    store.collection.delete.reset_mock()
    store._expire_old()
    store.collection.delete.assert_not_called()


def test_stats(store):
    emb = [0.1] * 768
    store.add(fingerprint("s", "1", "c1", "2025-06-01"), "s", "1", "2025-06-01T10:00:00", "c1", [], {}, emb, tier="raw", route="unclassified")
    store.add(fingerprint("s", "2", "c2", "2025-06-01"), "s", "2", "2025-06-01T10:00:00", "c2", [], {}, emb, tier="session", route="idea_capture")
    stats = store.stats()
    assert len(stats) > 0
    total = sum(s["count"] for s in stats)
    assert total == 2
# ── Incremental re-indexing (D1) ─────────────────────────────────────────────

def test_get_unembedded_is_empty_by_default(store):
    emb = [0.1] * 768
    store.add(fingerprint("s", "1", "hello", "2025-06-01"), "s", "1", "2025-06-01T10:00:00", "hello", [], {}, emb)
    assert store.get_unembedded() == []


def test_get_unembedded_finds_null_rows(store):
    # Simulate a row inserted without an embedding (embedded_at NULL)
    store.conn.execute(
        "INSERT INTO memories (id, source, source_id, timestamp, content, content_hash, tags, metadata, tier, weight, route, superseded, embedded_at)"
        " VALUES ('missing-emb', 'import', 'x', '2025-06-01T10:00:00', 'imported note', 'h', '[]', '{}', 'raw', 0.3, 'unclassified', 0, NULL)"
    )
    store.conn.commit()
    rows = store.get_unembedded()
    assert len(rows) == 1
    assert rows[0]["id"] == "missing-emb"


def test_mark_embedded_sets_timestamp(store):
    store.conn.execute(
        "INSERT INTO memories (id, source, source_id, timestamp, content, content_hash, tags, metadata, tier, weight, route, superseded, embedded_at)"
        " VALUES ('m1', 'import', 'x', '2025-06-01T10:00:00', 'note', 'h', '[]', '{}', 'raw', 0.3, 'unclassified', 0, NULL)"
    )
    store.conn.commit()
    store.mark_embedded("m1")
    row = store.conn.execute("SELECT embedded_at FROM memories WHERE id = 'm1'").fetchone()
    assert row["embedded_at"] is not None


# ── Tier promotion (D2) ──────────────────────────────────────────────────────

def test_promote_raw_to_session(store):
    emb = [0.1] * 768
    old = "2025-01-01T10:00:00"   # well over 7 days ago
    new = datetime.now(timezone.utc).isoformat()
    store.add(fingerprint("s", "old", "old content", "2025-01-01"), "s", "old", old, "old content", [], {}, emb, tier="raw")
    store.add(fingerprint("s", "new", "new content", new), "s", "new", new, "new content", [], {}, emb, tier="raw")
    promoted = store.promote_raw_to_session(days=7)
    assert promoted >= 1
    old_row = store.conn.execute("SELECT tier, weight FROM memories WHERE source_id = 'old'").fetchone()
    new_row = store.conn.execute("SELECT tier, weight FROM memories WHERE source_id = 'new'").fetchone()
    assert old_row["tier"] == "session"
    assert old_row["weight"] == 0.6
    assert new_row["tier"] == "raw"  # too fresh to promote


def test_promote_skips_superseded_and_non_raw(store):
    emb = [0.1] * 768
    old = "2025-01-01T10:00:00"
    # superseded raw memory
    fid = fingerprint("s", "gone", "gone content", "2025-01-01")
    store.add(fid, "s", "gone", old, "gone content", [], {}, emb, tier="raw")
    store.mark_superseded(fid)
    promoted = store.promote_raw_to_session(days=7)
    assert promoted == 0


def test_lookup_entities_returns_mapping(store):
    eid = store.get_or_create_entity("Alice Smith", entity_type="person")
    store.add("memX", "manual", "1", "2026-01-01T10:00:00", "met alice", [], {}, [0.1] * 8)
    store.link_memory_entity("memX", eid)
    links = store.lookup_entities(["memX", "unknown-id"])
    assert "memX" in links
    names = [e["name"] for e in links["memX"]]
    assert "Alice Smith" in names
    assert links.get("unknown-id") is None

def test_lookup_entities_empty(store):
    assert store.lookup_entities([]) == {}
