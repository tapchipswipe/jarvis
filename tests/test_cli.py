"""Regression tests for the jarvis CLI.

Covers the builtin-``list`` shadowing bug that broke ``remember`` when the
``task list`` click command was introduced.
"""
from __future__ import annotations

from unittest.mock import MagicMock


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
                        lambda question, n=8, source=None, history=None:
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
                        lambda question, n=8, source=None, history=None:
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

