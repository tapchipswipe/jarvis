"""
jarvis/sessions.py — SQLite-backed session database for multi-turn agent chat.
"""
import sqlite3
import uuid
import json
from datetime import datetime
from pathlib import Path

from jarvis.paths import data_dir

DEFAULT_DB_PATH = data_dir("data", "sessions.db")


class SessionDB:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT,
                summary TEXT,
                tier TEXT DEFAULT 'raw'
            );
            CREATE TABLE IF NOT EXISTS session_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                tool_call_id TEXT,
                created_at TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_session_messages_session
                ON session_messages(session_id);
        """)
        self.conn.commit()

    def create_session(self, title="", tier="raw") -> str:
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, tier) VALUES (?, ?, ?, ?, ?)",
            (session_id, title, now, now, tier),
        )
        self.conn.commit()
        self.append_message(session_id, "assistant", f"Session '{title}' started.")
        return session_id

    def append_message(self, session_id, role, content, tool_calls=None):
        now = datetime.utcnow().isoformat()
        tool_calls_json = json.dumps(tool_calls) if tool_calls is not None else None
        self.conn.execute(
            "INSERT INTO session_messages (session_id, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, tool_calls_json, now),
        )
        self.conn.commit()
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        self.conn.commit()

    def get_session(self, session_id) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit=20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_messages(self, session_id, limit=100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM session_messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("tool_calls"):
                try:
                    d["tool_calls"] = json.loads(d["tool_calls"])
                except Exception:
                    d["tool_calls"] = None
            else:
                d["tool_calls"] = None
            results.append(d)
        return results

    def update_session(self, session_id, **kwargs):
        if not kwargs:
            return
        now = datetime.utcnow().isoformat()
        kwargs["updated_at"] = now
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        params = list(kwargs.values()) + [session_id]
        self.conn.execute(
            f"UPDATE sessions SET {sets} WHERE id = ?",
            params,
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
