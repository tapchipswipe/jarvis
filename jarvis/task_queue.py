"""
jarvis/task_queue.py — SQLite-backed task queue for the Mayor orchestrator.

Tasks flow through these statuses:
    pending_review → approved → in_progress → completed / failed / blocked

The Mayor auto-creates tasks from user ideas (submitted via dashboard/CLI/phone).
The user can review and approve tasks before agents start working on them.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from jarvis.paths import config_file


class TaskQueue:
    """SQLite-backed task queue for the Mayor."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else config_file("task_queue.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                agent TEXT DEFAULT 'code',
                status TEXT DEFAULT 'pending_review',
                priority INTEGER DEFAULT 3,
                source TEXT DEFAULT 'user',
                raw_idea TEXT,
                created_at TEXT,
                approved_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                result TEXT,
                commit_hash TEXT,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent);
            CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
        """)
        self.conn.commit()

    def add_task(
        self,
        title: str,
        description: str = "",
        agent: str = "code",
        priority: int = 3,
        source: str = "user",
        raw_idea: str = "",
    ) -> str:
        """Add a new task to the queue. Returns the task ID."""
        task_id = str(uuid.uuid4())[:12]
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO tasks
               (id, title, description, agent, status, priority, source, raw_idea, created_at)
               VALUES (?, ?, ?, ?, 'pending_review', ?, ?, ?, ?)""",
            (task_id, title, description, agent, priority, source, raw_idea, now),
        )
        self.conn.commit()
        return task_id

    def get_task(self, task_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, status: str | None = None, agent: str | None = None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM tasks"
        params = []
        conditions = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY priority ASC, created_at ASC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def approve_task(self, task_id: str) -> bool:
        """Move a task from pending_review to approved."""
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'approved', approved_at = ? WHERE id = ? AND status = 'pending_review'",
            (now, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def approve_all(self) -> int:
        """Approve all pending_review tasks. Returns count approved."""
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'approved', approved_at = ? WHERE status = 'pending_review'",
            (now,),
        )
        self.conn.commit()
        return cur.rowcount

    def reject_task(self, task_id: str) -> bool:
        """Reject a pending task (mark as failed with reason)."""
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'failed', completed_at = ?, error = 'rejected by user' WHERE id = ? AND status = 'pending_review'",
            (now, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def start_task(self, task_id: str) -> bool:
        """Mark a task as in_progress."""
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'in_progress', started_at = ?, attempts = attempts + 1 WHERE id = ? AND status = 'approved'",
            (now, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def complete_task(self, task_id: str, result: str = "", commit_hash: str = "") -> bool:
        """Mark a task as completed."""
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ?, result = ?, commit_hash = ? WHERE id = ?",
            (now, result, commit_hash, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def fail_task(self, task_id: str, error: str = "") -> bool:
        """Mark a task as failed."""
        now = datetime.utcnow().isoformat()
        task = self.get_task(task_id)
        if not task:
            return False
        attempts = task.get("attempts", 0)
        max_attempts = task.get("max_attempts", 3)
        if attempts < max_attempts:
            # Reset to approved for retry
            cur = self.conn.execute(
                "UPDATE tasks SET status = 'approved', error = ? WHERE id = ?",
                (error, task_id),
            )
        else:
            # Max attempts reached → blocked
            cur = self.conn.execute(
                "UPDATE tasks SET status = 'blocked', completed_at = ?, error = ? WHERE id = ?",
                (now, error, task_id),
            )
        self.conn.commit()
        return cur.rowcount > 0

    def next_approved_task(self) -> dict | None:
        """Get the next approved task (highest priority, oldest first)."""
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE status = 'approved' ORDER BY priority ASC, approved_at ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def update_priority(self, task_id: str, priority: int) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET priority = ? WHERE id = ?",
            (priority, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def update_agent(self, task_id: str, agent: str) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET agent = ? WHERE id = ?",
            (agent, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def stats(self) -> dict:
        """Return counts by status."""
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
        ).fetchall()
        return {r["status"]: r["count"] for r in rows}

    def close(self):
        if self.conn:
            self.conn.close()
