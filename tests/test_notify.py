"""
Tests for jarvis/notify.py

Covers:
  - send_notification writes a durable log record
  - write_briefing creates/appends the daily briefing file
  - subprocess failures are swallowed (best-effort notify)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from jarvis import notify


def _patch_paths(tmp_path, monkeypatch):
    # The notify functions derive notifications.log and briefings/ from
    # STATE_DIR, so overriding just that one switch is enough for isolation.
    config_dir = tmp_path / "config"
    monkeypatch.setattr(notify, "STATE_DIR", config_dir)


def test_send_notification_writes_log(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    # Force off-Darwin so no osascript is attempted
    monkeypatch.setattr(notify, "SYSTEM", "Linux")
    with patch.object(notify, "_run", return_value=False):
        notify.send_notification("Hello", "World", category="test")
    log = (tmp_path / "config" / "notifications.log").read_text()
    assert "Hello" in log
    assert "World" in log


def test_send_notification_never_raises(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    # Real subprocess failures are swallowed by _run() -> send_notification
    # must never raise even when the OS calls fail.
    with patch.object(notify, "subprocess", create=True) as mock_sp:
        mock_sp.run.side_effect = OSError("boom")
        notify.send_notification("title", "body")  # must not raise


def test_send_notification_short_circuits_on_success(tmp_path, monkeypatch):
    """When terminal-notifier succeeds, osascript must NOT be invoked."""
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    with (
        patch.object(notify, "_send_terminal_notifier", return_value=True) as tn,
        patch.object(notify, "_send_osascript") as osa,
    ):
        notify.send_notification("title", "body", category="test")
    tn.assert_called_once_with("title", "body", "test")
    osa.assert_not_called()


def test_send_notification_falls_through_on_failure(tmp_path, monkeypatch):
    """When terminal-notifier fails, osascript must be tried as a fallback."""
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    with (
        patch.object(notify, "_send_terminal_notifier", return_value=False),
        patch.object(notify, "_send_osascript") as osa,
    ):
        notify.send_notification("title", "body")
    osa.assert_called_once_with("title", "body")


def test_log_notification_records_actual_delivering_channel(tmp_path, monkeypatch):
    """_log_notification must record the backend that actually delivered,
    not a hard-coded label."""
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    with (
        patch.object(notify, "_send_terminal_notifier", return_value=True),
        patch.object(notify, "_send_osascript") as osa,
    ):
        notify.send_notification("title", "body")
    osa.assert_not_called()
    log = (tmp_path / "config" / "notifications.log").read_text()
    assert "terminal-notifier" in log
    assert "[oscript]" not in log


def test_log_notification_records_fallback_channel(tmp_path, monkeypatch):
    """When terminal-notifier fails but osascript succeeds, the log must
    name osascript as the delivering channel."""
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    with (
        patch.object(notify, "_send_terminal_notifier", return_value=False),
        patch.object(notify, "_send_osascript", return_value=True),
    ):
        notify.send_notification("title", "body")
    log = (tmp_path / "config" / "notifications.log").read_text()
    assert "osascript" in log
    assert "terminal-notifier" not in log


def test_log_notification_records_failure_channel(tmp_path, monkeypatch):
    """When every desktop backend fails, the log must record the durable
    log-only channel rather than mislabeling it as a desktop backend."""
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    with (
        patch.object(notify, "_send_terminal_notifier", return_value=False),
        patch.object(notify, "_send_osascript", return_value=False),
    ):
        notify.send_notification("title", "body")
    log = (tmp_path / "config" / "notifications.log").read_text()
    assert "[log]" in log
    assert "osascript" not in log
    assert "terminal-notifier" not in log


def test_escape_osascript_handles_quotes_backslash_newlines():
    """Quotes/backslashes are escaped and newlines collapsed to a space so the
    generated AppleScript string literal stays on one compilable line."""
    raw = 'He said "hi" with a backslash \\\nand a newline'
    escaped = notify._escape_osascript(raw)
    # No raw double quote, backslash, or newline may survive unescaped.
    assert '"' not in escaped.replace('\\"', "")
    assert "\n" not in escaped
    assert "\\" in escaped  # escaping still present
    # Reconstructing the literal (\" -> ") must yield the original minus newlines.
    import ast

    assert ast.literal_eval(f'"{escaped}"') == raw.replace("\n", " ")


def test_send_osascript_uses_escaped_body(tmp_path, monkeypatch):
    """A body with quotes and newlines must produce an escaped osascript call
    that closes over the double-quoted literal instead of breaking it."""
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    with patch.object(notify, "_run", return_value=True) as run:
        ok = notify._send_osascript(
            'Lunch', 'Meet at "The Spot"\\ for 2\nBring files'
        )
    assert ok is True
    script = run.call_args.args[0][-1]
    # The raw body must never appear unescaped inside the script.
    assert '\n' not in script
    assert 'display notification "' in script
    assert 'with title "Lunch"' in script
    # The escaped body literal, when parsed, must round-trip to the body with
    # newlines collapsed to spaces.
    import ast

    body = script.split('display notification "', 1)[1].split('" with title', 1)[0]
    assert ast.literal_eval(f'"{body}"') == 'Meet at "The Spot"\\ for 2 Bring files'


def test_send_notification_popup_succeeds_with_tricky_body(tmp_path, monkeypatch):
    """With terminal-notifier unavailable, osascript must still succeed for a
    body containing quotes/newlines/backslash — the popup must NOT silently
    fall back to log-only."""
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    tricky = 'Call "Mom" at 5\\pm\nRe: report'
    with (
        patch.object(notify, "_send_terminal_notifier", return_value=False),
        patch.object(notify, "_send_osascript", return_value=True) as osa,
    ):
        notify.send_notification("Reminder", tricky)
    osa.assert_called_once()
    log = (tmp_path / "config" / "notifications.log").read_text()
    assert "osascript" in log  # delivered via osascript, not log-only


def test_send_terminal_notifier_collapses_newlines(tmp_path, monkeypatch):
    """terminal-notifier argv must not contain a raw newline in the message."""
    _patch_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(notify, "SYSTEM", "Darwin")
    with patch.object(notify, "_run", return_value=True) as run:
        ok = notify._send_terminal_notifier("t", "line1\nline2", category="g")
    assert ok is True
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("-message") + 1] == "line1 line2"
    assert "\n" not in cmd[cmd.index("-message") + 1]


def test_write_briefing_creates_file(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    path = notify.write_briefing("Morning", "Lots of content")
    assert path.exists()
    text = path.read_text()
    assert "Morning" in text
    assert "Lots of content" in text
    assert text.startswith("# Jarvis Briefings")


def test_write_briefing_appends_without_duplicate_header(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    notify.write_briefing("First", "content 1")
    path = notify.write_briefing("Second", "content 2")
    text = path.read_text()
    assert text.count("# Jarvis Briefings") == 1
    assert "Second" in text


def test_write_briefing_returns_path(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    path = notify.write_briefing("Title", "Content")
    assert isinstance(path, Path)