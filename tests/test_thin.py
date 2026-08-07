"""Tests for jarvis/collectors/thin.py (thin-client ambient collector).

Hermetic: no Store/Chroma handles and no network. scan_once only writes to the
disposable outbox cache (path isolated via JARVIS_CACHE), and the CLI path mocks
remote.is_remote + thin internals so nothing real is ever scanned or flushed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner


def _tmp_inbox(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    (d / "sub").mkdir(parents=True, exist_ok=True)
    (d / "a.md").write_text("First note about ordering a fence.", encoding="utf-8")
    (d / "b.txt").write_text("A second note, longer than fifty characters for sure.", encoding="utf-8")
    return d


def test_scan_enqueues_new_and_skips_unchanged(tmp_path, monkeypatch):
    from jarvis.collectors import thin

    monkeypatch.setenv("JARVIS_CACHE", str(tmp_path / "cache.db"))
    root = _tmp_inbox(tmp_path)

    first = thin.scan_once(roots=[root], max_files=100)
    assert first["enqueued"] == 2
    assert first["dups"] == 0

    second = thin.scan_once(roots=[root], max_files=100)
    assert second["enqueued"] == 0
    assert second["skipped_seen"] == 2  # unchanged -> fingerprint skip, no re-read/enqueue


def test_scan_dedupes_equal_content_without_fingerprint(tmp_path, monkeypatch):
    """Even if the fingerprint is gone, equal content must not bloat the outbox."""
    from jarvis.cache import Cache
    from jarvis.collectors import thin

    monkeypatch.setenv("JARVIS_CACHE", str(tmp_path / "cache.db"))
    root = _tmp_inbox(tmp_path)

    stats = thin.scan_once(roots=[root], max_files=100)
    assert stats["enqueued"] == 2
    # wipe the 'seen' fingerprint list so we drop back to content-hash dedupe
    cache = Cache()
    try:
        cache.conn.execute("DELETE FROM kv")
        cache.conn.commit()
    finally:
        cache.close()

    again = thin.scan_once(roots=[root], max_files=100)
    assert again["enqueued"] == 0  # content equality dedupes (dups)
    assert again["dups"] >= 2


def test_scan_skips_blank(tmp_path, monkeypatch):
    from jarvis.collectors import thin

    monkeypatch.setenv("JARVIS_CACHE", str(tmp_path / "cache.db"))
    d = tmp_path / "docs"
    d.mkdir()
    (d / "blank.txt").write_text("   \n  ", encoding="utf-8")
    stats = thin.scan_once(roots=[d], max_files=100)
    assert stats["files"] == 1
    assert stats["blank"] == 1
    assert stats["enqueued"] == 0


def test_collect_cli_needs_client_mode(monkeypatch):
    """Outside client mode the CLI refuses so the Mac never writes a local brain."""
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)
    with patch("jarvis.collectors.thin.scan_once", MagicMock()) as scan:
        result = CliRunner().invoke(cli, ["collect"])
        assert result.exit_code == 2
        scan.assert_not_called()


def test_collect_cli_runs_in_client_mode(monkeypatch):
    """In client mode it scans + optionally flushes (both fully mocked here)."""
    from jarvis.cli import cli

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    stats = {"files": 3, "enqueued": 2, "dups": 0, "blank": 1, "skipped_seen": 0, "errors": 0}
    with patch("jarvis.collectors.thin.scan_once", return_value=stats) as scan, \
         patch("jarvis.collectors.thin.flush_once", return_value={"pushed": 2, "failed": 0, "offline": False}) as fl:
        result = CliRunner().invoke(cli, ["collect", "--flush"])
        assert result.exit_code == 0
        assert "enqueued 2" in result.output
        assert "pushed=2" in result.output
        scan.assert_called_once()
        fl.assert_called_once()
