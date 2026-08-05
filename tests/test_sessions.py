"""
Tests for jarvis/sessions.py

Covers:
  - Session create / append / get / list / update / close
  - tool_calls round-tripping through JSON
"""
from __future__ import annotations

from jarvis.sessions import SessionDB


def _make(tmp_path):
    return SessionDB(db_path=tmp_path / "sessions.db")


def test_create_session(tmp_path):
    db = _make(tmp_path)
    try:
        sid = db.create_session(title="Chat One")
        sess = db.get_session(sid)
        assert sess is not None
        assert sess["title"] == "Chat One"
        assert sess["tier"] == "raw"
    finally:
        db.close()


def test_append_and_get_messages(tmp_path):
    db = _make(tmp_path)
    try:
        sid = db.create_session(title="T")
        db.append_message(sid, "user", "hello")
        db.append_message(sid, "assistant", "hi there")
        msgs = db.get_messages(sid)
        assert len(msgs) == 3  # includes the "Session started" opener
        roles = [m["role"] for m in msgs]
        assert roles == ["assistant", "user", "assistant"]
        assert msgs[-1]["content"] == "hi there"
    finally:
        db.close()


def test_tool_calls_roundtrip(tmp_path):
    db = _make(tmp_path)
    try:
        sid = db.create_session(title="T")
        calls = [{"name": "search_memories", "arguments": {"query": "x"}}]
        db.append_message(sid, "assistant", "", tool_calls=calls)
        msgs = db.get_messages(sid)
        last = msgs[-1]
        assert last["tool_calls"] == calls
    finally:
        db.close()


def test_list_sessions_orders_by_update(tmp_path):
    db = _make(tmp_path)
    try:
        a = db.create_session(title="A")
        b = db.create_session(title="B")
        # Touch A so it is most recently updated
        db.append_message(a, "user", "new activity")
        sids = [s["id"] for s in db.list_sessions()]
        assert sids.index(a) < sids.index(b)
    finally:
        db.close()


def test_update_session(tmp_path):
    db = _make(tmp_path)
    try:
        sid = db.create_session(title="T")
        db.update_session(sid, summary="A summary", tier="session")
        sess = db.get_session(sid)
        assert sess["summary"] == "A summary"
        assert sess["tier"] == "session"
    finally:
        db.close()


def test_get_missing_session(tmp_path):
    db = _make(tmp_path)
    try:
        assert db.get_session("does-not-exist") is None
    finally:
        db.close()