"""
Tests for jarvis/brain.py

Covers:
  - _is_substantive trivial-message filtering
  - remember stores chunks (network calls mocked)
  - correct marks original superseded and stores a correction
  - query returns a response and memory list (Ollama mocked)
  - save_session gates on turn count
"""
from __future__ import annotations

from unittest.mock import patch

from jarvis.brain import Brain


def _brain(store):
    return Brain(store, model="test-model")


def test_is_substantive():
    b = Brain(store=None)
    assert b._is_substantive("hello") is False
    assert b._is_substantive("thanks") is False
    assert b._is_substantive("hi") is False
    assert b._is_substantive("abc") is False       # < 5 chars
    assert b._is_substantive("Tell me about my week") is True
    assert b._is_substantive("I need to buy milk") is True


def test_remember_stores_chunks(store):
    b = _brain(store)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": ["test"], "entities": []}):
        added = b.remember("A sufficiently long memory about the project goals.", source="manual", tags=["alpha"])
        assert added >= 1
        rows = store.conn.execute(
            "SELECT * FROM memories WHERE superseded = 0"
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0]["source"] == "manual"


def test_remember_applies_tags(store):
    b = _brain(store)
    text = "Some longer memory text for tagging tests to run."
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": ["auto"], "entities": []}):
        # No explicit tags -> auto-extraction runs and supplies tags
        b.remember(text, source="manual")
        rows = store.get_recent_raw(hours=24, limit=5)
        assert rows
        import json
        tags = json.loads(rows[0]["tags"])
        assert "auto" in tags


def test_correct_marks_superseded(store):
    b = _brain(store)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        added = b.remember("Original statement that is now wrong.", source="manual")
        assert added >= 1
        orig_id = store.conn.execute(
            "SELECT id FROM memories WHERE superseded = 0 LIMIT 1"
        ).fetchone()["id"]
        b.correct(orig_id, "Corrected statement.")
        # Original should be superseded
        row = store.conn.execute("SELECT superseded FROM memories WHERE id = ?", (orig_id,)).fetchone()
        assert row["superseded"] == 1


def test_query_returns_response(store):
    b = _brain(store)
    fake_resp = {"message": {"content": "Here is the answer"}}
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.brain._ollama_chat", return_value=fake_resp):
        answer, memories = b.query("What happened last week?")
        assert "Here is the answer" in answer
        assert isinstance(memories, list)


def test_query_verbose_annotates_confidence(store):
    b = _brain(store)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.brain._ollama_chat", return_value={"message": {"content": "answer"}}):
        answer, _ = b.query("A question", verbose=True)
        assert "[confidence:" in answer


def test_save_session_requires_three_turns(store):
    b = _brain(store)
    short = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert b.save_session(short) is False