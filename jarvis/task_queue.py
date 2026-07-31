"""
SQLite-backed task queue for the Mayor orchestrator.

Tasks flow through these statuses:
    pending_review → approved → in_progress → completed / failed / blocked

The Mayor auto-creates tasks from user ideas (submitted via dashboard/CLI/phone).
The user can review and approve tasks before agents start working on them.
"""

import sqlite3
from typing import Optional

class TaskQueue:
    def __init__(self, db_path: str | None = "task_queue.db"):
        """
        Initialize the task queue.

        Args:
            db_path (str): The path to the SQLite database file. Defaults to "task_queue.db".
        """
        self.conn_path = Path(db_path) if db_path else None
        self.conn = sqlite3.connect(str(self.conn_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def _init_db(self):
        """
        Initialize the SQLite database.
        """
        try:
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
            """)
        except sqlite3.Error as e:
            print(f"Error initializing database: {e}")

    def add_task(self, title: str, description: str = "", agent: str = "code", priority: int = 3, source: str = "user", raw_idea: str = "") -> str:
        """
        Add a new task to the queue.

        Args:
            title (str): The title of the task.
            description (str): The description of the task. Defaults to an empty string.
            agent (str): The agent that created the task. Defaults to "code".
            priority (int): The priority of the task. Defaults to 3.
            source (str): The source of the task. Defaults to "user".
            raw_idea (str): The raw idea for the task. Defaults to an empty string.

        Returns:
            str: The ID of the newly added task.
        """
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

    def get_task(self, task_id: str) -> Optional[dict]:
        """
        Get a task by its ID.

        Args:
            task_id (str): The ID of the task to retrieve.

        Returns:
            dict | None: The task data if found, otherwise None.
        """
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None