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
    config_dir = tmp_path / "config"
    monkeypatch.setattr(notify, "STATE_DIR", config_dir)
    monkeypatch.setattr(notify, "NOTIFICATIONS_LOG", config_dir / "notifications.log")
    monkeypatch.setattr(notify, "BRIEFINGS_DIR", config_dir / "briefings")


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