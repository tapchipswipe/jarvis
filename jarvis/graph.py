"""
jarvis/graph.py — Entity resolution and relationship inference for the
Jarvis knowledge graph.

Public API:
    resolve_entity(name)          -> entity_id | None
    get_related(entity_id, depth) -> list[dict]
    get_entity_timeline(entity_id)-> list[dict]
    infer_relationships(store)    -> None
    upsert_entity(name, entity_type, memory_id) -> entity_id
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"^(mr|mrs|ms|dr)\.?\s+", re.IGNORECASE)


def _normalise(name: str) -> str:
    n = name.strip()
    n = _TITLE_RE.sub("", n)
    n = " ".join(n.split())
    return n.lower()


# ---------------------------------------------------------------------------
# Fuzzy matching (pure-Python fallback when thefuzz / fuzzywuzzy absent)
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalise(a), _normalise(b)).ratio()


def _best_match(name: str, candidates: list[dict]) -> str | None:
    """Return the entity_id of the closest candidate, or None.

    Candidates is a list of dicts with at least `id` and `canonical_name`.
    """
    if not candidates:
        return None
    best_id = None
    best_score = 0.0
    nn = _normalise(name)
    target = nn  # compare on normalised names directly
    for c in candidates:
        cn = _normalise(c["canonical_name"]) if c.get("canonical_name") else ""
        score = SequenceMatcher(None, target, cn).ratio()
        if score > best_score:
            best_score = score
            best_id = c["id"]
    if best_score >= 0.78:
        return best_id
    return None


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD = 0.78  # minimum similarity for a merge


def upsert_entity(store, name: str, entity_type: str = "person", memory_id: str | None = None) -> str | None:
    """Create or update an entity, returning its id."""
    canonical = _normalise(name)
    if not canonical:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    row = store.conn.execute(
        "SELECT id, source_count FROM entities WHERE canonical_name = ?",
        (canonical,)
    ).fetchone()
    if row:
        eid = row["id"]
        store.conn.execute(
            "UPDATE entities SET source_count = source_count + 1, last_seen = ? WHERE id = ?",
            (now, eid)
        )
        store.conn.commit()
    else:
        eid = hashlib.sha256(canonical.encode()).hexdigest()[:24]
        store.conn.execute(
            "INSERT INTO entities (id, canonical_name, entity_type, source_count, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, canonical, entity_type, 1, now, now)
        )
        store.conn.commit()
    if memory_id and eid:
        store.link_memory_entity(memory_id, eid)
    return eid


def resolve_entity(store, name: str) -> str | None:
    """Normalise *name* and return the matching entity_id, or None."""
    canonical = _normalise(name)
    if not canonical:
        return None
    # 1) exact match
    row = store.conn.execute(
        "SELECT id, canonical_name FROM entities WHERE canonical_name = ?",
        (canonical,)
    ).fetchone()
    if row:
        return row["id"]
    # 2) fuzzy match against all names (for small tables, full scan is fine)
    all_rows = store.conn.execute(
        "SELECT id, canonical_name FROM entities"
    ).fetchall()
    candidates = [dict(r) for r in all_rows]
    matched = _best_match(name, candidates)
    if matched:
        logger.debug("Fuzzy-matched '%s' -> %s", name, matched)
    return matched


def get_related(store, entity_id: str, depth: int = 1) -> list[dict]:
    """Return entities connected to *entity_id* within *depth* hops.

    A BFS over the relationships table, walking up to *depth* hops so
    multi-hop recall works.  ``depth=1`` returns exactly the direct
    neighbors (unchanged behaviour).  Returns entities reachable in
    ``1..depth`` hops, de-duplicated across levels.
    """
    if depth < 1:
        return []
    frontier = [entity_id]
    visited = {entity_id}
    results: list[dict] = []
    for _ in range(depth):
        next_frontier: list[str] = []
        for node in frontier:
            rows = store.conn.execute(
                """
                SELECT r.relation_type, r.confidence, r.created_at,
                       e1.canonical_name AS source_name,
                       e2.canonical_name AS target_name,
                       e2.entity_type AS target_type,
                       e2.id AS target_id
                FROM relationships r
                JOIN entities e1 ON r.source_entity = e1.id
                JOIN entities e2 ON r.target_entity = e2.id
                WHERE r.source_entity = ?
                UNION ALL
                SELECT r.relation_type, r.confidence, r.created_at,
                       e2.canonical_name AS source_name,
                       e1.canonical_name AS target_name,
                       e1.entity_type AS target_type,
                       e1.id AS target_id
                FROM relationships r
                JOIN entities e1 ON r.source_entity = e1.id
                JOIN entities e2 ON r.target_entity = e2.id
                WHERE r.target_entity = ? AND r.source_entity != ?
                """,
                (node, node, node)
            ).fetchall()
            for r in rows:
                tid = r["target_id"]
                if tid in visited:
                    continue
                visited.add(tid)
                next_frontier.append(tid)
                results.append({
                    "relation": r["relation_type"],
                    "confidence": r["confidence"],
                    "created_at": r["created_at"],
                    "entity_id": tid,
                    "entity_name": r["target_name"],
                    "entity_type": r["target_type"],
                })
        frontier = next_frontier
        if not frontier:
            break
    return results


def get_entity_timeline(store, entity_id: str) -> list[dict]:
    """All memories mentioning *entity_id*, ordered by timestamp desc"""
    rows = store.conn.execute(
        """
        SELECT m.id, m.timestamp, m.source, m.content, m.tier, m.route,
               me.confidence
        FROM memories m
        JOIN memory_entities me ON m.id = me.memory_id
        WHERE me.entity_id = ? AND m.superseded = 0
        ORDER BY m.timestamp DESC
        """,
        (entity_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Relationship heuristics (lightweight inline / nightly batch)
# ---------------------------------------------------------------------------

_RELATION_HEURISTICS = [
    # (relation_type, lambda that scans two entities co-occurring in one memory)
    ("co_participant", lambda ents, src: True),  # any list of >= 2 entities
]

# Entity types that are NOT meaningful "participants".  A person attending the
# same memory as a domain / org isn't a participant link — it's just co-venue
# noise — so we skip co_participant edges touching these.  (covers the
# extract_entities vocabulary: "domain", "org", plus the "organization" alias)
_NON_PARTICIPANT_TYPES = frozenset({"domain", "org", "organization"})


def _extract_entities_from_memory(store, memory_id: str) -> list[str]:
    rows = store.conn.execute(
        "SELECT entity_id FROM memory_entities WHERE memory_id = ?",
        (memory_id,)
    ).fetchall()
    return [r["entity_id"] for r in rows]


def infer_relationships(store, limit_hours: int = 24, max_memories: int = 500) -> None:
    """Scan recent memories and create relationship edges based on heuristics.

    *Inline* call — meant to run on ingestion (lightweight) or on a nightly
    cron (full sweep).  We process at most *max_memories* recent raw memories.
    """
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=limit_hours)).isoformat()
    rows = store.conn.execute(
        "SELECT id FROM memories WHERE tier = 'raw' AND timestamp >= ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?",
        (cutoff, max_memories)
    ).fetchall()
    created = 0
    for r in rows:
        mid = r["id"]
        entity_ids = _extract_entities_from_memory(store, mid)
        if len(entity_ids) < 2:
            continue
        # Resolve entity types once so we can suppress noisy person<->domain/org
        # co_participant edges (a person attending the same memory as a domain
        # isn't a meaningful "participant" link).
        placeholders = ",".join("?" * len(entity_ids))
        type_rows = store.conn.execute(
            f"SELECT id, entity_type FROM entities WHERE id IN ({placeholders})",
            entity_ids,
        ).fetchall()
        types = {tr["id"]: tr["entity_type"] for tr in type_rows}
        # For each pair, create a co_participant edge
        for i in range(len(entity_ids)):
            for j in range(i + 1, len(entity_ids)):
                if (
                    types.get(entity_ids[i]) in _NON_PARTICIPANT_TYPES
                    or types.get(entity_ids[j]) in _NON_PARTICIPANT_TYPES
                ):
                    continue
                store.add_relationship(
                    source_entity=entity_ids[i],
                    target_entity=entity_ids[j],
                    relation_type="co_participant",
                    source_memory_id=mid,
                    confidence=0.55,
                )
                # Avoid double-add without unique constraint on (s,t,type); store.add_relationship handles dedup
                created += 1
    if created:
        logger.info("infer_relationships: created %d relationship edges", created)

