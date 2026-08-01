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
    exiting via SystemExit) instead of converting ``tag`` to a list.
    """
    from click.testing import CliRunner

    from jarvis.cli import cli

    fake_store = MagicMock()
    fake_brain = MagicMock(remember=MagicMock(return_value=1))
    monkeypatch.setattr("jarvis.cli.Store", lambda *a, **k: fake_store)
    monkeypatch.setattr("jarvis.cli.Brain", lambda *a, **k: fake_brain)

    result = CliRunner().invoke(cli, ["remember", "regression check"])

    assert result.exit_code == 0
    assert "Remembered 1 chunk(s)." in result.output
    assert "Status" not in result.output


def test_task_list_command_still_registered():
    """The CLI command remains ``task list`` after the rename."""
    from jarvis.cli import cli

    assert "list" in cli.commands["task"].commands
