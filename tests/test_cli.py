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


def test_task_list_command_still_registered():
    """The CLI command remains ``task list`` after the rename."""
    from jarvis.cli import cli

    assert "list" in cli.commands["task"].commands

