import sqlite3
import hashlib
import json
import chromadb
from chromadb.config import Settings
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_CHROMA_DIR = Path.home() / "jarvis" / "data" / "chroma"
DEFAULT_DB_PATH = Path.home() / "jarvis" / "data" / "meta.db"

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


def fingerprint(source: str, source_id: str, content: str, date: str) -> str:
    return hashlib.sha256(f"{source}:{source_id}:{content[:256]}:{date}".encode()).hexdigest()


class Store:
    def __init__(self, chroma_dir: Path = DEFAULT_CHROMA_DIR, db_path: Path = DEFAULT_DB_PATH):
        chroma_dir.mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.chroma = chromadb.PersistentClient(path=str(chroma_dir), settings=Settings(anonymized_telemetry=False))
        self.collection = self.chroma.get_or_create_collection("memories")
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

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

    def _expire_old(self):
        now = datetime.utcnow().isoformat()
        self.conn.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        self.conn.commit()

    def exists(self, fid: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM memories WHERE id = ?", (fid,))
        return cur.fetchone() is not None

    def exists_by_content(self, content_hash: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM memories WHERE content_hash = ? LIMIT 1", (content_hash,))
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

    def add(self, fid: str, source: str, source_id: str, timestamp: str, content: str, tags: list, metadata: dict, embedding: list[float], tier: str = "raw", expires_at: str | None = None, consolidated_from: str | None = None, superseded: bool = False, route: str = "unclassified"):
        content_hash = self._content_hash(content)
        if self.exists_by_content(content_hash):
            self.merge_device_tags(content_hash, tags)
            return False
        if self.exists(fid):
            return False
        weight = self._tier_weight(tier)
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "INSERT OR IGNORE INTO memories (id, source, source_id, timestamp, content, content_hash, tags, metadata, tier, weight, route, expires_at, consolidated_from, superseded, embedded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fid, source, source_id, timestamp, content, content_hash, json.dumps(tags or []), json.dumps(metadata or {}), tier, weight, route, expires_at, consolidated_from, 1 if superseded else 0, now),
        )
        self.conn.commit()
        chroma_meta = {"source": source, "timestamp": timestamp, "tier": tier, "weight": weight, "route": route}
        self.collection.add(ids=[fid], embeddings=[embedding], documents=[content], metadatas=[chroma_meta])
        return True

    def search(self, query_embedding: list[float], n_results: int = 10, source_filter: str | None = None, re_rank: bool = True):
        where = {"source": source_filter} if source_filter else None
        results = self.collection.query(query_embeddings=[query_embedding], n_results=n_results * 3, where=where)
        docs = results.get("documents", [[]])[0]
        ids = results.get("ids", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        rows = []
        for doc_id, doc, meta in zip(ids, docs, metadatas):
            row = self.conn.execute("SELECT * FROM memories WHERE id = ? AND superseded = 0", (doc_id,)).fetchone()
            if row:
                rows.append(dict(row))
        if re_rank and rows:
            rows.sort(key=lambda r: r.get("weight", 0.3), reverse=True)
            rows = rows[:n_results]
        return rows

    def get_by_tier(self, tier: str, limit: int = 100):
        cur = self.conn.execute("SELECT * FROM memories WHERE tier = ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?", (tier, limit))
        return [dict(r) for r in cur.fetchall()]

    def get_recent_raw(self, hours: int = 24, limit: int = 200):
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        cur = self.conn.execute("SELECT * FROM memories WHERE tier = 'raw' AND timestamp >= ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?", (cutoff, limit))
        return [dict(r) for r in cur.fetchall()]

    def get_by_route(self, route: str, limit: int = 100):
        cur = self.conn.execute("SELECT * FROM memories WHERE route = ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?", (route, limit))
        return [dict(r) for r in cur.fetchall()]

    def get_unclassified(self, limit: int = 100):
        cur = self.conn.execute("SELECT * FROM memories WHERE route = 'unclassified' AND superseded = 0 ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]

    def mark_superseded(self, fid: str):
        self.conn.execute("UPDATE memories SET superseded = 1 WHERE id = ?", (fid,))
        self.conn.commit()

    def log_decision(self, memory_id: str, route: str, confidence: str, envelope: dict, applied: int = 0):
        self.conn.execute(
            "INSERT INTO decision_log (ts, memory_id, route, confidence, envelope, applied) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), memory_id, route, confidence, json.dumps(envelope), 1 if applied else 0)
        )
        self.conn.commit()

    def stats(self):
        cur = self.conn.execute("SELECT source, tier, route, COUNT(*) as count, MIN(timestamp) as oldest, MAX(timestamp) as newest FROM memories WHERE superseded = 0 GROUP BY source, tier, route ORDER BY source, tier, route")
        return [dict(r) for r in cur.fetchall()]

    def close(self):
        self.conn.close()
