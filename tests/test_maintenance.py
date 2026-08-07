"""
Tests for jarvis/maintenance.py (callable reindex/promote routines) and the
Mayor idle-maintenance loop.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.maintenance import promote_old, reindex_missing


def test_promote_old_calls_store(tmp_path):
    with patch("jarvis.maintenance.Store") as mock_store_cls:
        store = mock_store_cls.return_value
        store.promote_raw_to_session.return_value = 7
        assert promote_old(days=7, limit=100) == 7
        store.promote_raw_to_session.assert_called_once_with(days=7, limit=100)


def test_reindex_missing_calls_store(tmp_path):
    with patch("jarvis.maintenance.Store") as mock_store_cls, \
         patch("jarvis.maintenance.get_embedding", return_value=[0.1] * 8):
        store = mock_store_cls.return_value
        store.get_unembedded.return_value = [
            {"id": "m1", "content": "c1", "source": "s", "timestamp": "t1",
             "tier": "raw", "weight": 0.3, "route": "unclassified"}
        ]
        assert reindex_missing(limit=5) == 1
        store.collection.add.assert_called_once()
        store.mark_embedded.assert_called_once_with("m1")


def test_reindex_missing_accepts_provided_store(tmp_path):
    with patch("jarvis.maintenance.get_embedding", return_value=[0.1] * 8):
        provided = type("S", (), {
            "get_unembedded": lambda self, limit=200: [],
            "collection": object(),
            "mark_embedded": lambda *a, **k: None,
        })()
        # must not close a caller-provided store / open its own
        assert reindex_missing(store=provided, limit=5) == 0


def test_reindex_missing_skips_failed_embedding(tmp_path):
    """When get_embedding fails (None), the row must stay un-embedded so a later
    reindex run retries it instead of committing a degenerate zero-vector."""
    with patch("jarvis.maintenance.get_embedding", return_value=None):
        store = type("S", (), {
            "get_unembedded": lambda self, limit=200: [
                {"id": "m1", "content": "c1", "source": "s", "timestamp": "t1",
                 "tier": "raw", "weight": 0.3, "route": "unclassified"}
            ],
            "collection": MagicMock(),
            "mark_embedded": MagicMock(),
        })()
        assert reindex_missing(store=store, limit=5) == 0
        store.collection.add.assert_not_called()
        store.mark_embedded.assert_not_called()


def test_reindex_missing_skips_on_vector_write_failure(tmp_path):
    """If the vector write raises, the row must not be marked embedded (retry later)."""
    with patch("jarvis.maintenance.get_embedding", return_value=[0.1] * 8):
        col = MagicMock()
        col.add.side_effect = RuntimeError("chroma down")
        store = type("S", (), {
            "get_unembedded": lambda self, limit=200: [
                {"id": "m1", "content": "c1", "source": "s", "timestamp": "t1",
                 "tier": "raw", "weight": 0.3, "route": "unclassified"}
            ],
            "collection": col,
            "mark_embedded": MagicMock(),
        })()
        assert reindex_missing(store=store, limit=5) == 0
        store.mark_embedded.assert_not_called()


# ── Mayor idle maintenance ───────────────────────────────────────────────────

def test_mayor_idle_skips_when_tasks_approval_pending():
    from jarvis.mayor import Mayor
    m = Mayor(project_root="/tmp")
    m.task_queue = type("Q", (), {
        "next_approved_task": lambda self: {"id": "x"},
        "close": lambda self: None,
    })()
    m._maybe_idle_maintenance()
    # Since a task is queued, nothing should run — timestamps stay at 0.
    assert m._last_reindex == 0.0
    assert m._last_promote == 0.0
    m.task_queue.close()


def test_mayor_idle_runs_reindex():
    from jarvis.mayor import Mayor
    m = Mayor(project_root="/tmp")
    m.task_queue = type("Q", (), {
        "next_approved_task": lambda self: None,
        "close": lambda self: None,
    })()
    m._last_reindex = 0.0
    m._last_promote = 0.0
    m.MAINT_REINDEX_EVERY = 0  # always due
    m.MAINT_PROMOTE_EVERY = 1 << 60  # never due
    with patch.object(m, "_ollama_busy", return_value=False), \
         patch("jarvis.maintenance.reindex_missing", return_value=3) as m_reindex:
        m._maybe_idle_maintenance()
    m_reindex.assert_called_once()
    assert m._last_reindex > 0.0
    assert m._last_promote == 0.0
    m.task_queue.close()


def test_mayor_idle_skips_when_ollama_busy():
    from jarvis.mayor import Mayor
    m = Mayor(project_root="/tmp")
    m.task_queue = type("Q", (), {
        "next_approved_task": lambda self: None,
        "close": lambda self: None,
    })()
    m._last_reindex = m._last_promote = 0.0
    m.MAINT_REINDEX_EVERY = m.MAINT_PROMOTE_EVERY = 0
    with patch.object(m, "_ollama_busy", return_value=True):
        m._maybe_idle_maintenance()
    assert m._last_reindex == 0.0
    assert m._last_promote == 0.0
    m.task_queue.close()