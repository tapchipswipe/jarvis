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