"""
Tests for jarvis/consolidation.py

Covers:
  - cluster_by_topic grouping logic
  - summarize_cluster store handling (uses Brain, mocked)
  - run_daily / run_weekly / run_monthly don't crash when store is empty
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.consolidation import (
    PROMPT_DAILY,
    cluster_by_topic,
    run_daily,
    run_monthly,
    run_weekly,
    summarize_cluster,
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


@patch("jarvis.consolidation._ollama_chat")
def test_summarize_cluster_no_store_opened(mock_ollama):
    """summarize_cluster must NOT open a Store/Brain — it only needs the LLM to
    summarize pre-loaded text. Opening a Store would create a second Chroma
    PersistentClient handle on the same dir (single-writer hazard) and run a
    pointless embedding + empty n_results=0 search."""
    mock_ollama.return_value = {"message": {"content": "summary text"}}

    memories = [{"id": "m1", "content": "hello", "timestamp": "2025-01-01", "source": "test", "tags": []}]
    result = summarize_cluster(memories, PROMPT_DAILY)

    assert result == "summary text"
    mock_ollama.assert_called_once()
    assert mock_ollama.call_args.kwargs["messages"][0] == {"role": "system", "content": PROMPT_DAILY}
    # The combined memory text is passed straight to the LLM as the user message.
    assert mock_ollama.call_args.kwargs["messages"][1]["content"].startswith("[2025-01-01] [test] hello")


@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation._ollama_chat")
def test_summarize_cluster_no_second_store(mock_ollama, mock_store_cls):
    """Even when the surrounding run already holds an open Store, summarize_cluster
    never constructs another one — it only calls the LLM on the pre-loaded text,
    so exactly one Store (the caller's) exists at a time."""
    outer = MagicMock()
    mock_store_cls.return_value = outer
    mock_ollama.return_value = {"message": {"content": "summary text"}}

    memories = [{"id": "m1", "content": "hello", "timestamp": "2025-01-01", "source": "test", "tags": []}]
    result = summarize_cluster(memories, PROMPT_DAILY)

    assert result == "summary text"
    mock_store_cls.assert_not_called()
    outer.close.assert_not_called()


@patch("jarvis.consolidation._ollama_chat")
def test_summarize_cluster_ollama_exception_returns_none(mock_ollama):
    mock_ollama.side_effect = RuntimeError("oom")

    result = summarize_cluster([{"id": "m1", "content": "x", "timestamp": "t", "source": "s", "tags": []}], PROMPT_DAILY)
    assert result is None


@patch("jarvis.consolidation._ollama_chat")
def test_summarize_cluster_empty_answer_returns_none(mock_ollama):
    mock_ollama.return_value = {"message": {"content": ""}}

    result = summarize_cluster([{"id": "m1", "content": "x", "timestamp": "t", "source": "s", "tags": []}], PROMPT_DAILY)
    assert result is None


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


@patch("jarvis.consolidation.Store")
@patch("jarvis.consolidation.get_embedding")
@patch("jarvis.consolidation.summarize_cluster")
def test_run_daily_dedup_fires_on_second_run(mock_summarize, mock_emb, mock_store_cls):
    """Repeated consolidation on the same input must not create duplicates.

    The fingerprint date must be stable (day, not a live per-run timestamp) so
    store.exists() fires on the second run and the memory is not re-added.
    """
    added = set()

    def fake_exists(fid):
        return fid in added

    def fake_add(fid, *args, **kwargs):
        added.add(fid)

    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get_recent_raw.return_value = [
        {"id": f"m{i}", "content": f"content {i}", "timestamp": f"2025-01-0{i%9+1}T10:00:00", "source": "test", "tags": ["work"]}
        for i in range(25)
    ]
    # store.exists() returns whether the fid was already added on a prior run,
    # simulating the real dedup path across two consolidation runs.
    mock_store.exists.side_effect = fake_exists
    mock_store.add.side_effect = fake_add
    mock_store.conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
    mock_summarize.return_value = "Daily summary"
    mock_emb.return_value = [0.1] * 768

    first = run_daily()
    second = run_daily()

    # First run adds the consolidated memories; the second run is fully deduped.
    assert first == 1
    assert second == 0
    assert mock_store.add.call_count == 1


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
