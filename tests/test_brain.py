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


def test_query_synthesizes_retrieval_from_history(store, monkeypatch):
    """A follow-up with no topical signal must still embed/search with the
    thread's subject — the retrieval query is synthesized from prior user turns
    while the raw query stays in the final prompt (task_0038)."""
    import jarvis.brain as B
    seen = {}

    def _emb(text):
        seen["q"] = text
        return [0.1] * 8

    def _chat(model, messages):
        seen["prompt_q"] = messages[-1]["content"]
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(B, "get_embedding", _emb)
    monkeypatch.setattr(B, "_ollama_chat", _chat)
    b = _brain(store)
    b.query("and what about them?", history=[
        {"role": "user", "content": "Tell me about the rocket launch"},
        {"role": "assistant", "content": "It was delayed until Friday."},
    ])
    # Embedding used a synthesized query carrying the thread's topic...
    assert seen.get("q") is not None
    assert "rocket launch" in seen["q"]
    assert "and what about them?" in seen["q"]
    # ...but the raw follow-up remains the final prompt to the model.
    assert seen["prompt_q"] == "and what about them?"


def test_query_no_history_embeds_raw_query(store, monkeypatch):
    """Without history the retrieval query is exactly the raw user query."""
    import jarvis.brain as B
    seen = {}

    def _emb(text):
        seen["q"] = text
        return [0.1] * 8

    monkeypatch.setattr(B, "get_embedding", _emb)
    monkeypatch.setattr(
        B, "_ollama_chat", lambda model, messages: {"message": {"content": "ok"}}
    )
    b = _brain(store)
    b.query("just a standalone question")
    assert seen.get("q") == "just a standalone question"


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


def test_confidence_freshness_boosts_borderline(store):
    """A barely-medium memory that is freshly captured earns a modest boost,
    while an identical stale one does not. Memories without a usable timestamp
    are treated as stale, so legacy rows keep weight-only confidence."""
    from datetime import datetime, timezone
    b = _brain(store)
    fresh_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    stale_ts = "2025-01-01T10:00:00"
    # weight 0.55 is below the 0.6 medium threshold; the +0.1 freshness nudge
    # lifts a fresh memory to medium while the stale one stays low.
    fresh = b._confidence([{"weight": 0.55, "timestamp": fresh_ts}])
    stale = b._confidence([{"weight": 0.55, "timestamp": stale_ts}])
    assert fresh == "medium"
    assert stale == "low"


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


# ── stable-fingerprint dedup (task_0024) ─────────────────────────────────────

def _row_count(store):
    return store.conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]


def test_save_session_dedups_on_resave(store):
    """Re-saving the same session must produce no duplicate (dedup fires)."""
    b = _brain(store)
    session = [
        {"role": "user", "content": "What did I work on yesterday?"},
        {"role": "assistant", "content": "You worked on the sync protocol."},
        {"role": "user", "content": "And what is next on the list?"},
        {"role": "assistant", "content": "Consolidation is next."},
        {"role": "user", "content": "Thanks, that's all."},
    ]
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8):
        assert b.save_session(session) is True
        first = _row_count(store)
        assert first >= 1
        assert b.save_session(session) is False  # dedup fires -> no re-add
        assert _row_count(store) == first


def test_correct_dedups_on_reapply(store):
    """Re-applying the same correction to the same memory produces no duplicate."""
    b = _brain(store)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        b.remember("Original statement that needs fixing.", source="manual")
    orig_id = store.conn.execute(
        "SELECT id FROM memories WHERE content LIKE '%needs fixing%'"
    ).fetchone()["id"]
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8):
        first = b.correct(orig_id, "This is the corrected fact.")
        assert first >= 1
        before = _row_count(store)
        second = b.correct(orig_id, "This is the corrected fact.")
        assert second == 0  # dedup fires -> no new correction chunks
        assert _row_count(store) == before


def test_upgrade_dedups_on_relog(store, tmp_path, monkeypatch):
    """Re-logging the same feature request produces no duplicate and does not
    re-append to UPGRADES.md."""
    from jarvis import paths as P
    upgrades = tmp_path / "UPGRADES.md"
    upgrades.write_text("", encoding="utf-8")
    monkeypatch.setattr(P, "config_file", lambda *a: upgrades)
    b = _brain(store)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8):
        first = b.upgrade("Add a teleport button", status="requested")
        assert first >= 1
        before = _row_count(store)
        first_lines = len(upgrades.read_text(encoding="utf-8").splitlines())
        second = b.upgrade("Add a teleport button", status="requested")
        assert second == 0  # dedup fires -> no new upgrade memory
        assert _row_count(store) == before
        assert len(upgrades.read_text(encoding="utf-8").splitlines()) == first_lines


# ── graph linking for manual/session/consolidated memories (task_0037) ───────

def _linked_entity_ids(store, entity_name):
    """Return the memory_ids linked to the entity with the given name."""
    eid = store.get_or_create_entity(entity_name, entity_type="person")
    if eid is None:
        return []
    rows = store.conn.execute(
        "SELECT memory_id FROM memory_entities WHERE entity_id = ?", (eid,)
    ).fetchall()
    return [r["memory_id"] for r in rows]


def test_remember_links_entities_into_graph(store):
    """A manual memory mentioning a known entity must surface it in the graph."""
    from jarvis.graph import get_entity_timeline
    b = _brain(store)
    text = "Worked with Alice Smith and Jane Doe on the roadmap."
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        added = b.remember(text, source="manual")
        assert added >= 1
    # entity exists and is linked to at least one stored chunk
    assert _linked_entity_ids(store, "Alice Smith"), "Alice Smith not linked"
    # timeline query surfaces the manual memory
    eid = store.get_or_create_entity("Alice Smith", entity_type="person")
    timeline = get_entity_timeline(store, eid)
    assert timeline
    assert timeline[0]["source"] == "manual"
    assert "Alice Smith" in timeline[0]["content"]


def test_save_session_links_entities_into_graph(store):
    """A session mentioning entities must surface them via get_related."""
    from jarvis.graph import get_related
    b = _brain(store)
    session = [
        {"role": "user", "content": "What did Alice Smith and Jane Doe decide?"},
        {"role": "assistant", "content": "They agreed on the launch date."},
        {"role": "user", "content": "Great, please schedule the kickoff."},
        {"role": "assistant", "content": "Scheduled for Friday."},
        {"role": "user", "content": "Thanks, that covers it."},
    ]
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8):
        assert b.save_session(session) is True
    assert _linked_entity_ids(store, "Alice Smith"), "Alice Smith not linked"
    assert _linked_entity_ids(store, "Jane Doe"), "Jane Doe not linked"
    # co-participant edge inferred -> get_related surfaces the other entity
    alice_id = store.get_or_create_entity("Alice Smith", entity_type="person")
    related = get_related(store, alice_id, depth=1)
    names = {r["entity_name"] for r in related}
    assert "Jane Doe" in names


def test_remember_graph_linking_is_best_effort(store, monkeypatch):
    """Graph failures during remember() must not fail the memory write."""
    b = _brain(store)
    text = "Worked with Bob Carter on delivery."
    # Force entity extraction to raise inside the helper; the helper's own
    # best-effort try/except must swallow it and keep the memory write intact.
    import jarvis.extract_entities as EE
    def _boom(*a, **k):
        raise RuntimeError("graph down")
    monkeypatch.setattr(EE, "extract_entities", _boom)
    with patch("jarvis.brain.get_embedding", return_value=[0.1] * 8), \
         patch("jarvis.extract.extract_metadata", return_value={"tags": [], "entities": []}):
        added = b.remember(text, source="manual")
        assert added >= 1  # memory still stored despite graph failure
