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
    ensure_model_loaded,
    get_agent,
    get_mode,
    parse_idea,
    run_agent_on_task,
    run_tests_and_maybe_revert,
    unload_model,
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


def test_idle_maintenance_runs_reindex_and_promote(mayor, monkeypatch):
    m, q = mayor
    monkeypatch.setattr(m, "_ollama_busy", lambda: False)
    monkeypatch.setattr(m, "MAINT_REINDEX_EVERY", 1.0)
    monkeypatch.setattr(m, "MAINT_PROMOTE_EVERY", 1.0)
    m._last_reindex = 0.0
    m._last_promote = 0.0
    calls = []
    monkeypatch.setattr("jarvis.maintenance.reindex_missing",
                        lambda limit=200: calls.append(("reindex", limit)) or 5)
    monkeypatch.setattr("jarvis.maintenance.promote_old",
                        lambda days=7, limit=500: calls.append(("promote", days, limit)) or 3)

    m._maybe_idle_maintenance()

    assert ("reindex", 200) in calls
    assert ("promote", 7, 500) in calls
    assert m._last_reindex > 1.0   # advanced past the 0.0 seed
    assert m._last_promote > 1.0
    q.close()


def test_idle_maintenance_skips_when_ollama_busy(mayor, monkeypatch):
    m, q = mayor
    monkeypatch.setattr(m, "_ollama_busy", lambda: True)  # VRAM contention guard
    monkeypatch.setattr(m, "MAINT_REINDEX_EVERY", 1.0)
    m._last_reindex = 0.0
    called = []
    monkeypatch.setattr("jarvis.maintenance.reindex_missing",
                        lambda limit=200: called.append(1))

    m._maybe_idle_maintenance()

    assert called == []                 # maintenance did not run
    assert m._last_reindex == 0.0       # timer not advanced
    q.close()


def test_idle_maintenance_skips_when_task_queued(mayor, monkeypatch):
    m, q = mayor
    monkeypatch.setattr(m, "_ollama_busy", lambda: False)
    monkeypatch.setattr(m, "MAINT_REINDEX_EVERY", 1.0)
    m._last_reindex = 0.0
    called = []
    monkeypatch.setattr("jarvis.maintenance.reindex_missing",
                        lambda limit=200: called.append(1))
    tid = q.add_task("queued work")
    q.approve_task(tid)

    m._maybe_idle_maintenance()
    assert called == []                 # approved work present -> leave to dispatch
    q.close()


# ── model load/unload (ollama VRAM discipline) ───────────────────────────────

class _DummyResp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b"{}"


def test_unload_model_success(monkeypatch):
    sent = {}
    def _fake(req, timeout=None):
        sent["url"] = req.full_url
        sent["payload"] = req.data
        return _DummyResp()
    monkeypatch.setattr("jarvis.mayor.urllib.request.urlopen", _fake)
    unload_model("qwen2.5:7b")
    assert "api/generate" in sent["url"]
    assert b"keep_alive" in sent["payload"]


def test_unload_model_failure_is_quiet(monkeypatch):
    monkeypatch.setattr("jarvis.mayor.urllib.request.urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(
                            OSError("no ollama")))
    unload_model("qwen2.5:7b")  # must not raise


def test_ensure_model_loaded_true_and_false(monkeypatch):
    monkeypatch.setattr("jarvis.mayor.urllib.request.urlopen",
                        lambda req, timeout=None: _DummyResp())
    assert ensure_model_loaded("model-x") is True
    monkeypatch.setattr("jarvis.mayor.urllib.request.urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(
                            OSError("down")))
    assert ensure_model_loaded("model-x") is False


# ── agent dispatch ───────────────────────────────────────────────────────────

def test_run_agent_on_task_success(monkeypatch, tmp_path):
    class _FakeAgent:
        def execute(self, task):
            return {"success": True, "result": "ok", "commit_hash": "abc",
                    "files_changed": ["a.py"]}
    monkeypatch.setattr("jarvis.mayor.get_agent", lambda name, root: _FakeAgent())
    res = run_agent_on_task({"agent": "code", "id": "t1"}, tmp_path)
    assert res["success"] is True and res["commit_hash"] == "abc"


def test_run_agent_on_task_crash_returns_clean_dict(monkeypatch, tmp_path):
    class _Boom:
        def execute(self, task):
            raise RuntimeError("agent blew up")
    monkeypatch.setattr("jarvis.mayor.get_agent", lambda name, root: _Boom())
    res = run_agent_on_task({"agent": "code", "id": "t1"}, tmp_path)
    assert res["success"] is False
    assert "Agent crashed" in res["result"]


def test_get_agent_returns_executable(tmp_path):
    agent = get_agent("code", tmp_path)
    assert hasattr(agent, "execute")


# ── health checks ────────────────────────────────────────────────────────────

def test_run_health_checks_all_healthy(mayor, monkeypatch):
    m, q = mayor
    m.running = True
    monkeypatch.setattr(
        "jarvis.mayor.urllib.request.urlopen",
        lambda req, timeout=None: _json_resp(
            {"models": [{"name": "qwen2.5:7b"}]}))
    checks = m.run_health_checks()
    assert checks["ollama"]["healthy"] is True
    assert checks["task_queue"]["healthy"] is True
    assert checks["filesystem"]["healthy"] is True
    assert checks["mayor"]["healthy"] is True
    q.close()


def test_run_health_checks_ollama_down(mayor, monkeypatch):
    m, q = mayor
    m.running = True
    monkeypatch.setattr("jarvis.mayor.urllib.request.urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(
                            OSError("down")))
    checks = m.run_health_checks()
    assert checks["ollama"]["healthy"] is False
    q.close()


def _json_resp(obj):
    class _R(_DummyResp):
        def read(self): return json.dumps(obj).encode()
    return _R()


# ── mode switching ───────────────────────────────────────────────────────────

def test_check_mode_switch_to_memory_starts_night_mode(mayor, monkeypatch):
    m, q = mayor
    m.current_mode = "coding"
    monkeypatch.setattr("jarvis.mayor.get_mode", lambda *a, **k: "memory")
    monkeypatch.setattr("jarvis.mayor.unload_model", lambda *a, **k: None)
    monkeypatch.setattr("jarvis.mayor.ensure_model_loaded", lambda *a, **k: True)
    started = []
    monkeypatch.setattr(m, "_start_night_mode", lambda: started.append(1))
    m.check_mode_switch()
    assert m.current_mode == "memory"
    assert started == [1]
    q.close()


def test_check_mode_switch_no_change(mayor, monkeypatch):
    m, q = mayor
    m.current_mode = "memory"
    monkeypatch.setattr("jarvis.mayor.get_mode", lambda *a, **k: "memory")
    called = []
    monkeypatch.setattr(m, "_start_night_mode", lambda: called.append(1))
    m.check_mode_switch()
    assert called == []  # no switch -> no night-mode start
    q.close()


# ── task dispatch ────────────────────────────────────────────────────────────

def test_dispatch_next_task_skips_outside_coding_mode(mayor):
    m, _q = mayor
    m.current_mode = "memory"
    assert m.dispatch_next_task() is None


def test_dispatch_next_task_completes_task(mayor, monkeypatch):
    m, q = mayor
    m.current_mode = "coding"
    tid = q.add_task("ship the thing", agent="code")
    q.approve_task(tid)

    monkeypatch.setattr("jarvis.mayor.run_agent_on_task",
                        lambda task, root: {"success": True, "result": "done",
                                            "commit_hash": "c1"})
    monkeypatch.setattr("jarvis.mayor.run_tests_and_maybe_revert",
                        lambda root, commit: (True, "green"))
    m.dispatch_next_task()
    assert q.get_task(tid)["status"] == "completed"
    q.close()
