import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import chromadb
from chromadb.config import Settings

from jarvis.paths import data_dir, ensure_private_dir

DEFAULT_CHROMA_DIR = data_dir("data", "chroma")
DEFAULT_DB_PATH = data_dir("data", "meta.db")

TIER_WEIGHTS = {
    "raw": 0.3,
    "session": 0.6,
    "reflection": 1.0,
    "arc": 1.5,
}

ROUTE_TAG_MAP = {
    "idea_capture": ["idea"],
    "reference_note": ["reference"],
    "context_list_update": ["action"],
    "escalate": ["escalated"],
    "unclassified": [],
}

# Relation types where the edge is symmetric (undirected). For these we store a
# single canonical row per unordered pair: the lexicographically-smaller entity
# id is always persisted as `source_entity` so reversed adds collapse instead of
# creating duplicate A->B / B->A rows that inflate edge counts.
UNDIRECTED_RELATIONS = {"co_participant"}


def fingerprint(source: str, source_id: str, content: str, date: str) -> str:
    return hashlib.sha256(f"{source}:{source_id}:{content[:256]}:{date}".encode()).hexdigest()


def memory_age_hours(timestamp: str | None) -> float:
    """Age of a memory in hours relative to now (smaller == more recent).

    Returns ``float("inf")`` for missing or unparseable timestamps so those
    memories sort last behind any memory with a usable timestamp instead of
    crashing the ranking. Naive timestamps (no tz) are treated as UTC, matching
    how the rest of the codebase writes them.
    """
    if not timestamp:
        return float("inf")
    try:
        dt = datetime.fromisoformat(str(timestamp))
    except (ValueError, TypeError):
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        delta = datetime.now(timezone.utc) - dt
    except (OverflowError, OSError, ValueError):
        return float("inf")
    return max(0.0, delta.total_seconds() / 3600.0)


class Store:
    def __init__(self, chroma_dir: Path | None = None, db_path: Path | None = None):
        # Resolve at construction time so JARVIS_DATA_DIR / JARVIS_USER set
        # in the current process (CLI --data-dir/--user, LaunchAgent env) are
        # honoured even if the module was imported earlier.
        chroma_dir = chroma_dir or data_dir("data", "chroma")
        db_path = db_path or data_dir("data", "meta.db")
        ensure_private_dir(chroma_dir)
        ensure_private_dir(db_path.parent)
        self.chroma = chromadb.PersistentClient(path=str(chroma_dir), settings=Settings(anonymized_telemetry=False))
        self.collection = self.chroma.get_or_create_collection("memories")
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()
        self.conn.execute("PRAGMA journal_mode=WAL")

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                source TEXT,
                source_id TEXT,
                timestamp DATETIME,
                content TEXT,
                content_hash TEXT,
                tags TEXT,
                metadata TEXT,
                tier TEXT DEFAULT 'raw',
                weight REAL DEFAULT 0.3,
                route TEXT DEFAULT 'unclassified',
                expires_at DATETIME,
                consolidated_from TEXT,
                superseded INTEGER DEFAULT 0,
                embedded_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                started_at DATETIME,
                finished_at DATETIME,
                items_added INTEGER,
                items_skipped INTEGER
            );
            CREATE TABLE IF NOT EXISTS push_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                push_key TEXT UNIQUE,
                content TEXT,
                sidecar TEXT,
                attempts INTEGER DEFAULT 0,
                next_attempt_at DATETIME,
                created_at DATETIME,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_push_queue_due ON push_queue(next_attempt_at);
            CREATE TABLE IF NOT EXISTS decision_log (
                ts TEXT,
                memory_id TEXT,
                route TEXT,
                confidence TEXT,
                envelope TEXT,
                applied INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                entity_type TEXT DEFAULT 'person',
                source_count INTEGER DEFAULT 0,
                first_seen TEXT,
                last_seen TEXT
            );
            CREATE TABLE IF NOT EXISTS memory_entities (
                memory_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                PRIMARY KEY(memory_id, entity_id)
            );
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity TEXT NOT NULL,
                target_entity TEXT NOT NULL,
                relation_type TEXT DEFAULT 'related',
                source_memory_id TEXT,
                confidence REAL DEFAULT 0.5,
                created_at TEXT
            );
        """)
        self.conn.commit()
        self._migrate()
        self._expire_old()
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);
            CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp);
            CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
            CREATE INDEX IF NOT EXISTS idx_memories_route ON memories(route);
            CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories(expires_at);
            CREATE INDEX IF NOT EXISTS idx_memories_content_hash ON memories(content_hash);
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
            CREATE INDEX IF NOT EXISTS idx_memory_entities_memory ON memory_entities(memory_id);
            CREATE INDEX IF NOT EXISTS idx_memory_entities_entity ON memory_entities(entity_id);
            CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_entity);
            CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_entity);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name);
        """)
        self.conn.commit()

    def _migrate(self):
        cur = self.conn.execute("PRAGMA table_info(memories)").fetchall()
        columns = {r["name"] for r in cur}
        if "route" not in columns:
            self.conn.execute("ALTER TABLE memories ADD COLUMN route TEXT DEFAULT 'unclassified'")
            self.conn.commit()
        if "decision_log" not in [r["name"] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS decision_log (
                    ts TEXT,
                    memory_id TEXT,
                    route TEXT,
                    confidence TEXT,
                    envelope TEXT,
                    applied INTEGER DEFAULT 0
                );
            """)
            self.conn.commit()
        # Backfill existing NULL route values
        self.conn.execute("UPDATE memories SET route = 'unclassified' WHERE route IS NULL")
        self.conn.commit()

    def _content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def _delete_vectors(self, ids: list[str]):
        """Best-effort removal of stale vectors from Chroma.

        Chroma is a cache of the authoritative SQLite store, so a failure to
        delete here must never break the caller (expire/supersede have already
        committed their SQLite change) and must never open a second handle to
        the collection. Uses the same memory/chunk ids used at insert.
        """
        if not ids:
            return
        try:
            self.collection.delete(ids=list(ids))
        except Exception:  # noqa: S110, BLE001
            # Vector cleanup is best-effort; SQLite remains authoritative.
            pass

    def _expire_old(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        # Collect the ids we are about to expire so their vectors can be pruned
        # from Chroma too; otherwise stale vectors keep crowding the search
        # pre-filter and Chroma grows unbounded.
        rows = self.conn.execute(
            "SELECT id FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            self._delete_vectors(ids)
        self.conn.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        self.conn.commit()

    def exists(self, fid: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM memories WHERE id = ?", (fid,))
        return cur.fetchone() is not None

    def exists_by_content(self, content_hash: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM memories WHERE content_hash = ? AND superseded = 0 LIMIT 1", (content_hash,))
        return cur.fetchone() is not None

    def merge_device_tags(self, content_hash: str, new_tags: list[str]):
        existing = self.conn.execute("SELECT id, tags FROM memories WHERE content_hash = ?", (content_hash,)).fetchone()
        if not existing:
            return
        current_tags = set(json.loads(existing["tags"]))
        current_tags.update(new_tags)
        self.conn.execute("UPDATE memories SET tags = ? WHERE id = ?", (json.dumps(sorted(current_tags)), existing["id"]))
        self.conn.commit()

    def _tier_weight(self, tier: str) -> float:
        return TIER_WEIGHTS.get(tier, 0.3)

    # --- Knowledge Graph methods ---

    def get_or_create_entity(self, canonical_name: str, entity_type: str = "person") -> str | None:
        """Return entity id for canonical_name, creating it if absent."""
        if not canonical_name:
            return None
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        row = self.conn.execute(
            "SELECT id, source_count, first_seen FROM entities WHERE canonical_name = ?",
            (canonical_name,)
        ).fetchone()
        if row:
            eid = row["id"]
            self.conn.execute(
                "UPDATE entities SET source_count = source_count + 1, last_seen = ? WHERE id = ?",
                (now, eid)
            )
            self.conn.commit()
            return eid
        eid = hashlib.sha256(canonical_name.encode()).hexdigest()[:24]
        self.conn.execute(
            "INSERT INTO entities (id, canonical_name, entity_type, source_count, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, canonical_name, entity_type, 1, now, now)
        )
        self.conn.commit()
        return eid

    def link_memory_entity(self, memory_id: str, entity_id: str, confidence: float = 1.0):
        """Associate a memory with an entity"""
        if not memory_id or not entity_id:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO memory_entities (memory_id, entity_id, confidence) VALUES (?, ?, ?)",
            (memory_id, entity_id, confidence)
        )
        self.conn.commit()

    def lookup_entities(self, memory_ids, cap: int = 20):
        """Return {memory_id: [{name, entity_type}, ...]} for the given memories.

        Used to surface the knowledge graph alongside search/chat results.
        """
        ids = list(dict.fromkeys(memory_ids))[:cap]
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT me.memory_id, e.canonical_name, e.entity_type"
            f" FROM memory_entities me JOIN entities e ON me.entity_id = e.id"
            f" WHERE me.memory_id IN ({marks})",
            ids,
        ).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["memory_id"], []).append(
                {"name": r["canonical_name"], "entity_type": r["entity_type"]}
            )
        return out

    def add_relationship(self, source_entity: str, target_entity: str, relation_type: str, source_memory_id: str, confidence: float):
        """Create a relationship edge between two entities"""
        if not source_entity or not target_entity:
            return
        # For undirected relations (e.g. co_participant), canonicalize the pair
        # so the smaller entity id is always stored as source_entity. This makes
        # reversed adds (B,A) collapse onto the same directed key as (A,B),
        # preventing duplicate symmetric rows that inflate edge counts.
        if relation_type in UNDIRECTED_RELATIONS and target_entity < source_entity:
            source_entity, target_entity = target_entity, source_entity
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        # Upsert: if same pair + relation exists, keep best confidence
        existing = self.conn.execute(
            "SELECT id, confidence FROM relationships WHERE source_entity = ? AND target_entity = ? AND relation_type = ?",
            (source_entity, target_entity, relation_type)
        ).fetchone()
        if existing:
            best_conf = max(existing["confidence"], confidence)
            if confidence >= existing["confidence"]:
                self.conn.execute(
                    "UPDATE relationships SET confidence = ?, source_memory_id = ?, created_at = ? WHERE id = ?",
                    (best_conf, source_memory_id, now, existing["id"])
                )
            self.conn.commit()
            return
        self.conn.execute(
            "INSERT INTO relationships (source_entity, target_entity, relation_type, source_memory_id, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (source_entity, target_entity, relation_type, source_memory_id, confidence, now)
        )
        self.conn.commit()

    def add(self, fid: str, source: str, source_id: str, timestamp: str, content: str, tags: list, metadata: dict, embedding: list[float] | None, tier: str = "raw", expires_at: str | None = None, consolidated_from: str | None = None, superseded: bool = False, route: str = "unclassified"):
        content_hash = self._content_hash(content)
        if self.exists_by_content(content_hash):
            self.merge_device_tags(content_hash, tags)
            return True
        if self.exists(fid):
            return True
        weight = self._tier_weight(tier)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        # A missing/failed embedding (None or empty) means the memory is stored
        # without a vector: embedded_at stays NULL and Chroma is untouched, so
        # reindex_missing() can retry the embed later instead of permanently
        # poisoning search with a degenerate zero-vector.
        embedded_at = now if embedding else None
        self.conn.execute(
            "INSERT OR IGNORE INTO memories (id, source, source_id, timestamp, content, content_hash, tags, metadata, tier, weight, route, expires_at, consolidated_from, superseded, embedded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fid, source, source_id, timestamp, content, content_hash, json.dumps(tags or []), json.dumps(metadata or {}), tier, weight, route, expires_at, consolidated_from, 1 if superseded else 0, embedded_at),
        )
        self.conn.commit()
        if embedding:
            chroma_meta = {"source": source, "timestamp": timestamp, "tier": tier, "weight": weight, "route": route}
            self.collection.add(ids=[fid], embeddings=[embedding], documents=[content], metadatas=[chroma_meta])
        return True

    def search(self, query_embedding: list[float], n_results: int = 10, source_filter: str | None = None, re_rank: bool = True, recency_boost: bool = True):
        where = {"source": source_filter} if source_filter else None
        results = self.collection.query(query_embeddings=[query_embedding], n_results=n_results * 3, where=where)
        docs = results.get("documents", [[]])[0]
        ids = results.get("ids", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        # Keep each result paired with its vector distance so re-ranking can
        # preserve Chroma's similarity order *within* a tier instead of
        # discarding it. Smaller distance == more similar.
        rows = []
        for rank, (doc_id, doc, meta) in enumerate(zip(ids, docs, metadatas)):
            dist = distances[rank] if rank < len(distances) else 0.0
            if source_filter:
                row = self.conn.execute("SELECT * FROM memories WHERE id = ? AND superseded = 0 AND source = ?", (doc_id, source_filter)).fetchone()
            else:
                row = self.conn.execute("SELECT * FROM memories WHERE id = ? AND superseded = 0", (doc_id,)).fetchone()
            if row:
                rows.append((dist, dict(row)))
        if re_rank and rows:
            # Re-rank by tier weight (descending) then vector similarity
            # (ascending distance == descending similarity). Recency is only a
            # *tertiary* tiebreak: it never overrides relevance, it just breaks
            # exact ties so a freshly-captured memory surfaces before an older
            # one of identical weight and similarity. The stable sort keeps
            # Chroma's returned order as the final tie-break, so within a tier
            # the exact memory the user asked about is not dropped.
            if recency_boost:
                rows.sort(key=lambda pair: (-(pair[1].get("weight", 0.3)), pair[0], memory_age_hours(pair[1].get("timestamp"))))
            else:
                rows.sort(key=lambda pair: (-(pair[1].get("weight", 0.3)), pair[0]))
        # Cap the result list at n_results regardless of re_rank. Without this,
        # re_rank=False would return up to n_results*3 rows (the full Chroma
        # fetch) instead of the requested count.
        rows = rows[:n_results]
        return [row for _, row in rows]

    def get_by_tier(self, tier: str, limit: int = 100):
        cur = self.conn.execute("SELECT * FROM memories WHERE tier = ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?", (tier, limit))
        return [dict(r) for r in cur.fetchall()]

    def get_recent_raw(self, hours: int = 24, limit: int = 200):
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
        cur = self.conn.execute("SELECT * FROM memories WHERE tier = 'raw' AND timestamp >= ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?", (cutoff, limit))
        return [dict(r) for r in cur.fetchall()]

    def get_by_route(self, route: str, limit: int = 100):
        cur = self.conn.execute("SELECT * FROM memories WHERE route = ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?", (route, limit))
        return [dict(r) for r in cur.fetchall()]

    def get_unclassified(self, limit: int = 100):
        cur = self.conn.execute("SELECT * FROM memories WHERE route = 'unclassified' AND superseded = 0 ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]

    def get_unembedded(self, limit: int = 200):
        """Memories that are not yet in the vector store (embedded_at IS NULL).

        Normal ingestion embeds at add time, so this is typically empty — it is
        the safety net for rows written without an embedding (e.g. imported
        data) and the input for incremental / re-indexing runs.
        """
        cur = self.conn.execute(
            "SELECT * FROM memories WHERE superseded = 0 AND embedded_at IS NULL"
            " ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    def mark_embedded(self, fid: str):
        """Record that a memory now has a vector embedding."""
        self.conn.execute(
            "UPDATE memories SET embedded_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), fid),
        )
        self.conn.commit()

    # ── Durable push queue (device → Lightspeed) ────────────────────────────

    def enqueue_push(self, push_key: str, content: str, sidecar: dict):
        """Add a memory to the push queue (idempotent on push_key)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO push_queue (push_key, content, sidecar, created_at)"
            " VALUES (?, ?, ?, ?)",
            (push_key, content, json.dumps(sidecar, ensure_ascii=False),
             datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
        )
        self.conn.commit()

    def push_queue_due(self, limit: int = 1000, now: str | None = None):
        """Items ready to push now (never attempted, or backoff elapsed)."""
        now = now or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        cur = self.conn.execute(
            "SELECT * FROM push_queue WHERE next_attempt_at IS NULL OR next_attempt_at <= ?"
            " ORDER BY created_at ASC LIMIT ?",
            (now, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def push_queue_fail(self, queue_id: int, error: str, attempts: int, next_attempt_at: str):
        """Record a failed push with its next retry time (backoff)."""
        self.conn.execute(
            "UPDATE push_queue SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
            (attempts, next_attempt_at, error[:500], queue_id),
        )
        self.conn.commit()

    def push_queue_success(self, queue_id: int):
        """Remove a successfully pushed item from the queue."""
        self.conn.execute("DELETE FROM push_queue WHERE id = ?", (queue_id,))
        self.conn.commit()

    def push_queue_stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT attempts, COUNT(*) AS n FROM push_queue GROUP BY attempts"
        ).fetchall()
        return {
            "total": sum(r["n"] for r in rows),
            "by_attempts": {r["attempts"]: r["n"] for r in rows},
        }

    def log_sync(self, source: str, started_at: str, finished_at: str,
                 items_added: int, items_skipped: int):
        """Record a sync run in sync_log (push path writes here too)."""
        self.conn.execute(
            "INSERT INTO sync_log (source, started_at, finished_at, items_added, items_skipped)"
            " VALUES (?, ?, ?, ?, ?)",
            (source, started_at, finished_at, items_added, items_skipped),
        )
        self.conn.commit()

    def promote_raw_to_session(self, days: int = 7, limit: int = 500) -> int:
        """Promote raw memories older than *days* days to the session tier.

        Updates the SQLite tier/weight and syncs the same metadata into Chroma
        so search re-ranking keeps using the promoted weight. Returns the number
        of memories promoted.
        """
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE tier = 'raw' AND superseded = 0"
            " AND timestamp < ? ORDER BY timestamp ASC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        promoted = 0
        for r in rows:
            fid = r["id"]
            weight = TIER_WEIGHTS["session"]
            self.conn.execute(
                "UPDATE memories SET tier = 'session', weight = ? WHERE id = ?",
                (weight, fid),
            )
            chroma_meta = {
                "source": r["source"],
                "timestamp": r["timestamp"],
                "tier": "session",
                "weight": weight,
                "route": r["route"],
            }
            try:
                self.collection.update(ids=[fid], metadatas=[chroma_meta])
            except Exception:
                # Vector metadata is best-effort; SQLite remains authoritative.
                pass
            promoted += 1
        self.conn.commit()
        return promoted

    def mark_superseded(self, fid: str):
        self.conn.execute("UPDATE memories SET superseded = 1 WHERE id = ?", (fid,))
        self.conn.commit()
        # Prune the superseded memory's vector(s) from Chroma so they no longer
        # crowd the search pre-filter (which filters out superseded rows from
        # SQLite but left the vectors behind).
        self._delete_vectors([fid])

    def log_decision(self, memory_id: str, route: str, confidence: str, envelope: dict, applied: int = 0):
        self.conn.execute(
            "INSERT INTO decision_log (ts, memory_id, route, confidence, envelope, applied) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), memory_id, route, confidence, json.dumps(envelope), 1 if applied else 0)
        )
        self.conn.commit()

    def stats(self):
        cur = self.conn.execute("SELECT source, tier, route, COUNT(*) as count, MIN(timestamp) as oldest, MAX(timestamp) as newest FROM memories WHERE superseded = 0 GROUP BY source, tier, route ORDER BY source, tier, route")
        return [dict(r) for r in cur.fetchall()]

    def close(self):
        self.conn.close()

