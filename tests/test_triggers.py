"""
Tests for jarvis/triggers.py

Covers:
  - TimeTrigger cron matching (HH:MM and 5-field cron)
  - EventTrigger fire-once semantics
  - PollTrigger interval logic
  - TriggerEngine dispatch (never propagates exceptions)
  - Action dispatch (notify, brief, escalate)
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.triggers import (
    CLASS_MAP,
    EventTrigger,
    PollTrigger,
    TimeTrigger,
    TriggerContext,
    TriggerEngine,
    TriggerError,
    TriggerLoop,
    _dispatch_action,
    _instantiate,
    load_triggers,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ctx(dt=None, **kwargs):
    if dt is None:
        dt = datetime.now(timezone.utc)
    defaults = dict(
        now=dt,
        last_ingest_ts=None,
        activity_log=[],
        pending_queue=[],
        retry_queue=[],
        trigger_events={},
        memory_count=0,
        last_memory_ts=None,
        extra={},
    )
    defaults.update(kwargs)
    return TriggerContext(**defaults)


# ── TimeTrigger ───────────────────────────────────────────────────────────────

def test_time_trigger_daily_matches():
    t = TimeTrigger(name="morning", actions=[], cron_expr="08:00")
    ctx = _make_ctx(dt=datetime(2025, 6, 15, 8, 0, 0, tzinfo=timezone.utc))
    assert t.should_fire(ctx) is True


def test_time_trigger_daily_no_match():
    t = TimeTrigger(name="morning", actions=[], cron_expr="08:00")
    ctx = _make_ctx(dt=datetime(2025, 6, 15, 9, 0, 0, tzinfo=timezone.utc))
    assert t.should_fire(ctx) is False


def test_time_trigger_cron_field():
    t = TimeTrigger(name="every-5-min", actions=[], cron_expr="*/5 * * * *")
    ctx = _make_ctx(dt=datetime(2025, 6, 15, 10, 5, 0, tzinfo=timezone.utc))
    assert t.should_fire(ctx) is True


def test_time_trigger_cron_no_match():
    t = TimeTrigger(name="every-5-min", actions=[], cron_expr="*/5 * * * *")
    ctx = _make_ctx(dt=datetime(2025, 6, 15, 10, 3, 0, tzinfo=timezone.utc))
    assert t.should_fire(ctx) is False


def test_time_trigger_dedup_same_minute():
    t = TimeTrigger(name="morning", actions=[], cron_expr="08:00")
    dt = datetime(2025, 6, 15, 8, 0, 30, tzinfo=timezone.utc)
    ctx = _make_ctx(dt=dt)
    assert t.should_fire(ctx) is True
    ctx2 = _make_ctx(dt=dt.replace(second=59))
    assert t.should_fire(ctx2) is False


def test_time_trigger_invalid_expr_raises():
    with pytest.raises(TriggerError):
        TimeTrigger(name="bad", actions=[], cron_expr="invalid")


# ── EventTrigger ──────────────────────────────────────────────────────────────

def test_event_trigger_fires_when_event_present():
    t = EventTrigger(name="on-urgent", actions=[], event_name="urgent_email")
    ctx = _make_ctx(trigger_events={"urgent_email": {"fired_at": None}})
    assert t.should_fire(ctx) is True


def test_event_trigger_does_not_fire_when_no_event():
    t = EventTrigger(name="on-urgent", actions=[], event_name="urgent_email")
    ctx = _make_ctx(trigger_events={})
    assert t.should_fire(ctx) is False


def test_event_trigger_one_shot_after_fire():
    t = EventTrigger(name="on-urgent", actions=[], event_name="urgent_email")
    ctx = _make_ctx(trigger_events={"urgent_email": {"fired_at": "2025-01-01T00:00:00+00:00"}})
    assert t.should_fire(ctx) is False


def test_event_trigger_resets_after_window():
    t = EventTrigger(name="on-urgent", actions=[], event_name="urgent_email", reset_after=3600)
    fired = datetime(2025, 6, 15, 7, 0, 0, tzinfo=timezone.utc)
    ctx = _make_ctx(
        dt=datetime(2025, 6, 15, 8, 0, 0, tzinfo=timezone.utc),
        trigger_events={"urgent_email": {"fired_at": fired.isoformat()}},
    )
    assert t.should_fire(ctx) is True


# ── PollTrigger ───────────────────────────────────────────────────────────────

def test_poll_trigger_first_fire():
    t = PollTrigger(name="calendar-check", actions=[], interval_seconds=1800)
    ctx = _make_ctx(trigger_events={})
    assert t.should_fire(ctx) is True


def test_poll_trigger_within_interval():
    t = PollTrigger(name="calendar-check", actions=[], interval_seconds=1800)
    now = datetime.now(timezone.utc)
    ctx = _make_ctx(
        dt=now,
        trigger_events={"calendar-check": {"last_poll_ts": (now - timedelta(seconds=600)).isoformat()}},
    )
    assert t.should_fire(ctx) is False


def test_poll_trigger_after_interval():
    t = PollTrigger(name="calendar-check", actions=[], interval_seconds=1800)
    now = datetime.now(timezone.utc)
    ctx = _make_ctx(
        dt=now,
        trigger_events={"calendar-check": {"last_poll_ts": (now - timedelta(seconds=2000)).isoformat()}},
    )
    assert t.should_fire(ctx) is True


def test_poll_trigger_max_count():
    t = PollTrigger(name="limited", actions=[], interval_seconds=1, count=2)
    t._fire_count = 2
    ctx = _make_ctx()
    assert t.should_fire(ctx) is False


# ── Trigger base / evaluate ───────────────────────────────────────────────────

def test_trigger_disabled_returns_empty():
    t = TimeTrigger(name="disabled", actions=[], cron_expr="08:00", enabled=False)
    ctx = _make_ctx()
    assert t.evaluate(ctx) == []


def test_trigger_unknown_action_type_raises():
    with pytest.raises(TriggerError):
        _dispatch_action({"type": "nonexistent"}, _make_ctx())


# ── _instantiate / CLASS_MAP ──────────────────────────────────────────────────

def test_class_map_has_all_types():
    assert CLASS_MAP["time"] is TimeTrigger
    assert CLASS_MAP["event"] is EventTrigger
    assert CLASS_MAP["poll"] is PollTrigger


def test_instantiate_time_trigger():
    raw = {"type": "time", "name": "test", "cron_expr": "08:00", "actions": []}
    t = _instantiate(raw)
    assert isinstance(t, TimeTrigger)
    assert t.name == "test"


def test_instantiate_unknown_type_raises():
    with pytest.raises(TriggerError):
        _instantiate({"type": "unknown", "name": "x", "actions": []})


# ── TriggerEngine ─────────────────────────────────────────────────────────────

def test_engine_evaluate_never_raises():
    """Engine should catch exceptions from individual triggers."""
    bad_trigger = MagicMock()
    bad_trigger.TYPE = "time"
    bad_trigger.evaluate.side_effect = RuntimeError("boom")

    good_trigger = TimeTrigger(name="ok", actions=[], cron_expr="08:00")
    engine = TriggerEngine([bad_trigger, good_trigger])

    store = MagicMock()
    store.conn.execute.return_value.fetchone.return_value = (0, None)
    state = MagicMock()

    engine.evaluate(store=store, state=state)


def test_engine_raise_event():
    engine = TriggerEngine([])
    engine.raise_event("test_event", {"payload": "value"})
    assert "test_event" in engine._events
    assert engine._events["test_event"]["fired_at"] is not None


def test_engine_updates_poll_ts():
    t = PollTrigger(name="poll-test", actions=[{"type": "notify", "title": "Test", "body": "Poll fired"}], interval_seconds=1)
    engine = TriggerEngine([t])
    store = MagicMock()
    store.conn.execute.return_value.fetchone.return_value = (0, None)
    state = MagicMock()

    with patch("jarvis.triggers._dispatch_action", return_value="ok"):
        engine.evaluate(store=store, state=state)

    assert "poll-test" in engine._events
    assert "last_poll_ts" in engine._events["poll-test"]


# ── Action dispatch ───────────────────────────────────────────────────────────

@patch("jarvis.triggers.send_notification")
def test_action_notify(mock_notify):
    result = _dispatch_action({"type": "notify", "title": "Test", "body": "Hello"}, _make_ctx())
    assert result == "notify:Test"
    mock_notify.assert_called_once_with("Test", "Hello")


@patch("jarvis.triggers.write_briefing")
def test_action_brief(mock_brief):
    mock_brief.return_value = "/tmp/briefing.md"
    result = _dispatch_action({"type": "brief", "title": "Brief", "content": "Content"}, _make_ctx())
    assert result == "brief:/tmp/briefing.md"
    mock_brief.assert_called_once()


@patch("jarvis.triggers.send_notification")
def test_action_escalate(mock_notify):
    result = _dispatch_action({"type": "escalate", "reason": "Check this"}, _make_ctx())
    assert "escalate:" in result
    mock_notify.assert_called_once()


@patch("jarvis.triggers.send_notification")
@patch("jarvis.triggers.write_briefing")
@patch("jarvis.brain.Brain.build_digest", return_value="Here is the digest.")
def test_action_digest(mock_digest, mock_brief, mock_notify):
    from jarvis.triggers import _ACTION_HANDLERS
    mock_brief.return_value = "/tmp/briefing.md"
    assert "digest" in _ACTION_HANDLERS
    # A mock store avoids opening the real DB / touching real data.
    ctx = _make_ctx(store=MagicMock())
    result = _dispatch_action(
        {"type": "digest", "kind": "morning_brief", "title": "Morning"},
        ctx,
    )
    assert result.startswith("digest:morning_brief:")
    mock_digest.assert_called_once()
    mock_brief.assert_called_once()
    mock_notify.assert_called_once()


# ── calendar_poll action (upcoming-events-poll) ───────────────────────────────

def _calendar_store(rows):
    """Build a minimal in-memory sqlite store with calendar-sourced memories."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE memories ("
        " id TEXT PRIMARY KEY, source TEXT, timestamp DATETIME,"
        " content TEXT, superseded INTEGER DEFAULT 0);"
    )
    for i, r in enumerate(rows):
        conn.execute(
            "INSERT INTO memories (id, source, timestamp, content, superseded)"
            " VALUES (?, 'calendar', ?, ?, 0)",
            (f"cal-{i}", r["timestamp"], r["content"]),
        )
    conn.commit()
    store = MagicMock()
    store.conn = conn
    return store, conn


@patch("jarvis.triggers.send_notification")
def test_calendar_poll_no_events_no_notification(mock_notify):
    """Poll with no calendar rows => no notification fires."""
    store, conn = _calendar_store([])
    try:
        ctx = _make_ctx(
            store=store,
            now=datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        )
        result = _dispatch_action(
            {"type": "calendar_poll", "title": "📅 Upcoming Events"}, ctx
        )
        assert result == "calendar_poll:no_events"
        mock_notify.assert_not_called()
    finally:
        conn.close()


@patch("jarvis.triggers.send_notification")
def test_calendar_poll_upcoming_event_notifies(mock_notify):
    """Poll with an event within the window => notification fires with details."""
    store, conn = _calendar_store([
        {
            "timestamp": "2025-06-15T11:00:00",
            "content": "Team Standup\n2025-06-15T11:00:00 → 2025-06-15T11:30:00\nRoom 4",
        },
    ])
    try:
        ctx = _make_ctx(
            store=store,
            now=datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        )
        result = _dispatch_action(
            {"type": "calendar_poll", "title": "📅 Upcoming Events", "window_minutes": 120},
            ctx,
        )
        assert result == "calendar_poll:1"
        mock_notify.assert_called_once()
        title, body = mock_notify.call_args.args
        assert title == "📅 Upcoming Events"
        assert "Team Standup" in body
        assert "11:00" in body
    finally:
        conn.close()


@patch("jarvis.triggers.send_notification")
def test_calendar_poll_event_outside_window_no_notify(mock_notify):
    """An event starting beyond the 2h window must NOT trigger a notification."""
    store, conn = _calendar_store([
        {"timestamp": "2025-06-15T15:00:00", "content": "Much Later\n2025-06-15T15:00:00"},
    ])
    try:
        ctx = _make_ctx(
            store=store,
            now=datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        )
        _dispatch_action(
            {"type": "calendar_poll", "title": "📅 Upcoming Events", "window_minutes": 120},
            ctx,
        )
        mock_notify.assert_not_called()
    finally:
        conn.close()


def test_upcoming_events_poll_advanced_even_when_no_events():
    """The poll still counts as fired (last_poll_ts advances) with zero events."""
    from jarvis.triggers import load_triggers

    trigger = next(
        t for t in load_triggers(config_path=Path("/tmp/nonexistent-triggers.toml"))
        if t.name == "upcoming-events-poll"
    )
    assert trigger.TYPE == "poll"
    assert trigger.actions[0]["type"] == "calendar_poll"
    engine = TriggerEngine([trigger])
    store, conn = _calendar_store([])
    try:
        with patch("jarvis.triggers.send_notification") as mock_notify:
            engine.evaluate(store=store, state=MagicMock())
        mock_notify.assert_not_called()
        assert "last_poll_ts" in engine._events["upcoming-events-poll"]
    finally:
        conn.close()


# ── load_triggers ─────────────────────────────────────────────────────────────

def test_load_triggers_returns_defaults():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        triggers = load_triggers(config_path=Path(tmp) / "nonexistent.toml")
    assert len(triggers) > 0
    names = {t.name for t in triggers}
    assert "morning-brief" in names
    assert "end-of-day-wrap" in names
    assert "upcoming-events-poll" in names


# ── Template rendering ────────────────────────────────────────────────────────

def test_render_template_substitution():
    from jarvis.triggers import _render_template
    ctx = _make_ctx(memory_count=42, last_ingest_ts="2025-01-01T00:00:00+00:00", pending_queue=[{"x": 1}, {"y": 2}])
    result = _render_template("Count: {memory_count}, Pending: {pending_count}", ctx)
    assert "42" in result
    assert "2" in result


# ── TriggerLoop (daemon background thread) ────────────────────────────────────

def test_trigger_loop_evaluates_on_interval():
    """The loop thread evaluates the engine repeatedly on a short interval."""
    engine = MagicMock()
    engine.triggers = []
    loop = TriggerLoop(
        store=MagicMock(), state=MagicMock(), interval=0.05, engine=engine
    )
    loop.start()
    try:
        time.sleep(0.16)   # allow a couple of ticks
        assert engine.evaluate.call_count >= 1
    finally:
        loop.stop()
        loop.join(timeout=2)
    assert not loop.is_alive()


def test_trigger_loop_stops_cleanly():
    """stop() makes the thread exit; join() returns and the thread is gone."""
    engine = MagicMock()
    engine.triggers = []
    loop = TriggerLoop(
        store=object(), state=None, interval=60, engine=engine
    )
    loop.start()
    loop.stop()
    loop.join(timeout=2)
    assert not loop.is_alive()
    # stop() is idempotent-safe (e.g. called twice on shutdown)
    loop.stop()


def test_trigger_loop_stop_wakes_sleeping_thread():
    """A thread parked in its sleep window exits promptly on stop()."""
    engine = MagicMock()
    engine.triggers = []
    loop = TriggerLoop(
        store=object(), state=None, interval=30, engine=engine
    )
    loop.stop()   # signal before the thread wakes -> no evaluation
    loop.start()
    loop.join(timeout=2)
    assert not loop.is_alive()
    assert engine.evaluate.call_count == 0


def test_trigger_loop_loads_triggers_by_default():
    """Without an explicit engine the loop loads triggers via load_triggers()."""
    with patch("jarvis.triggers.load_triggers", return_value=[]) as mock_load:
        loop = TriggerLoop(store=object(), state=None, interval=60)
    mock_load.assert_called_once()
    assert loop.triggers == []


def test_trigger_loop_passes_config_path_to_load_triggers():
    with patch("jarvis.triggers.load_triggers", return_value=[]) as mock_load:
        TriggerLoop(store=object(), config_path=Path("/tmp/custom.toml"))
    mock_load.assert_called_once_with(config_path=Path("/tmp/custom.toml"))


def test_trigger_loop_rejects_non_positive_interval():
    with pytest.raises(TriggerError):
        TriggerLoop(store=object(), interval=0)
    with pytest.raises(TriggerError):
        TriggerLoop(store=object(), interval=-5)


def test_trigger_loop_builds_context_from_store():
    """Each tick the engine reads memory stats from the Store for the context."""
    engine = TriggerEngine([])
    store = MagicMock()
    store.conn.execute.return_value.fetchone.return_value = (7, "2025-06-01T12:00:00+00:00")
    loop = TriggerLoop(store=store, state=MagicMock(), interval=0.05, engine=engine)
    loop.start()
    try:
        time.sleep(0.16)
    finally:
        loop.stop()
        loop.join(timeout=2)
    assert not loop.is_alive()
    # memory_count / last_memory_ts were read from the store's sqlite conn
    assert store.conn.execute.call_count >= 1


def test_trigger_loop_dispatches_due_actions():
    """A due poll trigger dispatches its notify action from inside the loop."""
    trigger = PollTrigger(
        name="loop-poll",
        actions=[{"type": "notify", "title": "Loop", "body": "Fired"}],
        interval_seconds=1,
    )
    engine = TriggerEngine([trigger])
    store = MagicMock()
    store.conn.execute.return_value.fetchone.return_value = (0, None)
    loop = TriggerLoop(store=store, state=MagicMock(), interval=0.05, engine=engine)
    with patch("jarvis.triggers._dispatch_action", return_value="ok") as mock_dispatch:
        loop.start()
        try:
            time.sleep(0.16)
        finally:
            loop.stop()
            loop.join(timeout=2)
    mock_dispatch.assert_called()


def test_trigger_loop_survives_engine_errors():
    """An exception inside an iteration is logged and does not kill the loop."""
    engine = MagicMock()
    engine.triggers = []
    engine.evaluate.side_effect = [RuntimeError("boom"), None]
    loop = TriggerLoop(
        store=object(), state=None, interval=0.05, engine=engine
    )
    loop.start()
    try:
        time.sleep(0.16)
    finally:
        loop.stop()
        loop.join(timeout=2)
    assert not loop.is_alive()
    assert engine.evaluate.call_count >= 2  # loop kept going after the error
# ── server-side TriggerLoop (Round 9: config-gated in-process digests) ─────────

def test_start_trigger_loop_disabled_by_default(monkeypatch):
    """JARVIS_TRIGGERS unset/0 -> no loop, no TriggerLoop constructed."""
    from jarvis import triggers

    monkeypatch.delenv("JARVIS_TRIGGERS", raising=False)
    with patch.object(triggers, "TriggerLoop") as TL:
        assert triggers.start_trigger_loop() is None
        TL.assert_not_called()
    monkeypatch.setenv("JARVIS_TRIGGERS", "0")
    with patch.object(triggers, "TriggerLoop") as TL:
        assert triggers.start_trigger_loop() is None
        TL.assert_not_called()


def test_start_trigger_loop_enabled_starts_gated(monkeypatch):
    """JARVIS_TRIGGERS=1 -> loop started with per-tick store_factory, no persistent store."""
    from jarvis import triggers

    monkeypatch.setenv("JARVIS_TRIGGERS", "1")
    fake_loop = MagicMock()
    fake_loop.trigger_count = 3
    with patch.object(triggers, "TriggerLoop", return_value=fake_loop) as TL, \
         patch.object(triggers, "TriggerEngine"), \
         patch.object(triggers, "load_triggers", return_value=[]):
        result = triggers.start_trigger_loop(interval=10)
    assert result is fake_loop
    TL.assert_called_once()
    kwargs = TL.call_args.kwargs
    assert kwargs.get("store") is None            # no persistent handle
    assert kwargs.get("store_factory") is not None  # per-tick open/close
    fake_loop.start.assert_called_once()


def test_trigger_loop_opens_and_closes_store_per_tick():
    """store_factory: a fresh store is opened and closed on every tick."""
    from jarvis.triggers import TriggerLoop

    engine = MagicMock()
    opened = []

    class FakeStore:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    def factory():
        s = FakeStore()
        opened.append(s)
        return s

    loop = TriggerLoop(store=None, store_factory=factory, interval=0.05, engine=engine)
    loop.start()
    time.sleep(0.15)
    loop.stop()
    loop.join(timeout=2)

    assert len(opened) >= 1          # at least one tick ran
    assert all(s.closed for s in opened)  # every tick store was closed
    engine.evaluate.assert_called()

