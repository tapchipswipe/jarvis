"""Tests for the decision_log — append_decision writes a well-formed JSONL record."""
from __future__ import annotations

import json

from jarvis import decision_log


def test_append_decision_writes_record(tmp_path, monkeypatch):
    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr(decision_log, "_decision_log", lambda: log_path)

    decision_log.append_decision(
        memory_id="m-123", route="idea_capture", confidence="0.92",
        envelope={"tags": ["idea"], "entities": []}, applied=1,
    )

    assert log_path.exists()
    lines = [ln for ln in log_path.read_text(encoding="utf-8").strip().splitlines() if ln]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["memory_id"] == "m-123"
    assert rec["route"] == "idea_capture"
    assert rec["confidence"] == "0.92"
    assert rec["envelope"] == {"tags": ["idea"], "entities": []}
    assert rec["applied"] == 1
    assert rec.get("ts")


def test_append_decision_appends_multiple(tmp_path, monkeypatch):
    log_path = tmp_path / "nested" / "decisions.jsonl"
    monkeypatch.setattr(decision_log, "_decision_log", lambda: log_path)
    decision_log.append_decision("a", "r1", "0.5", {})
    decision_log.append_decision("b", "r2", "0.6", {}, applied=2)
    lines = [ln for ln in log_path.read_text(encoding="utf-8").strip().splitlines() if ln]
    assert len(lines) == 2
    assert json.loads(lines[0])["memory_id"] == "a"
    assert json.loads(lines[1])["applied"] == 2
