"""
Tests for jarvis/tools.py

Covers:
  - execute_tool dispatch + unknown-tool error
  - each tool's error-resilience via the @_safe wrapper (never raises)
  - schema integrity against TOOLS registry
"""
from __future__ import annotations

from unittest.mock import patch

from jarvis.tools import TOOLS, TOOLS_SCHEMA, execute_tool


def test_schema_names_match_registry():
    names = set(TOOLS.keys())
    schema_names = {s["function"]["name"] for s in TOOLS_SCHEMA}
    assert names == schema_names
    assert len(TOOLS) >= 6


def test_unknown_tool_returns_error():
    result = execute_tool("nope", None, {})
    assert result["error"]


def test_safety_wrapper_never_raises():
    # A tool that stores via Store() would hit the real DB; patch it to raise.
    with patch("jarvis.tools.Store", side_effect=RuntimeError("boom")):
        result = execute_tool("search_memories", None, {"query": "x"})
        assert "error" in result


# ── search_memories ──────────────────────────────────────────────────────────

def test_search_memories_empty_query_is_safe():
    result = execute_tool("search_memories", None, {})
    assert result == {"results": [], "note": "empty query"}


def test_search_memories_returns_results():
    class FakeStore:
        def __init__(self):
            pass
        def search(self, *a, **k):
            return [{"id": "1", "content": "c", "source": "s", "timestamp": "t", "tier": "raw"}]
        def close(self):
            pass

    with patch("jarvis.tools.Store", return_value=FakeStore()), \
         patch("jarvis.tools.get_embedding", return_value=[0.1]):
        result = execute_tool("search_memories", None, {"query": "hello", "n": 3})
        assert result["results"][0]["id"] == "1"


# ── check_calendar ───────────────────────────────────────────────────────────

def test_check_calendar_empty_store():
    from unittest.mock import MagicMock
    rows = MagicMock()
    rows.fetchall.return_value = []
    conn = MagicMock()
    conn.execute.return_value = rows
    fs = MagicMock()
    fs.conn = conn
    with patch("jarvis.tools.Store", return_value=fs):
        result = execute_tool("check_calendar", None, {})
        assert "events" in result
        assert result["events"] == []


# ── get_entity_context ───────────────────────────────────────────────────────

def test_get_entity_context_requires_name():
    result = execute_tool("get_entity_context", None, {})
    assert "error" in result


def test_get_entity_context_missing_entity():
    with patch("jarvis.tools.resolve_entity", return_value=None):
        result = execute_tool("get_entity_context", None, {"name": "Nobody"})
        assert "not found" in result["error"]


# ── summarize ────────────────────────────────────────────────────────────────

def test_summarize_needs_two_memories():
    result = execute_tool("summarize", None, {"memory_ids": ["a"]})
    assert result.get("error") == "insufficient memories"


def test_summarize_combines_content():
    from unittest.mock import MagicMock
    rows_by_id = {
        "a": MagicMock(__getitem__=lambda self, k: "first", content="first"),
        "b": MagicMock(__getitem__=lambda self, k: "second", content="second"),
    }
    conn = MagicMock()
    conn.execute.side_effect = lambda q, params: MagicMock(
        fetchone=lambda: rows_by_id.get(params[0])
    )
    fs = MagicMock()
    fs.conn = conn
    with patch("jarvis.tools.Store", return_value=fs):
        result = execute_tool("summarize", None, {"memory_ids": ["a", "b"]})
        assert result["count"] == 2
        assert "first" in result["summary"]


# ── search_web ───────────────────────────────────────────────────────────────

def test_search_web_empty_query():
    result = execute_tool("search_web", None, {})
    assert result == {"results": [], "note": "empty query"}


def test_search_web_handles_network_failure():
    with patch("jarvis.tools.urllib.request.urlopen", side_effect=OSError("no net")):
        result = execute_tool("search_web", None, {"query": "hello"})
        assert "error" in result


# ── create_reminder ──────────────────────────────────────────────────────────

def test_create_reminder_requires_title():
    result = execute_tool("create_reminder", None, {})
    assert result == {"error": "title is required", "status": "error"}


def test_create_reminder_writes_file(tmp_path, monkeypatch):
    from pathlib import Path as RealPath
    monkeypatch.setattr(RealPath, "home", staticmethod(lambda: tmp_path))
    result = execute_tool("create_reminder", None, {"title": "Buy milk", "due": "2026-01-01"})
    assert result["status"] == "created"
    reminder_file = tmp_path / "jarvis" / "data" / "reminders.json"
    assert reminder_file.exists()