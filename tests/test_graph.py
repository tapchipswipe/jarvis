"""
Tests for jarvis/graph.py

Covers:
  - Entity name normalisation (title stripping, lowercasing, whitespace)
  - Fuzzy matching fallback (difflib-based)
  - upsert_entity create + update
  - resolve_entity exact + fuzzy
  - get_related, get_entity_timeline
  - infer_relationships creates edges for co-occurring entities
"""
from __future__ import annotations

import pytest

from jarvis.graph import (
    _best_match,
    _normalise,
    _similarity,
    get_entity_timeline,
    get_related,
    infer_relationships,
    resolve_entity,
    upsert_entity,
)

# ── Normalisation ─────────────────────────────────────────────────────────────

def test_normalise_strips_titles():
    assert _normalise("Dr. Smith") == "smith"
    assert _normalise("Mr. Jones") == "jones"
    assert _normalise("Ms. Jane Doe") == "jane doe"
    assert _normalise("MRS. Smith") == "smith"


def test_normalise_lowercases():
    assert _normalise("JOHN") == "john"


def test_normalise_strips_whitespace():
    assert _normalise("  John   Doe  ") == "john doe"


def test_normalise_empty_string():
    assert _normalise("") == ""


# ── Similarity ─────────────────────────────────────────────────────────────────

def test_similarity_identical():
    assert _similarity("john", "john") == 1.0


def test_similarity_different():
    assert _similarity("john", "mary") < 0.5


def test_similarity_case_insensitive():
    assert _similarity("John", "JOHN") == 1.0


# ── Best match ─────────────────────────────────────────────────────────────────

def test_best_match_returns_none_for_empty():
    assert _best_match("john", []) is None


def test_best_match_returns_none_for_low_similarity():
    candidates = [{"id": "1", "canonical_name": "mary jane"}]
    result = _best_match("john", candidates)
    assert result is None


def test_best_match_returns_high_similarity():
    candidates = [{"id": "1", "canonical_name": "john smith"}]
    result = _best_match("John Smith", candidates)
    assert result == "1"


# ── upsert_entity ─────────────────────────────────────────────────────────────

def test_upsert_entity_creates_new(store):
    eid = upsert_entity(store, "John Doe", entity_type="person")
    assert eid is not None
    assert len(eid) == 24


def test_upsert_entity_returns_same_id_for_duplicate(store):
    eid1 = upsert_entity(store, "John Doe", entity_type="person")
    eid2 = upsert_entity(store, "John Doe", entity_type="person")
    assert eid1 == eid2


def test_upsert_entity_normalises_name(store):
    eid1 = upsert_entity(store, "Dr. John Doe", entity_type="person")
    eid2 = upsert_entity(store, "john doe", entity_type="person")
    assert eid1 == eid2


def test_upsert_entity_empty_name_returns_none(store):
    assert upsert_entity(store, "", entity_type="person") is None
    assert upsert_entity(store, "   ", entity_type="person") is None


def test_upsert_entity_with_memory_id_links(store):
    eid = upsert_entity(store, "Test Person", memory_id="mem123")
    assert eid is not None
    row = store.conn.execute(
        "SELECT * FROM memory_entities WHERE memory_id = ? AND entity_id = ?",
        ("mem123", eid)
    ).fetchone()
    assert row is not None


# ── resolve_entity ────────────────────────────────────────────────────────────

def test_resolve_entity_exact_match(store):
    eid = upsert_entity(store, "John Doe", entity_type="person")
    result = resolve_entity(store, "John Doe")
    assert result == eid


def test_resolve_entity_normalised_match(store):
    eid = upsert_entity(store, "John Doe", entity_type="person")
    result = resolve_entity(store, "dr. john doe")
    assert result == eid


def test_resolve_entity_fuzzy_match(store):
    eid = upsert_entity(store, "John Smith", entity_type="person")
    result = resolve_entity(store, "Jon Smith")
    assert result == eid


def test_resolve_entity_no_match(store):
    result = resolve_entity(store, "Nonexistent Person")
    assert result is None


def test_resolve_entity_empty_name(store):
    assert resolve_entity(store, "") is None
    assert resolve_entity(store, "   ") is None


# ── get_related ───────────────────────────────────────────────────────────────

def test_get_related_no_relationships(store):
    eid = upsert_entity(store, "John Doe", entity_type="person")
    result = get_related(store, eid)
    assert result == []


def test_get_related_returns_related(store):
    eid1 = upsert_entity(store, "John Doe", entity_type="person")
    eid2 = upsert_entity(store, "Jane Smith", entity_type="person")
    store.add_relationship(eid1, eid2, "co_participant", "mem1", 0.55)
    result = get_related(store, eid1)
    assert len(result) == 1
    assert result[0]["entity_name"] == "jane smith"


def test_get_related_depth_honored(store):
    """depth=1 returns only direct neighbors; higher depths walk multiple hops."""
    a = upsert_entity(store, "Alice", entity_type="person")
    b = upsert_entity(store, "Bob", entity_type="person")
    c = upsert_entity(store, "Carol", entity_type="person")
    store.add_relationship(a, b, "co_participant", "mem1", 0.55)
    store.add_relationship(b, c, "co_participant", "mem2", 0.55)

    # depth=1: only direct neighbor (unchanged behaviour)
    direct = get_related(store, a, depth=1)
    assert {r["entity_id"] for r in direct} == {b}

    # depth=2: direct + 2-hop neighbor Carol via Bob
    two = get_related(store, a, depth=2)
    assert {r["entity_id"] for r in two} == {b, c}

    # depth=3: same reachable set (nothing further), no duplicates
    three = get_related(store, a, depth=3)
    assert {r["entity_id"] for r in three} == {b, c}
    assert len(three) == 2


def test_get_related_depth_zero_returns_empty(store):
    a = upsert_entity(store, "Alice", entity_type="person")
    b = upsert_entity(store, "Bob", entity_type="person")
    store.add_relationship(a, b, "co_participant", "mem1", 0.55)
    assert get_related(store, a, depth=0) == []
    assert get_related(store, a, depth=-1) == []


# ── get_entity_timeline ───────────────────────────────────────────────────────

def test_get_entity_timeline_empty(store):
    eid = upsert_entity(store, "John Doe", entity_type="person")
    result = get_entity_timeline(store, eid)
    assert result == []


def test_get_entity_timeline_with_memories(store):
    eid = upsert_entity(store, "John Doe", entity_type="person")
    from jarvis.store import fingerprint
    fid = fingerprint("test", "1", "John Doe was here", "2025-01-01")
    store.conn.execute(
        "INSERT INTO memories (id, source, source_id, timestamp, content, content_hash, tags, metadata, tier, weight, route, expires_at, consolidated_from, superseded, embedded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fid, "test", "1", "2025-01-01", "John Doe was here", "hash", "[]", "{}", "raw", 0.3, "unclassified", None, None, 0, "2025-01-01")
    )
    store.link_memory_entity(fid, eid)
    result = get_entity_timeline(store, eid)
    assert len(result) == 1
    assert result[0]["content"] == "John Doe was here"


# ── infer_relationships ───────────────────────────────────────────────────────

def test_infer_relationships_creates_edges(store):
    from datetime import datetime, timezone
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    eid1 = upsert_entity(store, "Alice", entity_type="person")
    eid2 = upsert_entity(store, "Bob", entity_type="person")
    from jarvis.store import fingerprint
    fid = fingerprint("test", "1", "Alice and Bob met", now_ts)
    store.conn.execute(
        "INSERT INTO memories (id, source, source_id, timestamp, content, content_hash, tags, metadata, tier, weight, route, expires_at, consolidated_from, superseded, embedded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fid, "test", "1", now_ts, "Alice and Bob met", "hash", "[]", "{}", "raw", 0.3, "unclassified", None, None, 0, now_ts)
    )
    store.link_memory_entity(fid, eid1)
    store.link_memory_entity(fid, eid2)

    infer_relationships(store, limit_hours=24, max_memories=500)

    rows = store.conn.execute("SELECT * FROM relationships").fetchall()
    assert len(rows) >= 1


def test_infer_relationships_collapses_reversed_pair(store):
    """A co-occurring pair must yield exactly ONE canonical relationship row.

    entity_ids aren't ordered, so the same pair can appear reversed across
    memories (A->B in one, B->A in another). add_relationship must normalize
    undirected (co_participant) edges so those collapse instead of creating a
    reversed duplicate, while get_related still resolves both directions.
    """
    from datetime import datetime, timezone
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    eid1 = upsert_entity(store, "Alice", entity_type="person")
    eid2 = upsert_entity(store, "Bob", entity_type="person")
    from jarvis.store import fingerprint
    # Two memories, same co-occurring pair stored in reversed order.
    for idx, (first, second) in enumerate([(eid1, eid2), (eid2, eid1)]):
        fid = fingerprint("test", str(idx), f"meeting {idx}", now_ts)
        store.conn.execute(
            "INSERT INTO memories (id, source, source_id, timestamp, content, content_hash, tags, metadata, tier, weight, route, expires_at, consolidated_from, superseded, embedded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fid, "test", str(idx), now_ts, f"meeting {idx}", "hash", "[]", "{}", "raw", 0.3, "unclassified", None, None, 0, now_ts)
        )
        store.link_memory_entity(fid, first)
        store.link_memory_entity(fid, second)

    infer_relationships(store, limit_hours=24, max_memories=500)

    rows = store.conn.execute(
        "SELECT * FROM relationships WHERE relation_type = 'co_participant'"
    ).fetchall()
    # No reversed duplicate: exactly one canonical row for the unordered pair.
    assert len(rows) == 1

    # get_related still returns the neighbor from both directions.
    related1 = get_related(store, eid1)
    assert len(related1) == 1
    assert related1[0]["entity_name"] == "bob"
    related2 = get_related(store, eid2)
    assert len(related2) == 1
    assert related2[0]["entity_name"] == "alice"


def test_infer_relationships_skips_person_domain_org_edge(store):
    """No co_participant edge when an endpoint is a domain/org entity.

    A person attending the same memory as a domain/org isn't a meaningful
    participant link.  person<->person edges are still created.
    """
    from datetime import datetime, timezone
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    alice = upsert_entity(store, "Alice", entity_type="person")
    bob = upsert_entity(store, "Bob", entity_type="person")
    org = upsert_entity(store, "Acme Corp", entity_type="organization")
    domain = upsert_entity(store, "example.com", entity_type="domain")
    from jarvis.store import fingerprint
    fid = fingerprint("test", "1", "Alice and Bob use example.com at Acme", now_ts)
    store.conn.execute(
        "INSERT INTO memories (id, source, source_id, timestamp, content, content_hash, tags, metadata, tier, weight, route, expires_at, consolidated_from, superseded, embedded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fid, "test", "1", now_ts, "Alice and Bob use example.com at Acme", "hash", "[]", "{}", "raw", 0.3, "unclassified", None, None, 0, now_ts)
    )
    for eid in (alice, bob, org, domain):
        store.link_memory_entity(fid, eid)

    infer_relationships(store, limit_hours=24, max_memories=500)

    rows = store.conn.execute("SELECT * FROM relationships").fetchall()
    # Only the person<->person pair becomes a co_participant edge; any pair
    # touching a domain/org is suppressed (4 entities -> 6 pairs, 5 suppressed).
    assert len(rows) == 1
    assert rows[0]["relation_type"] == "co_participant"
    assert rows[0]["source_entity"] in (alice, bob)
    assert rows[0]["target_entity"] in (alice, bob)


# ── Knowledge Graph HTTP API (dashboard endpoints) ─────────────────────────────


@pytest.fixture()
def api_client(tmp_path, monkeypatch, mock_chroma_client):
    """TestClient for the dashboard app backed by a temp Store per request.

    Mirrors the real dashboard: every API request opens a fresh Store on the
    same SQLite file (and closes it after the response), so endpoints that
    call store.close() in a finally block are exercised exactly as in prod.
    """
    from fastapi.testclient import TestClient

    from jarvis import dashboard
    from jarvis.store import Store

    chroma_dir = tmp_path / "chroma"
    db_path = tmp_path / "meta.db"
    chroma_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "jarvis.store.chromadb.PersistentClient",
        lambda *a, **k: mock_chroma_client,
    )
    monkeypatch.setattr(
        dashboard, "_get_store",
        lambda: Store(chroma_dir=chroma_dir, db_path=db_path),
    )

    class _Api:
        client = TestClient(dashboard.app)

        def store(self):
            return Store(chroma_dir=chroma_dir, db_path=db_path)

    yield _Api()


def _seed_graph(api):
    store = api.store()
    try:
        e1 = upsert_entity(store, "John Doe", entity_type="person")
        e2 = upsert_entity(store, "Jane Smith", entity_type="person")
        e3 = upsert_entity(store, "Acme Corp", entity_type="organization")
        store.add_relationship(e1, e2, "co_participant", "mem1", 0.55)
        store.add_relationship(e1, e3, "works_at", "mem2", 0.9)
        return e1, e2, e3
    finally:
        store.close()


def test_api_entities_lists_all(api_client):
    _seed_graph(api_client)
    resp = api_client.client.get("/api/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    names = {e["canonical_name"] for e in body["entities"]}
    assert names == {"john doe", "jane smith", "acme corp"}


def test_api_entities_search_q(api_client):
    _seed_graph(api_client)
    resp = api_client.client.get("/api/entities", params={"q": "jane"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["entities"][0]["canonical_name"] == "jane smith"


def test_api_entities_search_type(api_client):
    _seed_graph(api_client)
    resp = api_client.client.get("/api/entities", params={"type": "person"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert all(e["entity_type"] == "person" for e in body["entities"])


def test_api_entities_limit(api_client):
    _seed_graph(api_client)
    resp = api_client.client.get("/api/entities", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2


def test_api_entities_fuzzy_search_resolves(api_client):
    _seed_graph(api_client)
    # "Jon Doe" doesn't substring-match, but fuzzy resolution surfaces "john doe".
    resp = api_client.client.get("/api/entities", params={"q": "Jon Doe"})
    assert resp.status_code == 200
    body = resp.json()
    assert any(e["canonical_name"] == "john doe" for e in body["entities"])


def test_api_entities_empty(api_client):
    resp = api_client.client.get("/api/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"entities": [], "count": 0}


def test_api_entity_relationships(api_client):
    e1, _e2, _e3 = _seed_graph(api_client)
    resp = api_client.client.get(f"/api/entities/{e1}/relationships")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity_id"] == e1
    assert body["count"] == 2
    assert {r["entity_name"] for r in body["relationships"]} == {"jane smith", "acme corp"}
    assert {r["relation"] for r in body["relationships"]} == {"co_participant", "works_at"}


def test_api_entity_relationships_none(api_client):
    store = api_client.store()
    try:
        e1 = upsert_entity(store, "Isolated Entity", entity_type="person")
    finally:
        store.close()
    resp = api_client.client.get(f"/api/entities/{e1}/relationships")
    assert resp.status_code == 200
    body = resp.json()
    assert body["relationships"] == []
    assert body["count"] == 0


def test_api_entity_relationships_not_found(api_client):
    resp = api_client.client.get("/api/entities/does-not-exist/relationships")
    assert resp.status_code == 404
    assert resp.json()["error"] == "entity not found"

