"""jarvis/cache.py — disposable line-of-sight cache for the thin client.

Not a brain: purely data. Two surfaces:
  * outbox   — write-back buffer (durable, idempotent) fed by collector/CLI
               capture; flushed to the server with backoff.
  * tail     — rolling read-cache of recent memories for offline glance
               (plain substring search, no vectors).
Explicitly disposable: delete the db and nothing real is lost.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _default_path() -> Path:
    """Default cache DB, resolved from env at call time (not import time) so tests
    can override JARVIS_CACHE after the module is imported."""
    return Path(os.environ.get("JARVIS_CACHE", "~/.cache/jarvis/cache.db")).expanduser()
BACKOFF = [5, 15, 60, 300]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Cache:
    def __init__(self, path: Path | str | None = None, conn: sqlite3.Connection | None = None):
        self.path = Path(path) if path else _default_path()
        if conn is not None:
            self.conn = conn
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                attempt INTEGER DEFAULT 0,
                next_attempt_at DATETIME,
                created_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS tail (
                id TEXT PRIMARY KEY,
                content TEXT,
                source TEXT,
                timestamp TEXT,
                tier TEXT,
                tags TEXT,
                cached_at DATETIME,
                last_access DATETIME
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(status, next_attempt_at);
        """)
        self.conn.commit()

    # ── outbox (write-back) ─────────────────────────────────────────────────
    def enqueue(self, content: str, source: str = "device", tags: list[str] | None = None,
                metadata: dict | None = None) -> bool:
        """Append a capture item. Idempotent on content hash — returns False if a
        pending copy already exists (so retries never duplicate)."""
        if not content or not content.strip():
            return False
        key = hashlib.sha256(content.encode()).hexdigest()
        payload = json.dumps({"content": content, "source": source,
                              "tags": tags or [], "metadata": metadata or {}})
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO outbox (key, payload, status, attempt, created_at)"
            " VALUES (?, ?, 'pending', 0, ?)",
            (key, payload, _iso()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def due(self, limit: int = 500) -> list[dict]:
        now = _iso()
        rows = self.conn.execute(
            "SELECT * FROM outbox WHERE status = 'pending' "
            " AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
            " ORDER BY created_at ASC LIMIT ?",
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_synced(self, outbox_id: int) -> None:
        self.conn.execute("DELETE FROM outbox WHERE id = ?", (outbox_id,))
        self.conn.commit()

    def mark_retry(self, outbox_id: int, backoff_seconds: int | None = None) -> None:
        row = self.conn.execute("SELECT attempt FROM outbox WHERE id = ?", (outbox_id,)).fetchone()
        attempt = (row["attempt"] if row else 0) + 1
        bo = backoff_seconds if backoff_seconds is not None else BACKOFF[min(attempt - 1, len(BACKOFF) - 1)]
        next_at = (datetime.now(timezone.utc).timestamp() + bo)
        next_iso = datetime.fromtimestamp(next_at, tz=timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE outbox SET attempt = ?, next_attempt_at = ? WHERE id = ?",
            (attempt, next_iso, outbox_id),
        )
        self.conn.commit()

    def pending_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS c FROM outbox WHERE status = 'pending'").fetchone()["c"]

    # ── tail (rolling read-cache) ───────────────────────────────────────────
    def store_tail(self, records: list[dict], cap: int = 2000) -> None:
        now = _iso()
        for r in records:
            self.conn.execute(
                "INSERT OR REPLACE INTO tail (id, content, source, timestamp, tier, tags, cached_at, last_access)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (r.get("id"), r.get("content"), r.get("source"), r.get("timestamp"),
                 r.get("tier"), json.dumps(r.get("tags") or []), now, now),
            )
        # evict beyond cap by last access
        self.conn.execute("DELETE FROM tail WHERE id NOT IN (SELECT id FROM tail ORDER BY last_access DESC LIMIT ?)", (cap,))
        self.conn.commit()

    def tail_search(self, q: str, limit: int = 10) -> list[dict]:
        """Offline keyword glance over the cached tail."""
        term = f"%{q}%"
        rows = self.conn.execute(
            "SELECT id, content, source, timestamp, tier, tags FROM tail"
            " WHERE content LIKE ? OR source LIKE ?"
            " ORDER BY timestamp DESC LIMIT ?",
            (term, term, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:  # noqa: BLE001 - tags are best-effort
                d["tags"] = []
            d["stale"] = True  # from cache, not authoritative
            out.append(d)
        return out

    # ── kv ──────────────────────────────────────────────────────────────────
    def put_kv(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def get_kv(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def close(self):
        if self.conn:
            self.conn.close()


def flush_outbox(cache: Cache, limit: int = 200) -> dict:
    """Push due outbox items to the server. Returns {pushed, failed, offline}.

    Offline = server unreachable -> items stay queued (never dropped).
    """
    from jarvis import remote
    if not remote.is_remote():
        return {"pushed": 0, "failed": 0, "offline": True}
    if not remote.remote_ok():
        return {"pushed": 0, "failed": 0, "offline": True}
    due = cache.due(limit=limit)
    if not due:
        return {"pushed": 0, "failed": 0, "offline": False}
    # batch: split into chunks and remember via the server
    mems = [json.loads(d["payload"]) for d in due]
    for chunk_start in range(0, len(mems), 200):
        chunk = mems[chunk_start:chunk_start + 200]
        try:
            remote.remember_batch(chunk)
        except Exception:  # noqa: BLE001 - keep queued on any server error
            for d in due[chunk_start:chunk_start + 200]:
                cache.mark_retry(d["id"])
            return {"pushed": 0, "failed": len(chunk), "offline": False}
    for d in due:
        cache.mark_synced(d["id"])
    return {"pushed": len(due), "failed": 0, "offline": False}
