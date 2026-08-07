"""
Shared fixtures for jarvis test suite.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _hermetic_env():
    """Clear jarvis env vars from os.environ before each test and restore after.

    The CI/shell environment is often contaminated with JARVIS_TOKEN, JARVIS_MODE,
    JARVIS_REMOTE, JARVIS_TLS_* and other JARVIS_* knobs that would change behavior
    (e.g. making the server tests expect a token and fail with 403). Saving and
    restoring the previously-set values keeps the suite hermetic and non-disruptive
    for any tooling that reads the ambient env after the run.
    """
    keys = [k for k in os.environ if k.startswith("JARVIS_")]
    saved = {k: os.environ[k] for k in keys}
    for k in keys:
        del os.environ[k]
    yield
    for k in keys:
        if k in saved:
            os.environ[k] = saved[k]
        else:
            os.environ.pop(k, None)


@pytest.fixture()
def tmp_sqlite(tmp_path):
    """Yield a path to a fresh temporary SQLite database file."""
    db = tmp_path / "test.db"
    yield str(db)


@pytest.fixture()
def mock_chroma_collection():
    """Return a MagicMock for a chromadb collection."""
    col = MagicMock()
    col.add = MagicMock()
    col.query = MagicMock(return_value={
        "documents": [[]],
        "ids": [[]],
        "metadatas": [[]],
    })
    col.get = MagicMock(return_value={"documents": [], "ids": [], "metadatas": []})
    return col


@pytest.fixture()
def mock_chroma_client(mock_chroma_collection):
    """Return a MagicMock chromadb PersistentClient."""
    client = MagicMock()
    client.get_or_create_collection = MagicMock(return_value=mock_chroma_collection)
    return client


@pytest.fixture()
def store(tmp_path, mock_chroma_client, mock_chroma_collection):
    """Create a Store backed by temporary SQLite + mocked Chroma."""
    from jarvis.store import Store

    chroma_dir = tmp_path / "chroma"
    db_path = tmp_path / "meta.db"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with patch("jarvis.store.chromadb.PersistentClient", return_value=mock_chroma_client):
        s = Store(chroma_dir=chroma_dir, db_path=db_path)

    # Ensure WAL mode is active
    mode = s.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal", f"Expected WAL, got {mode}"

    yield s
    s.close()