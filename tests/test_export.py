"""Tests for the `jarvis export` CLI command (Phase 5)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from jarvis.cli import cli

EXPECTED_CONTENTS = {
    "mem-1": "Jarvis remembers a fact about the user.",
    "mem-2": "Lunch with Sam on Friday.",
}


@pytest.fixture(autouse=True)
def _local_export_mode(monkeypatch):
    """These tests exercise the local-store export path; pin client mode OFF so the
    ambient thin-client env (Round 7) can't redirect them to the box."""
    monkeypatch.setattr("jarvis.remote.is_remote", lambda: False)


def _seed_memory(store):
    """Insert two memories into the (temp) store."""
    store.add(
        fid="mem-1",
        source="manual",
        source_id="",
        timestamp="2024-01-01T00:00:00",
        content=EXPECTED_CONTENTS["mem-1"],
        tags=["test", "fact"],
        metadata={"origin": "unit-test"},
        embedding=[0.1, 0.2, 0.3],
        tier="raw",
        route="reference_note",
    )
    store.add(
        fid="mem-2",
        source="calendar",
        source_id="cal-1",
        timestamp="2024-01-02T00:00:00",
        content=EXPECTED_CONTENTS["mem-2"],
        tags=[],
        metadata={},
        embedding=[0.4, 0.5, 0.6],
        tier="session",
    )


def _run_export(store, *args):
    """Invoke the export command with jarvis.cli.Store redirected to the temp store."""
    with patch("jarvis.cli.Store", return_value=store):
        return CliRunner().invoke(cli, ["export", *args])


# ── JSON ──────────────────────────────────────────────────────────────────────

def test_export_json_to_file(store, tmp_path):
    _seed_memory(store)
    out = tmp_path / "export.json"
    result = _run_export(store, "--format", "json", "--output", str(out))

    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == 2
    assert data["exported_at"]
    contents = {m["content"] for m in data["memories"]}
    assert contents == set(EXPECTED_CONTENTS.values())

    mem = next(m for m in data["memories"] if m["id"] == "mem-1")
    assert mem["tags"] == ["test", "fact"]
    assert mem["metadata"] == {"origin": "unit-test"}
    assert mem["route"] == "reference_note"


def test_export_json_stdout(store):
    _seed_memory(store)
    result = _run_export(store, "--format", "json", "-o", "-")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["count"] == 2
    assert {m["id"] for m in data["memories"]} == {"mem-1", "mem-2"}


# ── Markdown ──────────────────────────────────────────────────────────────────

def test_export_markdown_to_file(store, tmp_path):
    _seed_memory(store)
    out = tmp_path / "export.md"
    result = _run_export(store, "--format", "markdown", "--output", str(out))

    assert result.exit_code == 0, result.output
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "# Jarvis Memory Export" in text
    assert "**Memories:** 2" in text
    assert EXPECTED_CONTENTS["mem-1"] in text
    assert EXPECTED_CONTENTS["mem-2"] in text
    assert "**Tags:** test, fact" in text
    assert "**Route:** reference_note" in text


def test_export_markdown_md_alias_stdout(store):
    _seed_memory(store)
    result = _run_export(store, "--format", "md", "-o", "-")

    assert result.exit_code == 0, result.output
    assert "# Jarvis Memory Export" in result.output
    assert EXPECTED_CONTENTS["mem-1"] in result.output


# ── Default output + filters ──────────────────────────────────────────────────

def test_export_default_timestamped_file_in_data_dir(store, tmp_path, monkeypatch):
    _seed_memory(store)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    result = _run_export(store, "--format", "json")

    assert result.exit_code == 0, result.output
    exports = list((tmp_path / "data" / "exports").glob("jarvis-export-*.json"))
    assert len(exports) == 1
    data = json.loads(exports[0].read_text(encoding="utf-8"))
    assert data["count"] == 2


def test_export_filters_by_source_and_tier(store, tmp_path):
    _seed_memory(store)
    out = tmp_path / "filtered.json"
    result = _run_export(
        store, "--format", "json", "--source", "calendar", "--tier", "session",
        "--output", str(out),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["memories"][0]["id"] == "mem-2"


# ── thin-client (box) export ──────────────────────────────────────────────────

_BOX_MEMORIES = [
    {"id": "box-1", "content": "Box note one about tailscale mesh", "source": "deep",
     "timestamp": "2026-01-01T00:00:00", "tier": "raw", "tags": "[\"deep\"]",
     "metadata": "{\"path\": \"/box/x.md\"}", "route": "unclassified"},
    {"id": "box-2", "content": "Box note two, a manual bookkeeping entry", "source": "manual",
     "timestamp": "2026-01-02T00:00:00", "tier": "session", "tags": "[]",
     "metadata": "{}", "route": "reference_note"},
]


def test_export_client_mode_pulls_from_box_not_local_store(tmp_path, monkeypatch):
    """In client mode export must reach the box and never open the local store."""
    from jarvis import remote

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "export", lambda fmt="json": {"count": 2, "memories": _BOX_MEMORIES})
    # if the local store were opened this test would fail loudly instead of silently
    monkeypatch.setattr("jarvis.cli.Store",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("local store used in client mode")))

    out = tmp_path / "client-export.json"
    result = CliRunner().invoke(cli, ["export", "--format", "json", "--output", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == 2
    # tags/metadata arrive as JSON strings from the box and must be normalized
    assert data["memories"][0]["tags"] == ["deep"]
    assert data["memories"][0]["metadata"] == {"path": "/box/x.md"}
    assert {m["id"] for m in data["memories"]} == {"box-1", "box-2"}


def test_export_client_mode_markdown_stdout(monkeypatch):
    from jarvis import remote

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "export", lambda fmt="json": {"count": 1, "memories": [_BOX_MEMORIES[0]]})

    result = CliRunner().invoke(cli, ["export", "--format", "markdown", "-o", "-"])
    assert result.exit_code == 0, result.output
    assert "# Jarvis Memory Export" in result.output
    assert "Box note one about tailscale mesh" in result.output


def test_export_client_mode_filters_client_side(tmp_path, monkeypatch):
    from jarvis import remote

    monkeypatch.setattr("jarvis.remote.is_remote", lambda: True)
    monkeypatch.setattr(remote, "export", lambda fmt="json": {"count": 2, "memories": _BOX_MEMORIES})
    monkeypatch.setattr("jarvis.cli.Store",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("local store used in client mode")))

    out = tmp_path / "client-filtered.json"
    result = CliRunner().invoke(cli, ["export", "--format", "json", "--tier", "session", "--output", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["memories"][0]["id"] == "box-2"
