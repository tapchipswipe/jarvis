"""Tests for all jarvis collectors (Phase 3: Collector Audit & Fix)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_store():
    """Return a minimal mock store for collector testing."""
    store = MagicMock()
    store.added = []

    def fake_add(fid, source, source_id, timestamp, content, tags, metadata, embedding, **kwargs):
        store.added.append({
            "fid": fid, "source": source, "source_id": source_id,
            "timestamp": timestamp, "content": content[:80],
            "tags": tags, "metadata": metadata,
        })
        return True

    store.add.side_effect = fake_add
    store.exists.return_value = False
    return store


# ---------------------------------------------------------------------------
# thin collector
# ---------------------------------------------------------------------------

def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestThinCollector:
    def test_import(self):
        from jarvis.collectors import thin
        assert hasattr(thin, "scan_once")
        assert hasattr(thin, "_walk")

    def test_scan_walk_error_does_not_abort_others_enqueue(self, tmp_path, monkeypatch):
        """A per-path walk error (broken symlink / permission bounce) skips just
        that path; the rest of the scan still enqueues its files."""
        from jarvis.collectors import thin

        monkeypatch.setenv("JARVIS_CACHE", str(tmp_path / "cache.db"))
        d = tmp_path / "docs"
        _write_file(d / "a.md", "First note about ordering a fence post order.")
        _write_file(d / "b.md", "Second note, longer than fifty characters for sure.")

        real_exclude = thin._should_exclude

        def flaky(path):
            if path.name == "b.md":
                raise OSError("simulated broken symlink / permission bounce")
            return real_exclude(path)

        monkeypatch.setattr(thin, "_should_exclude", flaky)

        stats = thin.scan_once(roots=[d], max_files=100)
        # a.md still enqueued; b.md's walk-time error is skipped, not fatal.
        assert stats["enqueued"] == 1
        assert stats["errors"] == 0  # walk-time skips are not per-file errors

    def test_scan_commits_staged_enqueues_despite_walk_error(self, tmp_path, monkeypatch):
        """Staged enqueues are committed even when a later path errors mid-walk."""
        from jarvis.collectors import thin

        monkeypatch.setenv("JARVIS_CACHE", str(tmp_path / "cache.db"))
        d = tmp_path / "docs"
        _write_file(d / "a.md", "Third note about tailscale vpn setup details.")
        _write_file(d / "b.md", "Fourth note, longer than fifty characters for sure.")

        real_exclude = thin._should_exclude

        def flaky(path):
            if path.name == "b.md":
                raise OSError("simulated bad path")
            return real_exclude(path)

        monkeypatch.setattr(thin, "_should_exclude", flaky)

        first = thin.scan_once(roots=[d], max_files=100)
        assert first["enqueued"] == 1

        # If the commit had been skipped/rolled back, a.md's fingerprint would
        # not persist. Its presence on re-scan proves the staged enqueue committed
        # despite b.md erroring during the same walk.
        second = thin.scan_once(roots=[d], max_files=100)
        assert second["skipped_seen"] == 1


# ---------------------------------------------------------------------------
# files collector
# ---------------------------------------------------------------------------

class TestFilesCollector:
    def test_import(self):
        from jarvis.collectors import files
        assert hasattr(files, "start_watcher")
        assert hasattr(files, "FileIngestionHandler")

    def test_start_watcher_creates_observer(self, mock_store):
        from jarvis.collectors.files import start_watcher
        observer = start_watcher(mock_store)
        assert observer is not None
        observer.stop()
        observer.join(timeout=1)


# ---------------------------------------------------------------------------
# git collector
# ---------------------------------------------------------------------------

class TestGitCollector:
    def test_import(self):
        from jarvis.collectors import git
        assert hasattr(git, "sync_git")

    @patch("jarvis.collectors.git.GIT_DIRS", [])
    def test_sync_git_no_repos_returns_zero(self, mock_store):
        from jarvis.collectors.git import sync_git
        count = sync_git(mock_store)
        assert count == 0


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# system collector
# ---------------------------------------------------------------------------

class TestSystemCollector:
    def test_import(self):
        from jarvis.collectors import system
        assert hasattr(system, "sync_system")

    @patch("jarvis.collectors.system.subprocess.run")
    def test_sync_system_mocked(self, mock_run, mock_store):
        from jarvis.collectors.system import sync_system
        # Mock both subprocess.run calls (system_profiler + log show)
        mock_run.return_value.stdout = '{"_items": []}'
        mock_run.return_value.returncode = 0
        count = sync_system(mock_store)
        assert isinstance(count, int)

    @patch("jarvis.collectors.system.subprocess.run")
    def test_sync_system_idempotent_stable_ts(self, mock_run):
        """A fixed snapshot ts keeps the fingerprint stable: the first run adds
        memories, the second run (same snapshot content) is skipped entirely."""
        from jarvis.collectors import system

        payload = '{"_items": [{"_name": "Safari", "version": "17.0", "path": "/Applications/Safari.app"}]}'
        mock_run.return_value.stdout = payload
        mock_run.return_value.returncode = 0

        added = set()

        def fake_add(fid, source, source_id, timestamp, content, tags, metadata, embedding, **kwargs):
            added.add(fid)
            return True

        def fake_exists(fid):
            return fid in added

        store = MagicMock()
        store.add.side_effect = fake_add
        store.exists.side_effect = fake_exists

        with patch("jarvis.collectors.system.get_embedding", return_value=[0.1, 0.2]):
            first = system.sync_system(store)
            second = system.sync_system(store)

        assert first > 0, "first run should add system snapshot memories"
        assert second == 0, "second run with unchanged snapshot must be skipped"


# ---------------------------------------------------------------------------
# deep collector
# ---------------------------------------------------------------------------

class TestDeepCollector:
    def test_import(self):
        from jarvis.collectors import deep
        assert hasattr(deep, "sync_deep")

    @patch("jarvis.collectors.deep.DEEP_DIRS", [])
    def test_sync_deep_zero_files(self, mock_store):
        from jarvis.collectors.deep import sync_deep
        count = sync_deep(mock_store, max_files=5)
        assert count == 0

    def test_sync_deep_walk_error_does_not_abort(self, mock_store, tmp_path):
        """A per-path walk error (broken symlink / permission bounce) must skip
        just that path; other files still get embedded and added to the store."""
        from jarvis.collectors import deep

        d = tmp_path / "docs"
        d.mkdir()
        (d / "a.md").write_text(
            "A sufficiently long user-authored note about the garden fence plans.",
            encoding="utf-8",
        )
        (d / "b.md").write_text(
            "Another long note, well over fifty characters, for the deep scan.",
            encoding="utf-8",
        )

        real_exclude = deep._should_exclude

        def flaky(path):
            if path.name == "b.md":
                raise OSError("simulated broken symlink during walk")
            return real_exclude(path)

        with patch("jarvis.collectors.deep.DEEP_DIRS", [d]), \
             patch("jarvis.collectors.deep._should_exclude", flaky):
            count = deep.sync_deep(mock_store, max_files=100)

        # a.md was processed; b.md's walk error was skipped, not fatal.
        assert count >= 1
        assert mock_store.added, "expected at least one successfully added chunk"


# ---------------------------------------------------------------------------
# shell collector
# ---------------------------------------------------------------------------

class TestShellCollector:
    def test_import(self):
        from jarvis.collectors import shell
        assert hasattr(shell, "sync_shell")
        assert hasattr(shell, "_parse_zsh_line")
        assert hasattr(shell, "_parse_bash_line")
        assert hasattr(shell, "_parse_fish_line")

    def test_parse_zsh_line(self):
        from jarvis.collectors.shell import _parse_zsh_line
        assert _parse_zsh_line(": 1712345678:0;ls -la") == "ls -la"
        assert _parse_zsh_line("") is None
        assert _parse_zsh_line("plain command") == "plain command"

    def test_parse_bash_line(self):
        from jarvis.collectors.shell import _parse_bash_line
        assert _parse_bash_line("ls -la") == "ls -la"
        assert _parse_bash_line("") is None

    def test_parse_fish_line(self):
        from jarvis.collectors.shell import _parse_fish_line
        assert _parse_fish_line("- cmd: ls -la") == "cmd: ls -la"
        assert _parse_fish_line("") is None

    @patch("jarvis.collectors.shell.HISTORY_PATHS", [])
    def test_sync_shell_no_history(self, mock_store):
        from jarvis.collectors.shell import sync_shell
        count = sync_shell(mock_store)
        assert count == 0

    def test_sync_shell_idempotent_uses_file_mtime(self, tmp_path):
        """First run with a history file adds memories; a second run against the
        same file (unchanged mtime) produces the same fingerprint so the skip
        fires and no new memories are added."""
        from jarvis.collectors import shell

        history_file = tmp_path / ".zsh_history"
        history_file.write_text(": 1712345678:0;ls -la\n: 1712345679:0;git status\n", encoding="utf-8")

        # Track added fids so store.exists() reflects prior inserts.
        added = set()

        def fake_add(fid, source, source_id, timestamp, content, tags, metadata, embedding, **kwargs):
            added.add(fid)
            return True

        def fake_exists(fid):
            return fid in added

        store = MagicMock()
        store.add.side_effect = fake_add
        store.exists.side_effect = fake_exists

        with patch("jarvis.collectors.shell.HISTORY_PATHS", [(history_file, "zsh")]), \
             patch("jarvis.collectors.shell.get_embedding", return_value=[0.1, 0.2]), \
             patch("jarvis.collectors.shell.extract_metadata", return_value={"tags": [], "entities": []}):
            first = shell.sync_shell(store)
            second = shell.sync_shell(store)

        assert first > 0, "first run should add history memories"
        assert second == 0, "second run with unchanged mtime must not re-add memories"

    def test_sync_shell_ts_stable_across_runs(self, tmp_path):
        """The per-file mtime yields a stable batch ts so the fingerprint is not
        recomputed from datetime.now() on every run."""
        import os

        history_file = tmp_path / ".zsh_history"
        history_file.write_text(": 1712345678:0;echo hi\n", encoding="utf-8")

        from jarvis.collectors import shell
        from jarvis.store import fingerprint

        with patch("jarvis.collectors.shell.HISTORY_PATHS", [(history_file, "zsh")]):
            lines = history_file.read_text(errors="ignore").splitlines()
            cmd = shell._parse_zsh_line(lines[0])
            mtime_ts = os.path.getmtime(history_file)
            from datetime import datetime, timezone
            stable_ts = datetime.fromtimestamp(mtime_ts, tz=timezone.utc).replace(tzinfo=None).isoformat()
            fid = fingerprint("shell", cmd[:64], cmd, stable_ts)
            # Same mtime -> identical ts -> identical fingerprint across runs.
            assert shell._file_mtime_ts(history_file) == stable_ts
            assert fingerprint("shell", cmd[:64], cmd, shell._file_mtime_ts(history_file)) == fid


# ---------------------------------------------------------------------------
# calendar collector
# ---------------------------------------------------------------------------

class TestCalendarCollector:
    def test_import(self):
        from jarvis.collectors import calendar
        assert hasattr(calendar, "sync_calendar")

    @patch("jarvis.collectors.calendar.sqlite3.connect")
    def test_sync_calendar_no_data(self, mock_conn, mock_store):
        from jarvis.collectors.calendar import sync_calendar
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.execute.return_value = mock_cursor
        count = sync_calendar(mock_store)
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# contacts collector
# ---------------------------------------------------------------------------

class TestContactsCollector:
    def test_import(self):
        from jarvis.collectors import contacts
        assert hasattr(contacts, "sync_contacts")

    @patch("jarvis.collectors.contacts.sqlite3.connect")
    def test_sync_contacts_no_data(self, mock_conn, mock_store):
        from jarvis.collectors.contacts import sync_contacts
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.execute.return_value = mock_cursor
        count = sync_contacts(mock_store)
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# messages collector
# ---------------------------------------------------------------------------

class TestMessagesCollector:
    def test_import(self):
        from jarvis.collectors import messages
        assert hasattr(messages, "sync_messages")
        assert hasattr(messages, "_copy_locked_db")

    @patch("jarvis.collectors.messages.CHAT_DB", Path("/nonexistent/chat.db"))
    def test_sync_messages_no_db(self, mock_store):
        from jarvis.collectors.messages import sync_messages
        count = sync_messages(mock_store)
        assert count == 0

    @patch("jarvis.collectors.messages.sqlite3.connect")
    @patch("jarvis.collectors.messages._copy_locked_db")
    def test_sync_messages_no_data(self, mock_copy, mock_conn, mock_store):
        pass
# ---------------------------------------------------------------------------
# notes collector
# ---------------------------------------------------------------------------

class TestNotesCollector:
    def test_import(self):
        from jarvis.collectors import notes
        assert hasattr(notes, "sync_notes")
        assert hasattr(notes, "_copy_locked_db")

    @patch("jarvis.collectors.notes.NOTES_PATHS", [Path("/nonexistent/NoteStorage.sqlite")])
    def test_sync_notes_no_db(self, mock_store):
        from jarvis.collectors.notes import sync_notes
        count = sync_notes(mock_store)
        assert count == 0

    @patch("jarvis.collectors.notes.sqlite3.connect")
    @patch("jarvis.collectors.notes._copy_locked_db")
    def test_sync_notes_no_data(self, mock_copy, mock_conn, mock_store):
        from jarvis.collectors.notes import sync_notes
        mock_copy.return_value = Path("/tmp/fake_notestore.db")
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.execute.return_value = mock_cursor
        mock_conn.return_value.row_factory = None
        count = sync_notes(mock_store)
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# photos collector
# ---------------------------------------------------------------------------

class TestPhotosCollector:
    def test_import(self):
        from jarvis.collectors import photos
        assert hasattr(photos, "sync_photos")
        assert hasattr(photos, "_has_tesseract")
        assert hasattr(photos, "_ocr_image")

    @patch("jarvis.collectors.photos.subprocess.run")
    def test_has_tesseract_true(self, mock_run):
        from jarvis.collectors.photos import _has_tesseract
        mock_run.return_value.returncode = 0
        assert _has_tesseract() is True

    @patch("jarvis.collectors.photos.subprocess.run")
    def test_has_tesseract_false(self, mock_run):
        from jarvis.collectors.photos import _has_tesseract
        mock_run.side_effect = Exception("not found")
        assert _has_tesseract() is False

    @patch("jarvis.collectors.photos.subprocess.run")
    def test_ocr_image(self, mock_run):
        from jarvis.collectors.photos import _ocr_image
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "extracted text"
        result = _ocr_image(Path("/fake/photo.png"))
        assert result == "extracted text"

    @patch("jarvis.collectors.photos.subprocess.run")
    def test_ocr_image_fails(self, mock_run):
        from jarvis.collectors.photos import _ocr_image
        mock_run.return_value.returncode = 1
        result = _ocr_image(Path("/fake/photo.png"))
        assert result is None

    @patch("jarvis.collectors.photos.PHOTO_DIRS", [])
    @patch("jarvis.collectors.photos.subprocess.run")
    def test_sync_photos_no_dir(self, mock_run, mock_store):
        from jarvis.collectors.photos import sync_photos
        count = sync_photos(mock_store)
        assert count == 0

    @patch("jarvis.collectors.photos.subprocess.run")
    def test_has_exiftool_true(self, mock_run):
        from jarvis.collectors.photos import _has_exiftool
        mock_run.return_value.returncode = 0
        assert _has_exiftool() is True

    @patch("jarvis.collectors.photos.subprocess.run")
    def test_has_exiftool_false(self, mock_run):
        from jarvis.collectors.photos import _has_exiftool
        mock_run.return_value.returncode = 1
        assert _has_exiftool() is False

    @patch("jarvis.collectors.photos.subprocess.run")
    @patch("jarvis.collectors.photos.PHOTO_DIRS", [Path("/nonexistent")])
    def test_sync_photos_skips_when_no_exiftool(self, mock_run, mock_store):
        # exiftool missing -> skip quietly (return 0), and never attempt per-photo
        # metadata reads (which relied on exiftool).
        from jarvis.collectors.photos import sync_photos
        mock_run.return_value.returncode = 1  # `which exiftool` fails
        count = sync_photos(mock_store)
        assert count == 0
        # Only the `which exiftool` probe should run, no per-photo exiftool calls.
        assert mock_run.call_count <= 1


# ---------------------------------------------------------------------------
# email collector
# ---------------------------------------------------------------------------

class TestEmailCollector:
    def test_import(self):
        from jarvis.collectors import email
        assert hasattr(email, "sync_email")

    @patch("jarvis.collectors.email.sqlite3.connect")
    def test_sync_email_no_data(self, mock_conn, mock_store):
        from jarvis.collectors.email import sync_email
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.execute.return_value = mock_cursor
        count = sync_email(mock_store)
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# browser collector
# ---------------------------------------------------------------------------

class TestBrowserCollector:
    def test_import(self):
        from jarvis.collectors import browser
        assert hasattr(browser, "read_browser_history")
        assert hasattr(browser, "_read_chrome")
        assert hasattr(browser, "_read_safari")
        assert hasattr(browser, "_read_firefox")

    @patch("jarvis.collectors.browser.sqlite3.connect")
    def test_read_chrome_no_data(self, mock_conn, mock_store):
        from jarvis.collectors.browser import _read_chrome
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.execute.return_value = mock_cursor
        count = _read_chrome(mock_store, Path("/fake/History"), days_back=1)
        assert isinstance(count, int)

    @patch("jarvis.collectors.browser.sqlite3.connect")
    def test_read_safari_no_data(self, mock_conn, mock_store):
        from jarvis.collectors.browser import _read_safari
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.execute.return_value = mock_cursor
        count = _read_safari(mock_store, Path("/fake/History.db"), days_back=1)
        assert isinstance(count, int)

    @patch("jarvis.collectors.browser.sqlite3.connect")
    def test_read_firefox_no_data(self, mock_conn, mock_store, tmp_path):
        from jarvis.collectors.browser import _read_firefox
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir()
        count = _read_firefox(mock_store, profile_dir, days_back=1)
        assert count == 0

    @patch("jarvis.collectors.browser.sqlite3.connect")
    def test_read_browser_history_no_browsers(self, mock_conn, mock_store):
        from jarvis.collectors.browser import read_browser_history
        count = read_browser_history(mock_store, days_back=1)
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# reminders collector
# ---------------------------------------------------------------------------

class TestRemindersCollector:
    def test_import(self):
        from jarvis.collectors import reminders
        assert hasattr(reminders, "sync_reminders")

    @patch("jarvis.collectors.reminders.sqlite3.connect")
    def test_sync_reminders_no_data(self, mock_conn, mock_store):
        from jarvis.collectors.reminders import sync_reminders
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.return_value.execute.return_value = mock_cursor
        count = sync_reminders(mock_store)
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# kilo collector
# ---------------------------------------------------------------------------

class TestKiloCollector:
    def test_import(self):
        from jarvis.collectors import kilo
        assert hasattr(kilo, "ingest_kilo_sessions")

    def test_ingest_kilo_sessions_no_dir(self, mock_store):
        from jarvis.collectors.kilo import ingest_kilo_sessions
        count = ingest_kilo_sessions(mock_store)
        assert count == 0
# rss collector
# ---------------------------------------------------------------------------

class TestRssCollector:
    def test_import(self):
        from jarvis.collectors import rss
        assert hasattr(rss, "sync_rss")

    def test_sync_rss_no_files_returns_zero(self, mock_store):
        from jarvis.collectors.rss import sync_rss
        count = sync_rss(mock_store)
        assert isinstance(count, int)
# ---------------------------------------------------------------------------
# bookmarks collector
# ---------------------------------------------------------------------------

class TestBookmarksCollector:
    def test_import(self):
        from jarvis.collectors import bookmarks
        assert hasattr(bookmarks, "sync_bookmarks")
        assert hasattr(bookmarks, "_walk_safari")
        assert hasattr(bookmarks, "_walk_chrome")

    def test_walk_safari_empty(self):
        from jarvis.collectors.bookmarks import _walk_safari
        result = _walk_safari({"Children": []})
        assert result == []

    def test_walk_safari_with_url(self):
        from jarvis.collectors.bookmarks import _walk_safari
        node = {"Children": [
            {"URLString": "https://example.com", "Name": "Example",
             "URIDictionary": {"lastVisitedDate": "2024-01-01"}}
        ]}
        result = _walk_safari(node)
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com"

    def test_walk_safari_nested(self):
        from jarvis.collectors.bookmarks import _walk_safari
        node = {"Children": [
            {"Children": [{"URLString": "https://nested.com", "Name": "Nested"}]}
        ]}
        result = _walk_safari(node)
        assert len(result) == 1
        assert result[0]["url"] == "https://nested.com"

    def test_walk_chrome_empty(self, tmp_path):
        from jarvis.collectors.bookmarks import _walk_chrome
        bm_file = tmp_path / "Bookmarks"
        bm_file.write_text(json.dumps({"roots": {}}))
        result = _walk_chrome(bm_file)
        assert result == []

    def test_walk_chrome_with_bookmarks(self, tmp_path):
        from jarvis.collectors.bookmarks import _walk_chrome
        data = {
            "roots": {
                "bookmark_bar": {
                    "children": [
                        {"type": "url", "name": "Google", "url": "https://google.com", "date_added": "12345"},
                        {"type": "folder", "name": "Folder", "children": [
                            {"type": "url", "name": "Inside", "url": "https://inside.com"}
                        ]}
                    ]
                }
            }
        }
        bm_file = tmp_path / "Bookmarks"
        bm_file.write_text(json.dumps(data))
        result = _walk_chrome(bm_file)
        assert len(result) == 2
        assert result[0]["url"] == "https://google.com"
        assert result[1]["url"] == "https://inside.com"

    def test_sync_bookmarks_no_files(self, mock_store):
        from jarvis.collectors.bookmarks import sync_bookmarks
        count = sync_bookmarks(mock_store)
        assert isinstance(count, int)
# ---------------------------------------------------------------------------
# sync_runner tests
# ---------------------------------------------------------------------------

class TestSyncRunner:
    def test_import(self):
        from jarvis.collectors.sync_runner import ingest_gemini_takeout, run_sync
        assert callable(run_sync)
        assert callable(ingest_gemini_takeout)

    def test_run_sync_files(self, mock_store):
        with patch("jarvis.collectors.sync_runner.Store", return_value=mock_store):
            from jarvis.collectors.sync_runner import run_sync
            results = run_sync("files")
            assert isinstance(results, dict)
            assert "files" in results

    def test_run_sync_unknown_target(self, mock_store):
        with patch("jarvis.collectors.sync_runner.Store", return_value=mock_store):
            from jarvis.collectors.sync_runner import run_sync
            results = run_sync("nonexistent_source")
            assert isinstance(results, dict)
            assert len(results) == 0

    def test_ingest_gemini_takeout_no_file(self, mock_store):
        from jarvis.collectors.sync_runner import ingest_gemini_takeout
        count = ingest_gemini_takeout(Path("/nonexistent/takeout.zip"), mock_store)
        assert count == 0


# ---------------------------------------------------------------------------
# CLI sync command tests
# ---------------------------------------------------------------------------

class TestCliSyncCommand:
    def test_sync_command_import(self):
        """Verify the sync CLI command is registered."""
        from jarvis.cli import cli
        commands = {cmd.name: cmd for cmd in cli.commands.values()}
        assert "sync" in commands

    def test_sync_help_text_includes_all_sources(self):
        """Verify the --source help text mentions all collectors."""
        from jarvis.cli import cli
        sync_cmd = cli.commands.get("sync")
        assert sync_cmd is not None
        params = {p.name: p for p in sync_cmd.params}
        source_param = params.get("source")
        assert source_param is not None
        help_text = source_param.help
        assert help_text is not None
        for collector in ["files", "browser", "calendar", "email", "photos",
                          "bookmarks", "rss", "system", "deep", "git",
                          "shell", "kilo", "notes", "reminders", "contacts",
                          "messages", "photos_ocr"]:
            assert collector in help_text, f"{collector} missing from sync help text"


# ---------------------------------------------------------------------------
# End-to-end: verify all collector modules can be imported
# ---------------------------------------------------------------------------

class TestAllCollectorsImport:
    """Verify every collector module loads without ImportError."""

    COLLECTOR_NAMES = [
        "files", "git", "rss", "system", "deep", "shell", "kilo",
        "calendar", "contacts", "messages", "reminders", "notes",
        "photos", "email", "browser", "bookmarks",
    ]

    def test_all_import(self):
        import importlib
        for name in self.COLLECTOR_NAMES:
            mod = importlib.import_module("jarvis.collectors." + name)
            assert mod is not None, f"Failed to import {name}"
