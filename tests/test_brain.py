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


# ── build_digest ────────────────────────────────────────────────────────────

def test_build_digest_empty_window(store):
    b = _brain(store)
    # No memories in the window (store is empty) and no tasks -> static message
    with patch("jarvis.task_queue.TaskQueue"):
        text = b.build_digest(kind="morning_brief", hours=1)
    assert "No new activity" in text


def test_build_digest_uses_llm(store, monkeypatch):
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        b = _brain(store)
        b.remember("Planned the Round 4 architecture and wrote the plan.", source="manual")

    monkeypatch.setattr(
        "jarvis.brain._ollama_chat",
        lambda model, messages: {"message": {"content": "Here is your morning digest."}},
    )
    with patch("jarvis.task_queue.TaskQueue"):
        text = b.build_digest(kind="morning_brief", hours=24)
    assert "morning digest" in text


def test_build_digest_static_fallback(store, monkeypatch):
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        b = _brain(store)
        b.remember("Shipped the push queue with retry and backoff.", source="manual")

    # LLM returns empty -> static fallback lists the memory
    monkeypatch.setattr(
        "jarvis.brain._ollama_chat",
        lambda model, messages: {"message": {"content": ""}},
    )
    with patch("jarvis.task_queue.TaskQueue"):
        text = b.build_digest(kind="end_of_day", hours=24)
    assert "push queue" in text


def test_digest_model_env_resolution(monkeypatch, caplog):
    from jarvis.brain import DEFAULT_CHAT_MODEL, _digest_model
    monkeypatch.delenv("JARVIS_DIGEST_MODEL", raising=False)
    assert _digest_model() == DEFAULT_CHAT_MODEL
    monkeypatch.setenv("JARVIS_DIGEST_MODEL", "qwen2.5:3b")
    assert _digest_model() == "qwen2.5:3b"


def test_digest_model_warns_on_large_tier(monkeypatch):
    from jarvis.brain import _digest_model
    monkeypatch.setenv("JARVIS_DIGEST_MODEL", "qwen2.5:7b-instruct")
    caught = []

    class _L:
        def warning(self, *a, **k): caught.append(a)

    monkeypatch.setattr("jarvis.brain.logger", _L())
    _digest_model()
    assert caught and "large tier" in caught[0][0]


def test_build_digest_error_marker_falls_back(store, monkeypatch):
    """An '[ollama ...]' error string must NOT be returned as the digest — it
    must fall through to the static fallback (Round 9 digest RAM guard)."""
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        b = _brain(store)
        b.remember("Notes on the fallback behavior.", source="manual")

    monkeypatch.setattr(
        "jarvis.brain._ollama_chat",
        lambda model, messages: {"message": {"content": "[ollama connection error: down]"}},
    )
    with patch("jarvis.task_queue.TaskQueue"):
        text = b.build_digest(kind="morning_brief", hours=24)
    assert "fallback behavior" in text
    assert "[ollama" not in text


def test_build_digest_small_model_then_chat_fallback(store, monkeypatch):
    """With JARVIS_DIGEST_MODEL set, if the small model errors the digest must
    fall back to the chat model before static (never silently digest the error)."""
    calls = {"n": 0}
    _chat_args: list[str] = []

    def _chat(model, messages):
        calls["n"] += 1
        _chat_args.append(model)
        if calls["n"] == 1:  # small model fails
            return {"message": {"content": "[ollama connection error]"}}
        return {"message": {"content": f"digest via {model}"}}  # chat model wins

    monkeypatch.setenv("JARVIS_DIGEST_MODEL", "qwen2.5:3b")
    monkeypatch.setattr("jarvis.brain._ollama_chat", _chat)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        b = _brain(store)
        text = b.build_digest(kind="morning_brief", hours=24)
    assert calls["n"] == 2
    # first call used the small override, then fell back to the chat model
    assert _chat_args[0] == "qwen2.5:3b"
    assert _chat_args[1] == "test-model"
    assert "digest via" in text

def test_query_passes_history_into_messages(store, monkeypatch):
    """Threading: prior session turns must be injected between the system prompt
    and the new user query (Round 9 #6)."""
    import jarvis.brain as B
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8):
        b = _brain(store)
        seen = {}
        def _chat(model, messages):
            seen["m"] = messages
            return {"message": {"content": "ok"}}
        monkeypatch.setattr(B, "_ollama_chat", _chat)
        b.query("follow up?", history=[
            {"role": "user", "content": "first q"},
            {"role": "assistant", "content": "first a"},
        ])
    roles = [m["role"] for m in seen["m"]]
    contents = [m["content"] for m in seen["m"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "first q" in contents and "first a" in contents
    assert contents[-1] == "follow up?"


def test_query_injects_related_entities(store):
    fid = "mem-q1"
    store.add(fid, "manual", "1", "2026-01-01T10:00:00", "worked with alice on the plan", [], {}, [0.1] * 8)
    eid = store.get_or_create_entity("Alice Smith", entity_type="person")
    store.link_memory_entity(fid, eid)
    store.collection.query.return_value = {
        "documents": [["worked with alice"]], "ids": [[fid]],
        "metadatas": [[{"source": "manual"}]],
    }
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.brain._ollama_chat") as m_chat:
        m_chat.return_value = {"message": {"content": "answer"}}
        _, _mem = Brain(store).query("who did i work with?")
    sys_prompt = m_chat.call_args[1]["messages"][0]["content"]
    assert "RELATED ENTITIES" in sys_prompt
    assert "Alice Smith" in sys_prompt
# ── helper + new coverage (Round 9) ──────────────────────────────────────────

def test_messages_to_prompt_renders_roles():
    from jarvis.brain import _messages_to_prompt
    out = _messages_to_prompt([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "other", "content": "drop me"},
    ])
    assert "System: sys" in out and "User: u" in out and "Assistant: a" in out
    assert "drop me" not in out


def test_ollama_chat_success_and_error(monkeypatch):
    from jarvis import brain as B

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"response": "hi"}'

    monkeypatch.setattr("jarvis.brain.urllib.request.urlopen",
                        lambda req, timeout=180: _Resp())
    assert B._ollama_chat("m", [{"role": "user", "content": "x"}]) == \
        {"message": {"content": "hi"}}

    import urllib.error
    monkeypatch.setattr("jarvis.brain.urllib.request.urlopen",
                        lambda req, timeout=180: (_ for _ in ()).throw(
                            urllib.error.URLError("down")))
    out = B._ollama_chat("m", [])
    assert out["message"]["content"].startswith("[ollama connection error")


def test_confidence_levels(store):
    b = _brain(store)
    assert b._confidence([]) == "low"
    assert b._confidence([{"weight": 1.2}]) == "high"
    assert b._confidence([{"weight": 0.7}]) == "medium"
    assert b._confidence([{"weight": 0.3}]) == "low"


def test_chat_substantive_path(store, monkeypatch):
    import jarvis.brain as B
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        b = _brain(store)
        b.remember("Alice likes hiking on weekends.", source="manual")

    monkeypatch.setattr(B, "_ollama_chat",
                        lambda model, messages: {"message": {"content": "answer from memory"}})
    session: list = []
    answer, _mems, _n = b.chat(session, "What does Alice like doing?")
    assert "answer from memory" in answer
    assert session[0]["role"] == "user"
    assert session[-1]["role"] == "assistant"


def test_chat_non_substantive_is_noted(store):
    b = _brain(store)
    session: list = []
    answer, mems, n = b.chat(session, "ok")
    assert answer == "Noted."
    assert mems == [] and n == 0


def test_save_session_gates_on_turn_count(store):
    b = _brain(store)
    short = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert b.save_session(short) is False
    long_session = []
    for i in range(3):
        long_session += [{"role": "user", "content": f"q{i}"},
                         {"role": "assistant", "content": f"a{i}"}]
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8):
        assert b.save_session(long_session) is True


def test_get_recent_activity_returns_rows(store):
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        b = _brain(store)
        b.remember("recent activity marker", source="manual")
    act = b.get_recent_activity(hours=24, limit=20)
    assert any("recent activity marker" in r["content"] for r in act)


def test_remember_with_classify(store, monkeypatch):
    seen = []
    monkeypatch.setattr("jarvis.routes.classify_existing",
                        lambda store, memory: seen.append(memory["id"]) or {"route": "escalate"})
    b = _brain(store)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        added = b.remember("classify me please", source="manual", classify=True)
    assert added >= 1
    assert seen  # classify_existing was invoked


def test_classify_memory_found_and_missing(store, monkeypatch):
    b = _brain(store)
    assert b.classify_memory("does-not-exist") == {}
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        b.remember("a specific memory id mxyz", source="manual")
    row = store.conn.execute("SELECT id FROM memories WHERE content LIKE '%mxyz%'").fetchone()
    out = b.classify_memory(row["id"])
    assert out  # non-empty dict returned


def test_correct_nonexistent_id_still_adds(store):
    b = _brain(store)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8):
        added = b.correct("ghost-id", "this corrects a missing memory")
    assert added >= 1


def test_upgrade_adds_and_appends(store, tmp_path, monkeypatch):
    """`upgrade` stores a memory and appends to UPGRADES.md. (Note: the memory
    fingerprint includes a fresh timestamp, so repeated calls are distinct —
    dedupe only applies to an identical call within the same microsecond.)"""
    from jarvis import paths as P
    upgrades = tmp_path / "UPGRADES.md"
    upgrades.write_text("", encoding="utf-8")
    monkeypatch.setattr(P, "config_file", lambda *a: upgrades)
    b = _brain(store)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8):
        first = b.upgrade("Add a rocket mode", status="requested")
    assert first >= 1
    assert "rocket mode" in upgrades.read_text(encoding="utf-8")


def test_ollama_chat_honors_ollama_host_env(monkeypatch):
    """Round 9 #7: _ollama_chat must honor OLLAMA_HOST/PORT env (lets a thin
    client reach the box's Ollama for out-of-band digest previews)."""
    from jarvis import brain as B

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"response": "x"}'

    captured = {}
    def _fake(req, timeout=180):
        captured["u"] = req.full_url
        return _Resp()
    monkeypatch.setenv("OLLAMA_HOST", "100.102.0.99")
    monkeypatch.setenv("OLLAMA_PORT", "11434")
    monkeypatch.setattr("jarvis.brain.urllib.request.urlopen", _fake)
    B._ollama_chat("m", [{"role": "user", "content": "x"}])
    assert captured["u"] == "http://100.102.0.99:11434/api/generate"

# ── model-tier routing (fast/medium/big) ─────────────────────────────────────

def test_tier_for_routes_by_complexity():
    from jarvis.brain import _tier_for
    assert _tier_for("hello") == "fast"
    assert _tier_for("hi there") == "fast"
    assert _tier_for("how are you") == "fast"
    assert _tier_for("thanks a lot") == "fast"
    # recall questions (about the user's data) never use the fast toy model
    assert _tier_for("what did I do yesterday") == "medium"
    assert _tier_for("what did I work on yesterday morning") == "medium"
    assert _tier_for("explain how the sync protocol works") == "big"
    assert _tier_for("please analyze and compare the two architectures in detail why does a work better than b") == "big"


def test_select_model_for_uses_env_tiers(monkeypatch):
    from jarvis.brain import select_model_for
    monkeypatch.setenv("JARVIS_CHAT_MODEL", "medium-model")
    monkeypatch.setenv("JARVIS_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("JARVIS_CHAT_MODEL_BIG", "big-model")
    assert select_model_for("hello") == "fast-model"
    assert select_model_for("what did I work on yesterday morning") == "medium-model"
    assert select_model_for("explain the architecture") == "big-model"
    # force a tier
    assert select_model_for("hello", force="big") == "big-model"
    # recall escalates away from a forced fast tier
    assert select_model_for("what did I do yesterday", force="fast") == "medium-model"
    # exact model id override is always respected
    assert select_model_for("hello", force="qwen2.5:7b") == "qwen2.5:7b"
    assert select_model_for("what did I do yesterday", force="qwen2.5:7b") == "qwen2.5:7b"


def test_tier_model_falls_back_to_medium(monkeypatch):
    from jarvis.brain import tier_model
    monkeypatch.setenv("JARVIS_CHAT_MODEL", "medium-model")
    monkeypatch.delenv("JARVIS_CHAT_MODEL_FAST", raising=False)
    monkeypatch.delenv("JARVIS_CHAT_MODEL_BIG", raising=False)
    assert tier_model("fast") == "medium-model"
    assert tier_model("big") == "medium-model"
    assert tier_model("medium") == "medium-model"
