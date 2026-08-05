"""
Tests for jarvis/paths.py — multi-user path isolation.

Covers:
  - default data/config root resolution
  - JARVIS_DATA_DIR / JARVIS_CONFIG_DIR overrides
  - JARVIS_USER secondary-profile derivation
  - owner-only directory permissions (access control)
"""
from __future__ import annotations

import stat

from jarvis import paths


def test_default_data_root_is_home_jarvis(monkeypatch):
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("JARVIS_USER", raising=False)
    monkeypatch.delenv("JARVIS_CONFIG_DIR", raising=False)
    assert paths.data_root() == paths.Path.home() / "jarvis"
    assert paths.config_dir() == paths.Path.home() / ".config" / "jarvis"


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "custom"))
    assert paths.data_root() == tmp_path / "custom"
    assert paths.data_dir("data", "meta.db") == tmp_path / "custom" / "data" / "meta.db"


def test_config_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_CONFIG_DIR", str(tmp_path / "cfg"))
    assert paths.config_dir() == tmp_path / "cfg"
    assert paths.config_file("triggers.toml") == tmp_path / "cfg" / "triggers.toml"


def test_secondary_user_isolation(monkeypatch):
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("JARVIS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("JARVIS_USER", "alice")
    # Assert alice is treated as a secondary profile (different from OS user)
    assert paths.data_root() == paths.Path.home() / "jarvis" / "users" / "alice"
    assert paths.config_dir() == paths.data_root() / "config"
    assert paths.data_dir("data", "meta.db") == (
        paths.Path.home() / "jarvis" / "users" / "alice" / "data" / "meta.db"
    )


def test_os_user_keeps_default_root(monkeypatch):
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("JARVIS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("JARVIS_USER", paths._os_username())
    assert paths.data_root() == paths.Path.home() / "jarvis"


def test_ensure_private_dir_owner_only(tmp_path):
    d = tmp_path / "private"
    paths.ensure_private_dir(d)
    mode = stat.S_IMODE(d.stat().st_mode)
    # 0o700: owner rwx, no group/other access
    assert mode & 0o077 == 0
    assert mode & 0o700 == 0o700


def test_logs_dir_helper():
    assert paths.logs_dir("daemon.log") == paths.data_root() / "logs" / "daemon.log"


# ── Integration: real Store honors JARVIS_DATA_DIR ───────────────────────────

def test_store_isolates_per_data_dir(tmp_path, monkeypatch):
    from unittest.mock import patch as _patch

    from jarvis.store import Store

    d1 = tmp_path / "user1"
    d2 = tmp_path / "user2"

    with _patch("jarvis.store.chromadb.PersistentClient"):
        monkeypatch.setenv("JARVIS_DATA_DIR", str(d1))
        s1 = Store()
        s1.add("m1", "manual", "1", "2026-01-01T10:00:00", "memory for user one", [], {}, [0.1] * 8)
        s1.close()

        monkeypatch.setenv("JARVIS_DATA_DIR", str(d2))
        s2 = Store()
        s2.add("m2", "manual", "2", "2026-01-01T10:00:00", "memory for user two", [], {}, [0.1] * 8)
        s2.close()

    # Separate DB files, no cross-talk
    assert (d1 / "data" / "meta.db").exists()
    assert (d2 / "data" / "meta.db").exists()

    monkeypatch.setenv("JARVIS_DATA_DIR", str(d1))
    with _patch("jarvis.store.chromadb.PersistentClient"):
        s1b = Store()
        rows = s1b.conn.execute("SELECT content FROM memories").fetchall()
        contents = {r["content"] for r in rows}
        s1b.close()
    assert contents == {"memory for user one"}
    assert "memory for user two" not in contents