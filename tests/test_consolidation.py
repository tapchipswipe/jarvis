"""
Tests for jarvis/consolidation.py

Covers:
  - cluster_by_topic grouping logic
  - summarize_cluster store handling (uses Brain, mocked)
  - run_daily / run_weekly / run_monthly don't crash when store is empty
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from jarvis.consolidation import (
    cluster_by_topic,
    summarize_cluster,
    run_daily,
    run_weekly,
    run_monthly,
    PROMPT_DAILY,
    PROMPT_WEEKLY,
    PROMPT_MONTHLY,
)


# ── cluster_by_topic ─────────────────────────────────────────────────────────

def test_cluster_by_topic_groups_by_tag():
    memories = [
        {"id": "a", "content": "x", "tags": ["work", "urgent"]},
        {"id": "b", "content": "y", "tags": ["work", "meeting"]},
        {"id": "c", "content": "z", "tags": ["personal"]},
    ]
    clusters = cluster_by_topic(memories)
    assert len(clusters) == 2
    work_cluster = [c for c in clusters if len(c) == 2][0]
    assert "work" in work_cluster[0]["tags"]


def test_cluster_by_topic_empty_list():
    assert cluster_by_topic([]) == []


def test_cluster_by_topic_respects_max_clusters():
    memories = [{"id": str(i), "content": "x", "tags": [f"tag{i}"]} for i in range(10)]
    clusters = cluster_by_topic(memories, max_clusters=3)
    assert len(clusters) <= 3


# ── summarize_cluster ─────────────────────────────────────────────────────────

def test_summarize_cluster_empty_returns_none():
    assert summarize_cluster([], PROMPT_DAILY) is None


@patch("jarvis.consolidation.Brain")
@patch("jarvis.consolidation.Store")
def test_summarize_cluster_uses_store_param(mock_store_cls, mock_brain_cls):
    """summarize_cluster should create its own Store and close it."""
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_brain = MagicMock()
    mock_brain.query.return_value = ("summary text", [])
    mock_brain_cls.return_value = mock_brain

    memories = [{"id": "m1", "content": "hello", "timestamp": "2025-01-01", "source": "test", "tags": []}]
    result = summarize_cluster(memories, PROMPT_DAILY)

    assert result == "summary text"
    mock_store_cls.assert_called_once()
    mock_store.close.assert_called_once()


@patch("jarvis.consolidation.Brain")
@patch("jarvis.consolidation.Store")
def test_summarize_cluster_brain_exception_returns_none(mock_store_cls, mock_brain_cls):
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_brain = MagicMock()
    mock_brain.query.side_effect = RuntimeError("oom")
    mock_brain_cls.return_value = mock_brain

    result = summarize_cluster([{"id": "m1", "content": "x", "timestamp": "t", "source": "s", "tags": []}], PROMPT_DAILY)
    assert result is None
    mock_store.close.assert_called_once()


# ── run_daily ─────────────────────────────────────────────────────────────────

@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation.get_embedding")
@patch("jarvis.consolidation.summarize_cluster")
def test_run_daily_empty_store_returns_zero(mock_summarize, mock_emb, mock_store_cls):
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get_recent_raw.return_value = []

    result = run_daily()
    assert result == 0
    mock_store.close.assert_called_once()


@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation.get_embedding")
@patch("jarvis.consolidation.summarize_cluster")
def test_run_daily_too_few_raw_returns_zero(mock_summarize, mock_emb, mock_store_cls):
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get_recent_raw.return_value = [{"id": "m1", "content": "x", "timestamp": "t", "source": "s", "tags": []}]

    result = run_daily()
    assert result == 0
    mock_store.close.assert_called_once()


@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation.get_embedding")
@patch("jarvis.consolidation.summarize_cluster")
def test_run_daily_with_summaries(mock_summarize, mock_emb, mock_store_cls):
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get_recent_raw.return_value = [
        {"id": f"m{i}", "content": f"content {i}", "timestamp": f"2025-01-0{i%9+1}T10:00:00", "source": "test", "tags": ["work"]}
        for i in range(25)
    ]
    mock_store.exists.return_value = False
    mock_store.conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
    mock_summarize.return_value = "Daily summary"
    mock_emb.return_value = [0.1] * 768

    result = run_daily()
    assert result > 0
    mock_store.close.assert_called_once()


# ── run_weekly ────────────────────────────────────────────────────────────────

@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation.get_embedding")
@patch("jarvis.consolidation.summarize_cluster")
def test_run_weekly_insufficient_sessions_returns_zero(mock_summarize, mock_emb, mock_store_cls):
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get_by_tier.return_value = []

    result = run_weekly()
    assert result == 0
    mock_store.close.assert_called_once()


@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation.get_embedding")
@patch("jarvis.consolidation.summarize_cluster")
def test_run_weekly_with_sessions(mock_summarize, mock_emb, mock_store_cls):
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get_by_tier.return_value = [
        {"id": f"s{i}", "content": f"session {i}", "timestamp": f"2025-01-0{i%9+1}", "source": "consolidation", "tags": "[]", "tier": "session"}
        for i in range(15)
    ]
    mock_store.exists.return_value = False
    mock_summarize.return_value = "Weekly reflection"
    mock_emb.return_value = [0.1] * 768

    result = run_weekly()
    assert result == 1
    mock_store.close.assert_called_once()


# ── run_monthly ───────────────────────────────────────────────────────────────

@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation.get_embedding")
@patch("jarvis.consolidation.summarize_cluster")
def test_run_monthly_insufficient_reflections_returns_zero(mock_summarize, mock_emb, mock_store_cls):
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get_by_tier.return_value = []

    result = run_monthly()
    assert result == 0
    mock_store.close.assert_called_once()


@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation.get_embedding")
@patch("jarvis.consolidation.summarize_cluster")
def test_run_monthly_with_reflections(mock_summarize, mock_emb, mock_store_cls):
    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get_by_tier.return_value = [
        {"id": f"r{i}", "content": f"reflection {i}", "timestamp": f"2025-01-0{i%9+1}", "source": "consolidation", "tags": "[]", "tier": "reflection"}
        for i in range(6)
    ]
    mock_store.exists.return_value = False
    mock_summarize.return_value = "Monthly arc"
    mock_emb.return_value = [0.1] * 768

    result = run_monthly()
    assert result == 1
    mock_store.close.assert_called_once()
