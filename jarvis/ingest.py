"""
Jarvis Ingest Module for semantic text chunker integration.

This module provides functions to ingest and process semantic text chunks.
"""

import os
import sys
import time
import signal
import subprocess
from pathlib import Path
import json
from datetime import datetime

def _fallback_chunk(text: str, chunk_size: int = 2048, overlap: int = 200):
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        start = end - overlap if end < n else n
    return chunks

def chunk_text(text: str, chunk_size: int = 2048, overlap: int = 200):
    """
    Chunk semantic text into smaller pieces.

    Args:
        text (str): The input text to be chunked.
        chunk_size (int): The size of each chunk. Defaults to 2048.
        overlap (int): The overlap between chunks. Defaults to 200.

    Returns:
        list[str]: A list of chunked texts.
    """
    if _HAS_SEMANTIC_CHUNKER:
        try:
            chunker = SemanticTextChunker(chunk_size=chunk_size, overlap=overlap)
            return chunker.chunk(text)
        except Exception as e:
            print(f"Error: {e}")
    else:
        # Fallback to a simple chunking approach
        chunks = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + chunk_size, n)
            chunks.append(text[start:end])
            start = end - overlap if end < n else n
        return chunks

def _init_db():
    """
    Initialize the SQLite database.

    This function creates the necessary tables and indexes for the ingest module.
    """
    # Create tasks table
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
    # Create tasks table indexes
    self.conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent);
        CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
    """)

def get_task_id(task_title: str) -> str:
    """
    Get the task ID based on the given title.

    Args:
        task_title (str): The title of the task to be retrieved.

    Returns:
        str: The task ID.
    """
    # Query the tasks table for the matching title
    row = self.conn.execute("SELECT id FROM tasks WHERE title = ?", (task_title,)).fetchone()
    return row["id"] if row else None

def save_task(task_id: str, task_data: dict) -> bool:
    """
    Save a new task to the database.

    Args:
        task_id (str): The ID of the task to be saved.
        task_data (dict): The data of the task to be saved.

    Returns:
        bool: True if the task was saved successfully, False otherwise.
    """
    # Insert the task into the tasks table
    self.conn.execute("INSERT INTO tasks (id, title, description, agent, status, priority, source, raw_idea, created_at) VALUES (?, ?, ?, ?, 'pending_review', ?, ?, ?, ?)",
                      (task_id, task_data["title"], task_data["description"], task_data["agent"], task_data["status"], task_data["priority"], task_data["source"], task_data.get("raw_idea", ""), task_data.get("created_at", "")),
                      )
    # Commit the changes
    self.conn.commit()
    return True

def get_tasks():
    """
    Get all tasks from the database.

    Returns:
        list[dict]: A list of dictionaries containing the task data.
    """
    # Query the tasks table for all rows
    rows = self.conn.execute("SELECT * FROM tasks").fetchall()
    return [{"id": row["id"], "title": row["title"], "description": row["description"], "agent": row["agent"], "status": row["status"], "priority": row["priority"]} for row in rows]

def main():
    # Initialize the database
    _init_db()

    while True:
        try:
            # Get user input
            task_title = input("Enter a task title: ")
            task_data = {
                "title": task_title,
                "description": "",
                "agent": "code",
                "status": "pending_review",
                "priority": 3,
                "source": "user",
                "raw_idea": ""
            }

            # Save the task
            task_id = save_task(get_task_id(task_title), task_data)

            # Get tasks
            tasks = get_tasks()

            # Print the tasks
            for i, task in enumerate(tasks):
                print(f"Task {i+1}:")
                print(f"Title: {task['title']}")
                print(f"Description: {task['description']}")
                print(f"Agent: {task['agent']}")
                print(f"Status: {task['status']}")
                print(f"Priority: {task['priority']}")
                print(f"Raw Idea: {task.get('raw_idea', '')}")
                print()

        except KeyboardInterrupt:
            break