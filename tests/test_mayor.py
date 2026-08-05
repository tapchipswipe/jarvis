"""
Tests for jarvis/mayor.py

Covers:
  - get_mode day/night boundaries
  - parse_idea: JSON success path + graceful fallback
  - submit_idea queues a task with the right agent
  - auto_approve_old_tasks (and its fresh-task guard)
  - run_tests_and_maybe_revert: protections around git revert
    (dry_run, JARVIS_ALLOW_REVERT env, dirty-tree guard)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from jarvis.mayor import (
    Mayor,
    get_mode,
    parse_idea,
    run_tests_and_maybe_revert,
)

# ── get_mode ─────────────────────────────────────────────────────────────────

def test_get_mode_coding_window():
    tz = timezone.utc
    assert get_mode(datetime(2026, 1, 5, 8, 0, tzinfo=tz)) == "coding"
    assert get_mode(datetime(2026, 1, 5, 12, 0, tzinfo=tz)) == "coding"
    assert get_mode(datetime(2026, 1, 5, 22, 59, tzinfo=tz)) == "coding"


def test_get_mode_memory_window():
    tz = timezone.utc
    assert get_mode(datetime(2026, 1, 5, 23, 0, tzinfo=tz)) == "memory"
    assert get_mode(datetime(2026, 1, 5, 0, 30, tzinfo=tz)) == "memory"
    assert get_mode(datetime(2026, 1, 5, 7, 59, tzinfo=tz)) == "memory"


# ── parse_idea ───────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def test_parse_idea_success():
    body = json.dumps({"response": '{"agent": "qa", "title": "Run the tests", "priority": 2}'}).encode()
    with patch("jarvis.mayor.urllib.request.urlopen", return_value=_FakeResp(body)):
        task = parse_idea("Check tests")
    assert task["agent"] == "qa"
    assert task["title"] == "Run the tests"
    assert task["priority"] == 2


def test_parse_idea_fallback_on_network_error():
    with patch("jarvis.mayor.urllib.request.urlopen", side_effect=OSError("offline")):
        task = parse_idea("Build a feature")
    assert task["agent"] == "code"
    assert task["title"] == "Build a feature"
    assert task["priority"] == 3


def test_parse_idea_clamps_priority():
    body = json.dumps({"response": '{"agent": "code", "title": "t", "priority": 99}'}).encode()
    with patch("jarvis.mayor.urllib.request.urlopen", return_value=_FakeResp(body)):
        task = parse_idea("x")
    assert task["priority"] == 5


def test_parse_idea_invalid_agent_falls_back_to_code():
    body = json.dumps({"response": '{"agent": "singer", "title": "t", "priority": 3}'}).encode()
    with patch("jarvis.mayor.urllib.request.urlopen", return_value=_FakeResp(body)):
        task = parse_idea("x")
    assert task["agent"] == "code"


# ── Mayor instance (temp task queue) ─────────────────────────────────────────

@pytest.fixture()
def mayor(tmp_path, monkeypatch):
    from jarvis.task_queue import TaskQueue

    queue = TaskQueue(db_path=tmp_path / "tq.db")
    monkeypatch.setattr("jarvis.mayor.TaskQueue", lambda *a, **k: queue)
    m = Mayor(project_root=tmp_path)
    return m, queue


def test_submit_idea_queues_task(mayor, monkeypatch):
    m, q = mayor
    monkeypatch.setattr(
        "jarvis.mayor.parse_idea",
        lambda idea: {"agent": "research", "title": "Explore", "description": "D", "priority": 1},
    )
    result = m.submit_idea("Some research idea")
    assert result["status"] == "pending_review"
    assert result["agent"] == "research"
    task = q.get_task(result["task_id"])
    assert task["title"] == "Explore"


def test_auto_approve_skips_fresh_tasks(mayor):
    m, q = mayor
    tid = q.add_task("fresh task")
    m.auto_approve_old_tasks()
    assert q.get_task(tid)["status"] == "pending_review"


def test_auto_approve_old_tasks(mayor):
    m, q = mayor
    tid = q.add_task("old task")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    q.conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (old_ts, tid))
    q.conn.commit()
    m.auto_approve_old_tasks()
    assert q.get_task(tid)["status"] == "approved"


# ── run_tests_and_maybe_revert ───────────────────────────────────────────────

def _completed(cmd, code, stdout="", stderr=""):
    return CompletedProcess(cmd, code, stdout=stdout, stderr=stderr)


def _patch_git_with(monkeypatch, pytest_code, porcelain_out):
    results = []

    def fake_run(cmd, *a, **k):
        joined = " ".join(cmd)
        if "pytest" in joined:
            results.append(("pytest", cmd))
            return _completed(cmd, pytest_code, stdout="test output")
        if "--porcelain" in joined:
            results.append(("porcelain", cmd))
            return _completed(cmd, 0, stdout=porcelain_out)
        if "revert" in joined:
            results.append(("revert", cmd))
            return _completed(cmd, 0)
        results.append(("other", cmd))
        return _completed(cmd, 0)

    patcher = patch("jarvis.mayor.subprocess.run", side_effect=fake_run)
    patcher.start()
    return results, patcher


def test_run_no_commit(tmp_path):
    ok, msg = run_tests_and_maybe_revert(project_root=tmp_path, commit_hash=None)
    assert ok is True
    assert "No commit" in msg


def test_run_passing_tests_no_revert(tmp_path, monkeypatch):
    results, patcher = _patch_git_with(monkeypatch, pytest_code=0, porcelain_out="")
    try:
        ok, _ = run_tests_and_maybe_revert(project_root=tmp_path, commit_hash="abc1234")
        assert ok is True
        cmds = [c for tag, c in results if tag == "revert"]
        assert cmds == []  # no revert on green tests
    finally:
        patcher.stop()


def test_run_failing_tests_dry_run_skips_revert(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_ALLOW_REVERT", raising=False)
    results, patcher = _patch_git_with(monkeypatch, pytest_code=1, porcelain_out="")
    try:
        ok, msg = run_tests_and_maybe_revert(project_root=tmp_path, commit_hash="abc1234", dry_run=True)
        assert ok is False
        assert "revert disabled" in msg
        assert [c for tag, c in results if tag == "revert"] == []
    finally:
        patcher.stop()


def test_run_failing_tests_env_disables_revert(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOW_REVERT", "0")
    results, patcher = _patch_git_with(monkeypatch, pytest_code=1, porcelain_out="")
    try:
        ok, msg = run_tests_and_maybe_revert(project_root=tmp_path, commit_hash="abc1234")
        assert ok is False
        assert "revert disabled" in msg
        assert [c for tag, c in results if tag == "revert"] == []
    finally:
        patcher.stop()


def test_run_failing_tests_dirty_tree_skips_revert(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_ALLOW_REVERT", raising=False)
    results, patcher = _patch_git_with(monkeypatch, pytest_code=1, porcelain_out=" M jarvis/x.py\n")
    try:
        ok, msg = run_tests_and_maybe_revert(project_root=tmp_path, commit_hash="abc1234")
        assert ok is False
        assert "working tree dirty" in msg
        assert [c for tag, c in results if tag == "revert"] == []
    finally:
        patcher.stop()


def test_run_failing_tests_clean_tree_reverts(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_ALLOW_REVERT", raising=False)
    results, patcher = _patch_git_with(monkeypatch, pytest_code=1, porcelain_out="")
    try:
        ok, msg = run_tests_and_maybe_revert(project_root=tmp_path, commit_hash="abc1234")
        assert ok is False
        assert "reverted" in msg
        assert [c for tag, c in results if tag == "revert"] != []
    finally:
        patcher.stop()