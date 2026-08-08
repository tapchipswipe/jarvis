"""Tests for the Jarvis user profile (structured onboarding)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from jarvis import cli
from jarvis import profile as profile_mod


@pytest.fixture()
def profile_file(tmp_path, monkeypatch):
    """Point profile storage at a temp file (patch profile_path directly, since
    the JARVIS_PROFILE_FILE env var conflicts with the autouse hermetic fixture)."""
    p = tmp_path / "profile.json"
    monkeypatch.setattr("jarvis.profile.profile_path", lambda: p)
    return p


@pytest.fixture()
def mem_profile(monkeypatch):
    """In-memory profile store for CLI tests — fully deterministic, no filesystem
    and no cross-test monkeypatch ordering issues (the `add` command's double
    invoke otherwise leaks a patched path across tests)."""
    store = {"name": "", "role": "", "employer": "", "timezone": "", "motto": "",
             "goals": [], "people": [], "projects": [], "preferences": [], "commitments": []}

    def fake_load(path=None):
        return dict(store)

    def fake_save(profile, path=None):
        store.clear()
        store.update(profile)
        return path or profile_mod.profile_path()

    monkeypatch.setattr("jarvis.profile.load_profile", fake_load)
    monkeypatch.setattr("jarvis.profile.save_profile", fake_save)
    monkeypatch.setattr("jarvis.cli._load_profile_cmd", fake_load)
    return store


def test_load_profile_defaults_when_missing(profile_file):
    prof = profile_mod.load_profile()
    assert prof["name"] == ""
    assert prof["goals"] == []
    assert prof["people"] == []


def test_save_load_roundtrip(profile_file):
    prof = {
        "name": "Lucas",
        "role": "software engineer",
        "employer": "Saint Anselm College",
        "timezone": "America/New_York",
        "motto": "",
        "goals": ["Ship the Jarvis project"],
        "people": ["Mom: family", "Boss: work"],
        "projects": ["Jarvis: active"],
        "preferences": ["coffee: black"],
        "commitments": ["standup: 9am weekdays"],
    }
    profile_mod.save_profile(prof)
    loaded = profile_mod.load_profile()
    assert loaded["name"] == "Lucas"
    assert loaded["goals"] == ["Ship the Jarvis project"]
    assert loaded["people"] == ["Mom: family", "Boss: work"]
    assert loaded["projects"] == ["Jarvis: active"]


def test_profile_entries(profile_file):
    prof = {
        "name": "Lucas",
        "role": "",
        "employer": "",
        "timezone": "",
        "motto": "",
        "goals": ["Ship Jarvis", "Run a marathon"],
        "people": ["Mom: family"],
        "projects": [],
        "preferences": [],
        "commitments": [],
    }
    entries = profile_mod.profile_entries(prof)
    texts = {e["text"] for e in entries}
    assert "name: Lucas" in texts
    assert "goals: Ship Jarvis" in texts
    assert "goals: Run a marathon" in texts
    assert "people: Mom: family" in texts
    # empty fields produce no entries
    assert not any(e["field"] == "role" for e in entries)
    # every entry carries profile tag
    assert all("profile" in e["tags"] for e in entries)


def test_profile_entries_only_fields(profile_file):
    prof = {"name": "Lucas", "role": "engineer", "goals": ["x"], "people": [], "projects": [], "preferences": [], "commitments": [], "employer": "", "timezone": "", "motto": ""}
    entries = profile_mod.profile_entries(prof, only_fields=["name"])
    assert [e["field"] for e in entries] == ["name"]


def test_profile_digest(profile_file):
    prof = {"name": "Lucas", "role": "engineer", "goals": ["Ship Jarvis"], "people": [], "projects": [], "preferences": [], "commitments": [], "employer": "", "timezone": "", "motto": ""}
    digest = profile_mod.profile_digest(prof)
    assert "name: Lucas" in digest
    assert "goals: Ship Jarvis" in digest


def test_apply_profile_locally_adds_arc_tier(store):
    prof = {
        "name": "Lucas",
        "role": "engineer",
        "goals": ["Ship Jarvis"],
        "people": ["Mom: family"],
        "projects": [],
        "preferences": [],
        "commitments": [],
        "employer": "",
        "timezone": "",
        "motto": "",
    }
    added = profile_mod.apply_profile_locally(store, prof)
    assert added >= 1
    rows = store.conn.execute(
        "SELECT * FROM memories WHERE source = 'profile'"
    ).fetchall()
    assert len(rows) == added
    # arc tier + never-expiring + profile route
    for r in rows:
        assert r["tier"] == "arc"
        assert r["expires_at"] is None
        assert r["route"] == "profile"


def test_apply_profile_locally_dedups_on_resync(store):
    prof = {"name": "Lucas", "role": "", "goals": ["x"], "people": [], "projects": [], "preferences": [], "commitments": [], "employer": "", "timezone": "", "motto": ""}
    first = profile_mod.apply_profile_locally(store, prof)
    second = profile_mod.apply_profile_locally(store, prof)
    assert first >= 1
    assert second == 0  # nothing new on re-sync


def test_cli_profile_set_writes_file(mem_profile):
    runner = CliRunner()
    with patch("jarvis.cli._apply_profile_to_brain", return_value=0) as apply_mock:
        result = runner.invoke(cli.cli, ["profile", "set", "name", "Lucas"])
    assert result.exit_code == 0
    assert mem_profile["name"] == "Lucas"
    apply_mock.assert_called_once()


def test_cli_profile_add_appends(mem_profile):
    runner = CliRunner()
    with patch("jarvis.cli._apply_profile_to_brain", return_value=0):
        r1 = runner.invoke(cli.cli, ["profile", "add", "goals", "Ship Jarvis"])
        r2 = runner.invoke(cli.cli, ["profile", "add", "goals", "Run a marathon"])
    assert r1.exit_code == 0 and r2.exit_code == 0
    assert mem_profile["goals"] == ["Ship Jarvis", "Run a marathon"]


def test_cli_profile_show_empty(mem_profile):
    runner = CliRunner()
    result = runner.invoke(cli.cli, ["profile", "show"])
    assert result.exit_code == 0
    assert "empty" in result.output


def test_cli_profile_show_digest(mem_profile):
    # A populated profile renders its digest on `jarvis profile show`.
    mem_profile["name"] = "Lucas"
    mem_profile["goals"] = ["Ship Jarvis"]
    runner = CliRunner()
    result = runner.invoke(cli.cli, ["profile", "show"])
    assert result.exit_code == 0
    assert "name: Lucas" in result.output
    assert "goals: Ship Jarvis" in result.output