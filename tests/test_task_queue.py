"""
Tests for jarvis/task_queue.py

Covers:
  - Task lifecycle: add -> approve -> start -> complete / fail / block
  - Retry-on-failure (attempts vs max_attempts)
  - reject, stats, priority ordering, update helpers
"""
from __future__ import annotations

from jarvis.task_queue import TaskQueue


def _make(tmp_path):
    return TaskQueue(db_path=tmp_path / "tasks.db")


# ── Basic lifecycle ──────────────────────────────────────────────────────────

def test_add_task_creates_pending_review(tmp_path):
    tq = _make(tmp_path)
    try:
        tid = tq.add_task("Do thing", description="desc", agent="code", priority=2)
        task = tq.get_task(tid)
        assert task is not None
        assert task["status"] == "pending_review"
        assert task["agent"] == "code"
        assert task["priority"] == 2
        assert task["description"] == "desc"
    finally:
        tq.close()


def test_approve_then_start(tmp_path):
    tq = _make(tmp_path)
    try:
        tid = tq.add_task("T")
        assert tq.approve_task(tid) is True
        assert tq.get_task(tid)["status"] == "approved"
        assert tq.start_task(tid) is True
        task = tq.get_task(tid)
        assert task["status"] == "in_progress"
        assert task["attempts"] == 1
    finally:
        tq.close()


def test_complete(tmp_path):
    tq = _make(tmp_path)
    try:
        tid = tq.add_task("T")
        tq.approve_task(tid)
        tq.start_task(tid)
        assert tq.complete_task(tid, result="done", commit_hash="abc123") is True
        task = tq.get_task(tid)
        assert task["status"] == "completed"
        assert task["commit_hash"] == "abc123"
    finally:
        tq.close()


def test_start_requires_approved(tmp_path):
    tq = _make(tmp_path)
    try:
        tid = tq.add_task("T")
        # Not approved yet -> cannot start
        assert tq.start_task(tid) is False
        assert tq.get_task(tid)["status"] == "pending_review"
    finally:
        tq.close()


def test_approve_unknown_returns_false(tmp_path):
    tq = _make(tmp_path)
    try:
        assert tq.approve_task("missing") is False
    finally:
        tq.close()


# ── Retry / failure ──────────────────────────────────────────────────────────

def test_failure_below_max_attempts_requeues(tmp_path):
    tq = _make(tmp_path)
    try:
        tid = tq.add_task("T")
        tq.approve_task(tid)
        tq.start_task(tid)  # attempts = 1
        assert tq.fail_task(tid, error="boom") is True
        task = tq.get_task(tid)
        assert task["status"] == "approved"  # requeued for retry
        assert task["error"] == "boom"
    finally:
        tq.close()


def test_failure_after_max_attempts_blocks(tmp_path):
    tq = _make(tmp_path)
    try:
        tid = tq.add_task("T")
        tq.approve_task(tid)
        # Cycle start->fail max_attempts times; each fail re-queues (attempts
        # increments on start_task), and the final one exceeds max -> blocked.
        for _ in range(tq.get_task(tid)["max_attempts"]):
            tq.start_task(tid)
            tq.fail_task(tid, error="boom")
        task = tq.get_task(tid)
        assert task["status"] == "blocked"
        assert task["error"] == "boom"
    finally:
        tq.close()


def test_reject(tmp_path):
    tq = _make(tmp_path)
    try:
        tid = tq.add_task("T")
        assert tq.reject_task(tid) is True
        assert tq.get_task(tid)["status"] == "failed"
        # Cannot reject twice
        assert tq.reject_task(tid) is False
    finally:
        tq.close()


# ── Approve-all / priority / helpers ─────────────────────────────────────────

def test_approve_all(tmp_path):
    tq = _make(tmp_path)
    try:
        tq.add_task("A")
        tq.add_task("B")
        assert tq.approve_all() == 2
    finally:
        tq.close()


def test_next_approved_task_priority_order(tmp_path):
    tq = _make(tmp_path)
    try:
        low = tq.add_task("low", priority=5)
        high = tq.add_task("high", priority=1)
        mid = tq.add_task("mid", priority=3)
        tq.approve_task(low)
        tq.approve_task(high)
        tq.approve_task(mid)
        nxt = tq.next_approved_task()
        assert nxt["id"] == high
    finally:
        tq.close()


def test_update_priority_and_agent(tmp_path):
    tq = _make(tmp_path)
    try:
        tid = tq.add_task("T", agent="code", priority=3)
        assert tq.update_priority(tid, 1) is True
        assert tq.update_agent(tid, "qa") is True
        task = tq.get_task(tid)
        assert task["priority"] == 1
        assert task["agent"] == "qa"
    finally:
        tq.close()


def test_stats(tmp_path):
    tq = _make(tmp_path)
    try:
        tq.add_task("A")
        tq.add_task("B")
        tq.add_task("C")
        stats = tq.stats()
        assert stats.get("pending_review") == 3
    finally:
        tq.close()