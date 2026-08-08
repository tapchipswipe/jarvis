"""
Tests for jarvis/agent.py

Covers:
  - Module imports without error
  - _ollama_chat fallback on connection error
  - _extract_text / _parse_tool_call helpers
  - _inject_rag_context returns string
  - run_turn handles missing Ollama gracefully (returns error message, never raises)
  - tools never raise (via execute_tool safety wrapper)
  - SessionDB session creation/appending
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from jarvis.agent import (
    MAX_STEPS,
    SYSTEM_PROMPT,
    _extract_text,
    _inject_rag_context,
    _ollama_chat,
    _parse_tool_call,
    run_turn,
)
from jarvis.sessions import SessionDB
from jarvis.tools import TOOLS, TOOLS_SCHEMA, execute_tool

# ── Import / constants ────────────────────────────────────────────────────────

def test_constants():
    assert MAX_STEPS == 8
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT) > 0


def test_tools_schema_populated():
    assert len(TOOLS_SCHEMA) > 0
    tool_names = [s["function"]["name"] for s in TOOLS_SCHEMA]
    assert "search_memories" in tool_names
    assert "check_calendar" in tool_names
    assert "get_entity_context" in tool_names
    assert "summarize" in tool_names


# ── _extract_text ─────────────────────────────────────────────────────────────

def test_extract_text_from_content():
    resp = {"message": {"content": "Hello world"}}
    assert _extract_text(resp) == "Hello world"


def test_extract_text_from_chunks():
    resp = {"message": {"content": ""}, "_chunks": [
        {"message": {"content": "Hello "}, "done": False},
        {"message": {"content": "world"}, "done": True},
    ]}
    assert _extract_text(resp) == "Hello world"


def test_extract_text_empty():
    resp = {"message": {}}
    assert _extract_text(resp) == ""


# ── _parse_tool_call ──────────────────────────────────────────────────────────

def test_parse_tool_call_present():
    resp = {"message": {"content": "Let me check", "tool_calls": [
        {"name": "search_memories", "arguments": {"query": "test"}}
    ]}}
    name, args = _parse_tool_call(resp)
    assert name == "search_memories"
    assert args == {"query": "test"}


def test_parse_tool_call_absent():
    resp = {"message": {"content": "No tools here"}}
    assert _parse_tool_call(resp) is None


def test_parse_tool_call_empty_tool_calls():
    resp = {"message": {"content": "ok", "tool_calls": []}}
    assert _parse_tool_call(resp) is None


# ── _ollama_chat ──────────────────────────────────────────────────────────────

def test_ollama_chat_connection_error():
    """When Ollama is unreachable, _ollama_chat should return an error message, not raise."""
    import urllib.error
    with patch("jarvis.agent.urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        result = _ollama_chat("test-model", [{"role": "user", "content": "hi"}], stream=False)
    assert "message" in result
    assert "error" in result["message"]["content"].lower()


def test_ollama_chat_non_streaming():
    response_data = {"message": {"content": "Hello from Ollama"}}
    cm = MagicMock()
    resp = MagicMock()
    resp.read.return_value = json.dumps(response_data).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    cm.__enter__ = MagicMock(return_value=resp)
    cm.__exit__ = MagicMock(return_value=False)

    with patch("jarvis.agent.urllib.request.urlopen", return_value=cm):
        result = _ollama_chat("test-model", [{"role": "user", "content": "hi"}], stream=False)
    assert result["message"]["content"] == "Hello from Ollama"


# ── _inject_rag_context ───────────────────────────────────────────────────────

def test_inject_rag_context_returns_string():
    with patch("jarvis.agent.Store") as mock_store_cls:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_store.search.return_value = []
        result = _inject_rag_context(MagicMock(), "test query")
    assert isinstance(result, str)


def test_inject_rag_context_with_results():
    with patch("jarvis.agent.Store") as mock_store_cls:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_store.search.return_value = [
            {"source": "email", "timestamp": "2025-01-01", "content": "Test content"}
        ]
        result = _inject_rag_context(MagicMock(), "test query")
    assert "RELEVANT MEMORIES" in result
    assert "Test content" in result


def test_inject_rag_context_reuses_provided_store():
    """A caller-provided store must be reused, never replaced or closed."""
    provided_store = MagicMock()
    provided_store.search.return_value = [
        {"source": "email", "timestamp": "2025-01-01", "content": "Reused content"}
    ]
    with patch("jarvis.agent.Store") as mock_store_cls:
        result = _inject_rag_context(
            MagicMock(), "test query", store=provided_store
        )
    # No new Store was constructed.
    mock_store_cls.assert_not_called()
    # The provided store was used and NOT closed by _inject_rag_context.
    provided_store.search.assert_called_once()
    provided_store.close.assert_not_called()
    assert "Reused content" in result


def test_inject_rag_context_opens_and_closes_own_store_when_absent():
    """Without a provided store, _inject_rag_context opens and closes its own."""
    with patch("jarvis.agent.Store") as mock_store_cls:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_store.search.return_value = []
        result = _inject_rag_context(MagicMock(), "test query")
    mock_store_cls.assert_called_once_with()
    assert result == ""


# ── execute_tool (safety wrapper) ─────────────────────────────────────────────

def test_execute_tool_unknown_returns_error():
    result = execute_tool("nonexistent_tool", MagicMock(), {})
    assert "error" in result


def test_execute_tool_never_raises():
    """All registered tools should return a dict, never raise."""
    mock_store = MagicMock()
    for name, tool_def in TOOLS.items():
        try:
            result = tool_def["fn"](mock_store, {})
            assert isinstance(result, dict)
        except Exception as e:
            pytest.fail(f"Tool '{name}' raised: {e}")


# ── SessionDB ─────────────────────────────────────────────────────────────────

def test_sessiondb_create_and_get(tmp_path):
    db_path = tmp_path / "sessions.db"
    sdb = SessionDB(db_path=str(db_path))
    try:
        sid = sdb.create_session(title="Test Session")
        assert sid is not None
        session = sdb.get_session(sid)
        assert session is not None
        assert session["title"] == "Test Session"
    finally:
        sdb.close()


def test_sessiondb_append_and_retrieve_messages(tmp_path):
    db_path = tmp_path / "sessions.db"
    sdb = SessionDB(db_path=str(db_path))
    try:
        sid = sdb.create_session(title="Chat")
        sdb.append_message(sid, "user", "Hello")
        sdb.append_message(sid, "assistant", "Hi there")
        messages = sdb.get_messages(sid)
        assert len(messages) >= 2
        assert messages[-2]["role"] == "user"
        assert messages[-2]["content"] == "Hello"
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "Hi there"
    finally:
        sdb.close()


def test_sessiondb_list_sessions(tmp_path):
    db_path = tmp_path / "sessions.db"
    sdb = SessionDB(db_path=str(db_path))
    try:
        sdb.create_session(title="Session 1")
        sdb.create_session(title="Session 2")
        sessions = sdb.list_sessions()
        assert len(sessions) == 2
    finally:
        sdb.close()


def test_sessiondb_persists_across_reopen(tmp_path):
    db_path = tmp_path / "sessions.db"
    sdb = SessionDB(db_path=str(db_path))
    sid = sdb.create_session(title="Persistent")
    sdb.append_message(sid, "user", "Hello")
    sdb.close()

    sdb2 = SessionDB(db_path=str(db_path))
    try:
        session = sdb2.get_session(sid)
        assert session is not None
        assert session["title"] == "Persistent"
        messages = sdb2.get_messages(sid)
        assert len(messages) >= 2
    finally:
        sdb2.close()


# ── run_turn (integration) ────────────────────────────────────────────────────

def test_run_turn_no_ollama_returns_error():
    """run_turn should return an error message string, not raise, when Ollama is down."""
    import urllib.error
    db_path = "/tmp/test_jarvis_agent_sessions.db"
    # Clean up any leftover
    if os.path.exists(db_path):
        os.remove(db_path)
    sdb = SessionDB(db_path=db_path)
    try:
        sid = sdb.create_session(title="Test")
        with patch("jarvis.agent.urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            text, tool_log = run_turn("Hello", sid, max_steps=2)
        assert isinstance(text, str)
        assert len(tool_log) == 0
    finally:
        sdb.close()
        if os.path.exists(db_path):
            os.remove(db_path)
