"""Tests for the `jarvis export` CLI command (Phase 5)."""
from __future__ import annotations

import json

from click.testing import CliRunner
from unittest.mock import patch

from jarvis.cli import cli

EXPECTED_CONTENTS = {
    "mem-1": "Jarvis remembers a fact about the user.",
    "mem-2": "Lunch with Sam on Friday.",
}


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
