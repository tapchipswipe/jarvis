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


def test_task_list_command_still_registered():
    """The CLI command remains ``task list`` after the rename."""
    from jarvis.cli import cli

    assert "list" in cli.commands["task"].commands

