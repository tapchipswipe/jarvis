"""Regression tests for the jarvis CLI.

Covers the builtin-``list`` shadowing bug that broke ``remember`` when the
``task list`` click command was introduced.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner


def _fake_classify_rows():
    rows = [
        {"id": "mx1", "content": "idea: a thing", "route": None, "source_id": "s1"},
        {"id": "mx2", "content": "reference: another", "route": None, "source_id": "s2"},
    ]
    return rows


def test_classify_forwards_dry_run(monkeypatch):
    """`classify --dry-run` must forward dry_run=True to classify_existing."""
    import jarvis.cli as cli_module
    from jarvis.cli import cli

    seen = {}
    fake_store = MagicMock()
    fake_store.conn.execute.return_value.fetchone.return_value = {
        "id": "mx1", "content": "idea: a thing", "route": None, "source_id": "s1",
    }
    monkeypatch.setattr(cli_module, "Store", lambda *a, **k: fake_store)
    monkeypatch.setattr(
        cli_module, "classify_existing",
        lambda store, memory, model=None, dry_run=False: (
            seen.update(memory_id=memory["id"], dry_run=dry_run) or
            {"route": "idea_capture", "confidence": "high", "escalate_reason": None,
             "action_atom": None, "target_list": None, "tag_seeds": ["idea"]}))

    result = CliRunner().invoke(cli, ["classify", "mx1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert seen["memory_id"] == "mx1"
    assert seen["dry_run"] is True
    assert "dry-run" in result.output


def test_classify_not_dry_run_by_default(monkeypatch):
    """`classify` without --dry-run must forward dry_run=False."""
    import jarvis.cli as cli_module
    from jarvis.cli import cli

    seen = {}
    fake_store = MagicMock()
    fake_store.conn.execute.return_value.fetchone.return_value = {
        "id": "mx1", "content": "idea: a thing", "route": None, "source_id": "s1",
    }
    monkeypatch.setattr(cli_module, "Store", lambda *a, **k: fake_store)
    monkeypatch.setattr(
        cli_module, "classify_existing",
        lambda store, memory, model=None, dry_run=False: (
            seen.update(memory_id=memory["id"], dry_run=dry_run) or
            {"route": "idea_capture", "confidence": "high", "escalate_reason": None,
             "action_atom": None, "target_list": None, "tag_seeds": ["idea"]}))

    result = CliRunner().invoke(cli, ["classify", "mx1"])
    assert result.exit_code == 0, result.output
    assert seen["dry_run"] is False
    assert "Applied: yes" in result.output


def test_classify_recent_forwards_dry_run(monkeypatch):
    """`classify_recent --dry-run` must forward dry_run=True to classify_existing."""
    import jarvis.cli as cli_module
    from jarvis.cli import cli

    seen = {"calls": 0}
    fake_store = MagicMock()
    fake_store.get_unclassified.return_value = _fake_classify_rows()
    monkeypatch.setattr(cli_module, "Store", lambda *a, **k: fake_store)

    def spy(store, memory, model=None, dry_run=False):
        seen["calls"] += 1
        seen["dry_run"] = dry_run
        return {"route": "idea_capture", "confidence": "high", "escalate_reason": None,
                "action_atom": None, "target_list": None, "tag_seeds": ["idea"]}

    monkeypatch.setattr(cli_module, "classify_existing", spy)

    result = CliRunner().invoke(cli, ["classify-recent", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert seen["calls"] == 2
    assert seen["dry_run"] is True


def test_remember_does_not_invoke_task_list(monkeypatch):
    """Regression: ``remember`` must store/confirm, not run the ``task list`` command.

    The ``task list`` command's ``def list`` shadowed the builtin ``list`` and was
    rebound to a click Command at module level, so ``list(tag)`` inside
    ``remember`` invoked the task-list command (printing the task table and
    exiting via SystemExit) instead of converting ``tag`` to a list in the local
    store path.

    Round 7 note: the Mac thin client persists ``JARVIS_MODE=client``, so
    ``remote.is_remote()`` can return True in an ambient environment even though
    this test unit-tests the local store path. We pin ``is_remote`` to False so the
    local branch is exercised deterministically regardless of ambient env.
    """
    from click.testing import CliRunner

    from jarvis.cli import cli

    fake_store = MagicMock()
    fake_brain = MagicMock(remember=MagicMock(return_value=1))
    monkeypatch.setattr("jarvis.cli.Store", lambda *a, **k: fake_store)
    monkeypatch.setattr("jarvis.cli.Brain", lambda *a, **k: fake_brain)
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)

    result = CliRunner().invoke(cli, ["remember", "regression check"])

    assert result.exit_code == 0
    assert "Remembered 1 chunk(s)." in result.output
    assert "Status" not in result.output


def test_remember_remote_path_does_not_invoke_task_list(monkeypatch):
    """The thin-client path uses ``list(tag)`` for tags too — it must NOT hit the
    ``task list`` click command either, and must report the remote capture line."""
    from click.testing import CliRunner

    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.enqueue.return_value = 1
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr(
        "jarvis.cache.flush_outbox",
        lambda cache, limit=200: {"pushed": 1, "failed": 0, "offline": False},
    )

    result = CliRunner().invoke(cli, ["remember", "regression check"])

    assert result.exit_code == 0
    assert "Captured 1 memory to Jarvis to server." in result.output
    assert "Status" not in result.output


def test_search_offline_falls_back_to_cached_tail(monkeypatch):
    import urllib.error

    from jarvis import remote
    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.store_tail.return_value = None
    fake_cache.tail_search.return_value = [
        {"id": "c1", "source": "file", "timestamp": "2026-01-01T00:00:00",
         "content": "cached snippet about offline search", "tags": []},
    ]
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr(remote, "search", lambda *a, **k: (_ for _ in ()).throw(
        urllib.error.URLError("host down")))

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["search", "offline"])
    assert result.exit_code == 0
    assert "Offline (cached subset)" in result.output
    assert "[stale]" in result.output
    fake_cache.tail_search.assert_called_once()


def test_search_surfaces_server_error_not_offline(monkeypatch):
    import io
    import urllib.error

    from jarvis import remote
    from jarvis.cli import cli

    fake_cache = MagicMock()
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    # a reachable server that rejects us (e.g. bad token after Round 3 guards)
    body = io.BytesIO(b'{"error": "forbidden"}')
    err = urllib.error.HTTPError("http://box/api/search", 403, "Forbidden",
                                 {}, body)
    monkeypatch.setattr(remote, "search", lambda *a, **k: (_ for _ in ()).throw(err))

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["search", "hello"])
    assert result.exit_code == 0
    assert "Server error (403)" in result.output
    assert "forbidden" in result.output
    assert "Offline" not in result.output


def test_search_json_output(monkeypatch):
    """--json emits structured JSON (memories + count), not the human table."""
    import json

    from jarvis import remote
    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.store_tail.return_value = None
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr(remote, "search",
                        lambda query, n=10, source=None: {
                            "count": 1,
                            "memories": [{"id": "b1", "content": "json search hit body",
                                          "source": "deep", "tier": "raw",
                                          "timestamp": "2026-01-01T00:00:00"}],
                            "entities": {},
                        })

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["search", "jsonquery", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["count"] == 1
    assert data["memories"][0]["id"] == "b1"
    assert "--" not in result.output.split("\n", 1)[0]  # not the human header


def test_status_reports_live_box_in_client_mode(monkeypatch):
    from jarvis import remote
    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.pending_count.return_value = 0
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr(remote, "health_deep",
                        lambda: {"ok": True, "memories": 3954, "mode": "local", "uptime": 600})

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "Box (thin client): ok=True memories=3954 mode=local" in result.output
    assert "Outbox: 0 pending." in result.output


def test_status_offline_reports_box_unreachable(monkeypatch):
    import urllib.error

    from jarvis import remote
    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.pending_count.return_value = 3
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr(remote, "health_deep", lambda *a, **k: (_ for _ in ()).throw(
        urllib.error.URLError("down")))

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "Box unreachable" in result.output


def test_ingest_status_reports_box_progress(monkeypatch):
    from jarvis import remote
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "ingest_status",
                        lambda: {"active": True, "enabled": True, "processed": 250,
                                 "added": 248, "remaining": 5208, "done": False,
                                 "inbox": "C:/data/jarvis/inbox"})

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["ingest-status"])
    assert result.exit_code == 0
    assert "Active=True" in result.output
    assert "remaining=5208" in result.output
    assert "C:/data/jarvis/inbox" in result.output


def test_ingest_status_needs_client_mode(monkeypatch):
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)
    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["ingest-status"])
    assert result.exit_code == 0
    assert "requires thin-client mode" in result.output


def test_doctor_reports_checks(monkeypatch):
    from jarvis import remote
    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.pending_count.return_value = 0
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr(remote, "is_remote", lambda: True)
    monkeypatch.setattr(remote, "health_deep",
                        lambda: {"ok": True, "memories": 3954, "mode": "local", "uptime": 100})
    monkeypatch.setattr(remote, "ingest_status",
                        lambda: {"active": True, "remaining": 5208})

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 0
    assert "PASS] mode" in result.output or "WARN] mode" in result.output
    assert "[PASS] box" in result.output
    assert "memories=3954" in result.output
    assert "ingest" in result.output
    assert "git" in result.output  # bot/main/origin sync line is present


def test_doctor_local_mode_no_box(monkeypatch):
    from jarvis import remote
    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.pending_count.return_value = 0
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr(remote, "is_remote", lambda: False)

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 0
    assert "local mode (no box to probe)" in result.output


def test_flush_client_mode(monkeypatch):
    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.pending_count.return_value = 3
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr("jarvis.cache.flush_outbox",
                        lambda cache, limit=200: {"pushed": 3, "failed": 0, "offline": False})

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["flush"])
    assert result.exit_code == 0
    assert "Pushed 3 memory(-ies)" in result.output


def test_flush_offline(monkeypatch):
    from jarvis.cli import cli

    fake_cache = MagicMock()
    fake_cache.pending_count.return_value = 5
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.cache.Cache", lambda *a, **k: fake_cache)
    monkeypatch.setattr("jarvis.cache.flush_outbox",
                        lambda cache, limit=200: {"pushed": 0, "failed": 0, "offline": True})

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["flush"])
    assert result.exit_code == 0
    assert "Server unreachable" in result.output
    assert "5 item(s) stay queued" in result.output


def test_flush_needs_client_mode(monkeypatch):
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)
    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["flush"])
    assert result.exit_code == 0
    assert "Not in client mode" in result.output


def test_memories_client_mode(monkeypatch):
    from jarvis import remote
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "memories", lambda **kw: {
        "count": 1,
        "memories": [{"id": "b1", "content": "a recent memory about the fence install",
                      "source": "deep", "timestamp": "2026-01-01T00:00:00",
                      "tier": "raw", "tags": ["deep"], "route": "unclassified"}],
    })

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["memories"])
    assert result.exit_code == 0
    assert "[raw] [deep] 2026-01-01T00:00:00 deep" in result.output
    assert "id=b1" in result.output


def test_memories_client_mode_tag_filter(monkeypatch):
    from jarvis import remote
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "memories", lambda **kw: {
        "count": 2,
        "memories": [
            {"id": "a", "content": "alpha", "source": "s", "timestamp": "2026-01-01T00:00:00",
             "tier": "raw", "tags": ["deep"], "route": "x"},
            {"id": "b", "content": "beta", "source": "s", "timestamp": "2026-01-02T00:00:00",
             "tier": "raw", "tags": ["manual"], "route": "y"},
        ],
    })

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["memories", "--tag", "deep"])
    assert result.exit_code == 0
    assert "id=a" in result.output
    assert "id=b" not in result.output


def test_timeline_client_mode(monkeypatch):
    from jarvis import remote
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    captured = {}
    def _memories(**kw):
        captured.update(kw)
        return {"count": 1, "memories": [
            {"id": "t1", "content": "timeline entry body text", "source": "file",
             "timestamp": "2026-01-01T00:00:00", "tier": "raw", "tags": ["file"], "route": "x"}]}
    monkeypatch.setattr(remote, "memories", _memories)

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["timeline", "--days", "3"])
    assert result.exit_code == 0
    assert "timeline entry body text" in result.output
    assert "since=" not in result.output  # human-readable, not the raw query
    assert "since" in captured  # but the box call did pass a since filter


def test_memories_json_output(monkeypatch):
    """memories --json emits structured JSON, not the formatted table."""
    import json as _json

    from jarvis import remote
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "memories", lambda **kw: {
        "count": 2,
        "memories": [
            {"id": "a", "content": "alpha body", "source": "s", "timestamp": "2026-01-01T00:00:00",
             "tier": "raw", "tags": ["deep"], "route": "x"},
            {"id": "b", "content": "beta body", "source": "s", "timestamp": "2026-01-02T00:00:00",
             "tier": "session", "tags": ["manual"], "route": "y"},
        ],
    })

    from click.testing import CliRunner
    result = CliRunner().invoke(cli, ["memories", "--json"])
    assert result.exit_code == 0
    data = _json.loads(result.output)
    assert isinstance(data, list) and len(data) == 2
    assert data[0]["id"] == "a"


def test_task_list_command_still_registered():
    """The CLI command remains ``task list`` after the rename."""
    from jarvis.cli import cli

    assert "list" in cli.commands["task"].commands


def test_ask_grounds_on_local_brain(monkeypatch):
    """`ask` in local mode must answer AND show the grounding sources."""
    import json as _json

    from click.testing import CliRunner

    from jarvis.brain import Brain
    from jarvis.cli import cli

    class _FakeStore:
        def close(self): pass
        def lookup_entities(self, ids): return {}

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)
    monkeypatch.setattr("jarvis.cli.Store", lambda *a, **k: _FakeStore())

    # never call the (potentially unmocked) local store/LLM — stub the query
    monkeypatch.setattr(
        Brain, "query",
        lambda self, question, **kw: (
            "The fence post is solid.",
            [{"source": "deep", "timestamp": "2026-01-01T00:00:00",
              "content": "installed the fence post"}]),
    )

    result = CliRunner().invoke(cli, ["ask", "how is the fence?", "--no-save"])
    assert result.exit_code == 0, result.output
    assert "The fence post is solid." in result.output
    assert "grounded in 1 memory" in result.output
    assert "installed the fence post" in result.output

    # json mode returns a structured {answer, sources}
    result2 = CliRunner().invoke(cli, ["ask", "how is the fence?", "--json-out", "--no-save"])
    assert result2.exit_code == 0
    data = _json.loads(result2.output)
    assert data["answer"] == "The fence post is solid."
    assert data["sources"][0]["source"] == "deep"


def test_ask_client_mode_delegates_to_box(monkeypatch):
    """`ask` in client mode must call the box's grounded brain (not local)."""
    import json as _json

    from click.testing import CliRunner

    from jarvis import remote
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "query",
                        lambda question, n=8, source=None, history=None, model=None:
                        {"answer": "box answer", "memories": [], "entities": {}})
    monkeypatch.setattr(remote, "remember_batch", lambda items: {"added": 1})

    result = CliRunner().invoke(cli, ["ask", "hello", "--json-out", "--no-save"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["answer"] == "box answer"


def test_ask_session_threads_and_persists(monkeypatch):
    """`ask --session <id>` must thread prior turns into the query and persist the
    exchange to the session store (Round 9 #6)."""
    import json as _json

    from click.testing import CliRunner

    from jarvis.brain import Brain
    from jarvis.cli import cli

    class _FakeStore:
        def close(self): pass

    class _FakeSDB:
        def __init__(self): self.msgs = []
        def close(self): pass
        def get_messages(self, sid, limit=100):
            return [{"role": "user", "content": "earlier q"},
                    {"role": "assistant", "content": "earlier a"}]
        def create_session(self, title=""): return "sess-1"
        def append_message(self, sid, role, content, tool_calls=None):
            self.msgs.append((role, content))

    fake_sdb = _FakeSDB()
    seen = {}
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)
    monkeypatch.setattr("jarvis.cli.Store", lambda *a, **k: _FakeStore())
    monkeypatch.setattr("jarvis.sessions.SessionDB", lambda *a, **k: fake_sdb)
    monkeypatch.setattr(
        Brain, "query",
        lambda self, question, **kw: (seen.update(history=kw.get("history")) or "follow-up answer", []))

    result = CliRunner().invoke(cli, ["ask", "follow up?", "--session", "sess-1", "--json-out", "--no-save"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["session_id"] == "sess-1"
    # history was loaded from the session and passed into the grounded query
    assert seen["history"][0]["content"] == "earlier q"
    # user + assistant turns appended back to the session
    assert fake_sdb.msgs[0][0] == "user" and fake_sdb.msgs[-1][0] == "assistant"


def test_ask_save_writes_back_to_brain(monkeypatch):
    """`ask --save` must persist the Q&A back into the brain as an 'ask' memory."""
    from click.testing import CliRunner

    from jarvis.brain import Brain
    from jarvis.cli import cli

    class _FakeStore:
        def close(self): pass

    saved = {}
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)
    monkeypatch.setattr("jarvis.cli.Store", lambda *a, **k: _FakeStore())
    monkeypatch.setattr(
        Brain, "query",
        lambda self, question, **kw: ("the saved answer", []))
    monkeypatch.setattr(
        Brain, "remember",
        lambda self, text, source="manual", tags=None, classify=False:
            saved.update(text=text, source=source, tags=tags) or 1)

    result = CliRunner().invoke(cli, ["ask", "a question", "--save"])
    assert result.exit_code == 0, result.output
    assert "Q: a question" in saved["text"]
    assert saved["source"] == "ask"
    assert saved["tags"] == ["ask"]


def test_ask_remote_threads_session_and_saves(monkeypatch):
    """Client-mode `ask --session/--save` must thread (local session log) and write
    back to the box via remember_batch."""
    import json as _json

    from click.testing import CliRunner

    from jarvis import remote
    from jarvis.cli import cli

    class _FakeSDB:
        def __init__(self): self.msgs = []
        def close(self): pass
        def get_messages(self, sid, limit=100): return []
        def create_session(self, title=""): return "s9"
        def append_message(self, sid, role, content, tool_calls=None):
            self.msgs.append((role, content))

    seen = {}
    fake_sdb = _FakeSDB()
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.sessions.SessionDB", lambda *a, **k: fake_sdb)
    monkeypatch.setattr(remote, "query",
                        lambda question, n=8, source=None, history=None, model=None:
                        seen.update(msg=question, hist=history) or
                        {"answer": "box threaded answer", "memories": [], "entities": {}})
    monkeypatch.setattr(remote, "remember_batch",
                        lambda items: seen.update(save_items=items) or {"added": 1})

    result = CliRunner().invoke(cli, ["ask", "hello again", "--session", "s9", "--save", "--json-out"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["answer"] == "box threaded answer"
    assert seen["msg"] == "hello again"
    # thread: the user turn was persisted to the local session log
    assert fake_sdb.msgs[0][0] == "user"
    assert seen["save_items"][0]["source"] == "ask"


def test_digest_now_remote(monkeypatch):
    """`digest --now` in client mode must ask the box for an on-demand digest."""
    import json as _json

    from click.testing import CliRunner

    from jarvis import remote
    from jarvis.cli import cli

    seen = {}
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "digest",
                        lambda kind="morning_brief": seen.update(kind=kind) or
                        {"kind": kind, "text": "Good morning! Here is your digest."})
    result = CliRunner().invoke(cli, ["digest", "--now", "--json-out"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert "Good morning!" in data["text"]
    assert seen["kind"] == "morning_brief"


def test_digest_report_without_now(monkeypatch):
    """`digest` without --now just reports the schedule (no network)."""
    from click.testing import CliRunner

    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    result = CliRunner().invoke(cli, ["digest"])
    assert result.exit_code == 0
    assert "08:00" in result.output and "--now" in result.output


def test_console_interactive_loop(monkeypatch):
    """`jarvis console` must answer a piped question and honor /quit (idea: an
    interactive Iron-Man-style terminal). Sources are hidden by default for
    natural chat."""
    from click.testing import CliRunner

    from jarvis import remote
    from jarvis.cli import cli

    class _FakeSDB:
        def __init__(self): self.msgs = []
        def close(self): pass
        def get_messages(self, sid, limit=100): return []
        def create_session(self, title=""): return "c1"
        def append_message(self, sid, role, content, tool_calls=None):
            self.msgs.append((role, content))

    seen = {}
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.sessions.SessionDB", lambda *a, **k: _FakeSDB())
    monkeypatch.setattr(remote, "query",
                        lambda question, n=8, source=None, history=None, model=None:
                        seen.update(q=question) or {"answer": "console says hello",
                                                    "memories": [{"source": "shell",
                                                                  "timestamp": "2026-01-01T00:00:00",
                                                                  "content": "some memory"}],
                                                    "entities": {}})
    monkeypatch.setattr(remote, "remember_batch", lambda items: {"added": 1})
    monkeypatch.setattr(remote, "health_deep", lambda: {"memories": 5, "mode": "local"})

    result = CliRunner().invoke(cli, ["console", "--no-save"],
                                input="hello jarvis\n/status\n/quit\n")
    assert result.exit_code == 0, result.output
    assert "J A R V I S" in result.output
    assert "console says hello" in result.output
    assert "memories=5" in result.output
    assert "Goodbye." in result.output
    assert seen.get("q") == "hello jarvis"
    # casual chat is clean: sources hidden by default
    assert "grounded in 1 memory" not in result.output


def test_console_save_qa_default_saves_no_save_skips(monkeypatch):
    """Regression: console /no-save must truly disable Q&A recall.

    `console` used a single ``--no-save`` flag bound to ``save_qa`` (default
    False) with a ``not save_qa`` guard, so the flag semantics were inverted
    relative to `ask`: default never saved and passing ``--no-save`` forced a
    save. Now it uses the same ``--save/--no-save`` (default True) + ``if
    save_qa`` pattern as `ask` — default saves, ``--no-save`` skips.
    """
    from click.testing import CliRunner

    from jarvis.cli import cli

    class _FakeSDB:
        def __init__(self): self.msgs = []
        def close(self): pass
        def get_messages(self, sid, limit=100): return []
        def create_session(self, title="", tier="raw"): return "c1"
        def append_message(self, sid, role, content, tool_calls=None):
            self.msgs.append((role, content))

    saved = []
    monkeypatch.setattr("jarvis.sessions.SessionDB", lambda *a, **k: _FakeSDB())
    monkeypatch.setattr(
        "jarvis.cli._ask_grounded",
        lambda question, history=None, model=None:
        ("console answer", [], {}),
    )
    monkeypatch.setattr("jarvis.cli._save_ask",
                        lambda q, a: saved.append((q, a)))

    # Default (no --no-save): Q&A is saved back to memory.
    result = CliRunner().invoke(cli, ["console"], input="remember me\n/quit\n")
    assert result.exit_code == 0, result.output
    assert saved == [("remember me", "console answer")]

    saved.clear()
    # --no-save: Q&A is NOT saved.
    result = CliRunner().invoke(cli, ["console", "--no-save"],
                                input="don't remember\n/quit\n")
    assert result.exit_code == 0, result.output
    assert saved == []


def test_console_sources_toggle_shows_sources(monkeypatch):
    """`/sources on` must reveal the grounded-memory dump; a recall question
    shows sources even when the toggle is off."""
    from click.testing import CliRunner

    from jarvis import remote
    from jarvis.cli import cli

    class _FakeSDB:
        def __init__(self): self.msgs = []
        def close(self): pass
        def get_messages(self, sid, limit=100): return []
        def create_session(self, title=""): return "c1"
        def append_message(self, sid, role, content, tool_calls=None):
            self.msgs.append((role, content))

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.sessions.SessionDB", lambda *a, **k: _FakeSDB())
    monkeypatch.setattr(remote, "query",
                        lambda question, n=8, source=None, history=None, model=None:
                        {"answer": "here is the finding",
                         "memories": [{"source": "deep", "timestamp": "2026-01-01T00:00:00",
                                       "content": "a relevant memory"}],
                         "entities": {}})
    monkeypatch.setattr(remote, "remember_batch", lambda items: {"added": 1})

    # /sources on then a casual question -> sources shown
    result = CliRunner().invoke(cli, ["console", "--no-save"],
                                input="/sources on\nhello again\n/quit\n")
    assert result.exit_code == 0, result.output
    assert "(sources: on)" in result.output
    assert "grounded in 1 memory" in result.output


def test_is_recall_detects_memory_questions():
    from jarvis.brain import _is_recall_question
    assert _is_recall_question("what did I do yesterday") is True
    assert _is_recall_question("show me my memories about the deploy") is True
    assert _is_recall_question("tell me about the sync protocol") is True
    assert _is_recall_question("hello sir how are you") is False
    assert _is_recall_question("thanks!") is False


def test_chat_remote_routes_turns_through_box(monkeypatch):
    """Thin-client `chat` must route every turn to the box (the single brain) and
    must NOT open a local Store — otherwise chat is memory-less in client mode,
    contradicting ask/digest which both go through the box."""
    from click.testing import CliRunner

    from jarvis.cli import cli

    calls = []

    def fake_chat(message, session_id=None, max_steps=8, model=None):
        calls.append({"message": message, "session_id": session_id,
                      "max_steps": max_steps, "model": model})
        return {"answer": f"box reply to {message!r}",
                "session_id": "box-sess-1",
                "tool_log": [{"tool": "search", "args": {"q": "x"}}]}

    store_calls = []
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.remote.chat", fake_chat)
    monkeypatch.setattr(
        "jarvis.cli.Store",
        lambda *a, **k: (store_calls.append(1), MagicMock())[1],
    )

    # Two turns: the box creates the session on the first, and we thread the
    # returned id into the second turn.
    result = CliRunner().invoke(cli, ["chat", "--verbose"],
                                input="hello\nagain\n/quit\n")

    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert calls[0]["message"] == "hello"
    assert calls[0]["session_id"] is None  # box creates session on first turn
    assert calls[1]["message"] == "again"
    assert calls[1]["session_id"] == "box-sess-1"  # threaded from box response
    assert "jarvis: box reply to 'hello'" in result.output
    assert "jarvis: box reply to 'again'" in result.output
    assert "[tools: 1 calls]" in result.output
    assert "search" in result.output
    # Remote chat must never open a local store (that would make it memory-less).
    assert store_calls == []
    assert "Session saved on the box. Goodbye." in result.output


def test_chat_local_turn_uses_run_turn(monkeypatch):
    """Regression: local (non-remote) ``chat`` must run each prompt through
    ``agent.run_turn`` and print the answer, instead of raising NameError from
    the undefined ``run_turn`` reference in the local chat body."""
    from click.testing import CliRunner

    from jarvis.cli import cli

    class _FakeSDB:
        def __init__(self):
            self.sid = "sess-1"

        def get_session(self, sid):
            return {"id": sid, "title": "t"}

        def create_session(self, title="", tier="raw"):
            self.sid = "sess-1"
            return self.sid

        def get_messages(self, sid, limit=100):
            return []

        def append_message(self, *a, **k):
            pass

        def update_session(self, *a, **k):
            pass

        def close(self):
            pass

    fake_store = MagicMock()
    fake_store.close = MagicMock()
    monkeypatch.setattr("jarvis.cli.SessionDB", lambda *a, **k: _FakeSDB())
    monkeypatch.setattr("jarvis.cli.Store", lambda *a, **k: fake_store)
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)

    calls = []
    monkeypatch.setattr(
        "jarvis.agent.run_turn",
        lambda user_input, sid, session_db, store_db, max_steps, verbose, model: (
            calls.append(
                {
                    "user_input": user_input,
                    "sid": sid,
                    "max_steps": max_steps,
                    "verbose": verbose,
                    "model": model,
                }
            )
            or ("local jarvis reply", [{"tool": "search", "args": {"q": "x"}}])
        ),
    )

    result = CliRunner().invoke(
        cli, ["chat", "--verbose"], input="My Chat\nhello sir\n/quit\n"
    )

    assert result.exit_code == 0, result.output
    assert calls, "local chat never invoked agent.run_turn"
    assert calls[0]["user_input"] == "hello sir"
    assert calls[0]["sid"] == "sess-1"
    assert calls[0]["max_steps"] == 8
    assert calls[0]["verbose"] is True
    # The answer printed by the local turn, no NameError.
    assert "jarvis: local jarvis reply" in result.output
    assert "[tools: 1 calls]" in result.output
    assert "Goodbye." in result.output


def test_chat_sources_lists_tool_messages_local(monkeypatch):
    """Regression: ``/sources`` in local ``chat`` must list messages whose
    ``role == 'tool'`` (the stored tool-result rows).

    The old filter ``"tool" in (m.get("tool_calls") or "{}")`` was always False
    because ``tool_calls`` is a *list of dicts* on the assistant row, so only
    ``role == 'system'`` rows ever matched and tool interactions never showed.
    """
    from click.testing import CliRunner

    from jarvis.cli import cli

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "let me check",
         "tool_calls": [{"name": "remember", "arguments": {}}]},
        {"role": "tool", "content": "{\"ok\": true}"},
        {"role": "system", "content": "big prompt"},
    ]

    class FakeSessionDB:
        def __init__(self):
            self.sid = "sess-1"

        def get_session(self, sid):
            return {"id": sid, "title": "t"}

        def create_session(self, title="", tier="raw"):
            self.sid = "sess-1"
            return self.sid

        def get_messages(self, sid, limit=100):
            return messages

        def append_message(self, *a, **k):
            pass

        def update_session(self, *a, **k):
            pass

        def close(self):
            pass

    fake_store = MagicMock()
    fake_store.close = MagicMock()
    monkeypatch.setattr("jarvis.cli.SessionDB", lambda *a, **k: FakeSessionDB())
    monkeypatch.setattr("jarvis.cli.Store", lambda *a, **k: fake_store)
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)

    # Only drive command lines (/sources, /quit); a real prompt turn would hit
    # the (pre-existing, unrelated) undefined ``run_turn`` in the local chat body.
    result = CliRunner().invoke(
        cli, ["chat"], input="My Chat\n/sources\n/quit\n"
    )

    assert result.exit_code == 0, result.output
    # Only the single role=='tool' row is counted as a tool interaction.
    assert "Last 1 tool interactions:" in result.output
    assert "tool: {\"ok\": true}" in result.output
    # The system/prompt row must NOT be reported as a tool interaction.
    assert "  system:" not in result.output


def test_chat_sources_lists_tool_messages_remote(monkeypatch):
    """The thin-client (remote) ``/sources`` path must behave the same way."""
    from click.testing import CliRunner

    from jarvis.cli import cli

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok",
         "tool_calls": [{"name": "remember", "arguments": {}}]},
        {"role": "tool", "content": "{\"ok\": true}"},
        {"role": "system", "content": "prompt"},
    ]
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr("jarvis.remote.session_messages", lambda sid: {"messages": messages})
    monkeypatch.setattr(
        "jarvis.remote.chat",
        lambda *a, **k: {"answer": "hi", "session_id": "sess-1", "tool_log": []},
    )

    result = CliRunner().invoke(
        cli, ["chat", "--resume", "sess-1"], input="/sources\n/quit\n"
    )

    assert result.exit_code == 0, result.output
    assert "Last 1 tool interactions:" in result.output
    assert "tool: {\"ok\": true}" in result.output
    assert "  system:" not in result.output


def test_followup_suggestions_picked_from_entities():
    """Proactive follow-ups are derived from grounding entities (no LLM call):
    the most-referenced names win, and entity_type shapes the phrase."""
    from jarvis.cli import _build_followup_suggestions

    entities = {
        "m1": [{"name": "Ada Lovelace", "entity_type": "person"},
               {"name": "Analytical Engine", "entity_type": "device"}],
        "m2": [{"name": "Ada Lovelace", "entity_type": "person"}],
        "m3": [{"name": "London", "entity_type": "location"}],
    }
    suggestions = _build_followup_suggestions(entities)
    # Ada appears twice → top pick; person type → "what have I said about …"
    assert suggestions[0] == "what have I said about Ada Lovelace?"
    # At most 3, and the default template appears for a non-person/place entity.
    assert len(suggestions) <= 3
    assert "what do I know about London?" in suggestions
    assert "tell me more about Analytical Engine" in suggestions


def test_render_grounded_prints_try_line(capsys):
    """A grounded answer with entities renders a single, low-key ``→ try:`` line."""
    from jarvis.cli import _render_grounded

    entities = {"m1": [{"name": "JARVIS", "entity_type": "system"}]}
    _render_grounded("Here is the answer.", [], entities,
                     show_entities=False, show_sources=False)
    out = capsys.readouterr().out
    assert "Here is the answer." in out
    assert "→ try: " in out
    assert '"tell me more about JARVIS"' in out


def test_render_grounded_no_entities_clean_output(capsys):
    """No entities → no bogus suggestion line, and no crash."""
    from jarvis.cli import _render_grounded

    _render_grounded("Nothing grounded.", [], {},
                     show_entities=True, show_sources=False)
    out = capsys.readouterr().out
    assert "Nothing grounded." in out
    assert "→ try:" not in out
    assert "-- related entities" not in out


def test_render_grounded_suggest_opt_out(capsys):
    """suggest=False suppresses the proactive line entirely."""
    from jarvis.cli import _render_grounded

    entities = {"m1": [{"name": "JARVIS", "entity_type": "system"}]}
    _render_grounded("Answer.", [], entities, show_entities=False,
                     show_sources=False, suggest=False)
    out = capsys.readouterr().out
    assert "→ try:" not in out

def test_greeting_banner_includes_context_counts(monkeypatch):
    """`_build_greeting_banner` must surface pending tasks / calendar counts /
    last-memory recency when the (stubbed) data is available."""
    from jarvis.cli import _build_greeting_banner

    facts = {"task_pending": 3, "calendar_today": 2,
             "last_memory_ts": "2026-01-01T08:00:00"}
    monkeypatch.setattr("jarvis.cli._collect_greeting_facts", lambda: facts)
    # Deterministic: no profile name -> default "sir" greeting.
    monkeypatch.setattr("jarvis.cli._profile_first_name", lambda: "")

    text = "\n".join(_build_greeting_banner("s1"))
    assert "J A R V I S" in text
    assert "3 task(s) awaiting you" in text
    assert "2 calendar event(s) today" in text
    assert "last memory" in text
    assert "(session s1)" in text
    assert "sir" in text


def test_greeting_banner_uses_profile_name(monkeypatch):
    """When a profile name is set, the greeting addresses the user by name."""
    from jarvis.cli import _build_greeting_banner

    monkeypatch.setattr("jarvis.cli._collect_greeting_facts", lambda: {})
    monkeypatch.setattr("jarvis.cli._profile_first_name", lambda: "Lucas")

    text = "\n".join(_build_greeting_banner("s1"))
    assert "Lucas" in text
    assert "sir" not in text


def test_greeting_banner_falls_back_when_data_unavailable(monkeypatch):
    """If the context collector raises, the console must still show a clean
    static banner — no crash, no task/calendar noise."""
    from jarvis.cli import _build_greeting_banner

    def _boom():
        raise RuntimeError("brain unreachable")

    monkeypatch.setattr("jarvis.cli._collect_greeting_facts", _boom)

    text = "\n".join(_build_greeting_banner("s1"))
    assert "J A R V I S" in text
    assert "(session s1)" in text
    assert "task(s)" not in text
    assert "calendar event(s)" not in text


def test_greeting_facts_render_gracefully_with_none():
    """All-None facts → no fact phrases, and `_fmt_ago` degrades gracefully."""
    from jarvis.cli import _fmt_ago, _render_greeting_facts

    assert _render_greeting_facts(
        {"task_pending": None, "calendar_today": None, "last_memory_ts": None}
    ) == []
    assert _render_greeting_facts(
        {"task_pending": 0, "calendar_today": 0, "last_memory_ts": None}
    ) == []
    assert _fmt_ago("not-a-timestamp") == "a while ago"


def test_console_model_override_selects_requested_model(monkeypatch):
    """`/model <tier|id>` must set the console model override so the next grounded
    question is answered with that model (passed through to `_ask_grounded`), not
    silently left to auto-tier."""
    from click.testing import CliRunner

    from jarvis.cli import cli

    class _FakeSDB:
        def __init__(self): self.msgs = []
        def close(self): pass
        def get_messages(self, sid, limit=100): return []
        def create_session(self, title=""): return "c1"
        def append_message(self, sid, role, content, tool_calls=None):
            self.msgs.append((role, content))

    seen = {}
    monkeypatch.setattr("jarvis.sessions.SessionDB", lambda *a, **k: _FakeSDB())
    monkeypatch.setattr(
        "jarvis.cli._ask_grounded",
        lambda question, history=None, model=None:
        seen.update(model=model) or ("chosen-model answer", [], {}),
    )
    monkeypatch.setattr("jarvis.cli._save_ask", lambda q, a: None)
    monkeypatch.setattr("jarvis.cli._render_grounded", lambda *a, **k: None)

    result = CliRunner().invoke(cli, ["console", "--no-save"],
                                input="/model big\nquick question\n/quit\n")
    assert result.exit_code == 0, result.output
    assert "(model set to big)" in result.output
    # The override is passed to the grounded asker — not dropped to auto-tier.
    assert seen.get("model") == "big"


def test_console_model_override_bare_shows_current(monkeypatch):
    """Bare `/model` (no arg) reports the current override, or 'auto' when unset."""
    from click.testing import CliRunner

    from jarvis.cli import cli

    class _FakeSDB:
        def __init__(self): self.msgs = []
        def close(self): pass
        def get_messages(self, sid, limit=100): return []
        def create_session(self, title=""): return "c1"
        def append_message(self, sid, role, content, tool_calls=None):
            self.msgs.append((role, content))

    monkeypatch.setattr("jarvis.sessions.SessionDB", lambda *a, **k: _FakeSDB())
    monkeypatch.setattr("jarvis.cli._ask_grounded",
                        lambda question, history=None, model=None: ("a", [], {}))
    monkeypatch.setattr("jarvis.cli._save_ask", lambda q, a: None)
    monkeypatch.setattr("jarvis.cli._render_grounded", lambda *a, **k: None)

    # Unset → auto.
    result = CliRunner().invoke(cli, ["console", "--no-save"],
                                input="/model\n/quit\n")
    assert result.exit_code == 0, result.output
    assert "(model tier: auto)" in result.output

    # After an override, bare `/model` reports it.
    result = CliRunner().invoke(cli, ["console", "--no-save"],
                                input="/model medium\n/model\n/quit\n")
    assert result.exit_code == 0, result.output
    assert "(model tier: medium)" in result.output
