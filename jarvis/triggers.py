"""
jarvis/triggers.py

Trigger engine for Jarvis proactive notifications.

Trigger types
-------------
TimeTrigger   – cron-like schedule; evaluated every minute of each hour.
EventTrigger  – one-shot; fires once when a named event fires.
PollTrigger   – fixed-interval poll; uses a fixed cadence in seconds.
TriggerLoop   – daemon background thread that evaluates triggers on a
                fixed cadence (wraps TriggerEngine; see TriggerLoop).

Action types
------------
notify   – send a desktop notification (notify.py / log fallback)
brief    – write a briefing file (notify.write_briefing)
escalate – log for manual review (notifications log with [ESCALATE] tag)

Configuration
-------------
User triggers live at  ~/.config/jarvis/triggers.toml
Hardcoded defaults are merged in when keys are absent from the file.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.notify import send_notification, write_briefing

logger = logging.getLogger("jarvis.triggers")

# ── Exception types ──────────────────────────────────────────────────────────
class TriggerError(Exception):
    """Raised on fatal trigger misconfiguration."""

class TriggerCooldown(Exception):
    """Raised inside evaluate() to suppress repeats within the cooldown window."""


# ── Context (read-only view of daemon + store) ───────────────────────────────
@dataclass
class TriggerContext:
    """Snapshot of current state available to trigger conditions."""
    now: datetime
    # DaemonState fields (decoupled from the actual class to avoid circular imports)
    last_ingest_ts: str | None   = None
    activity_log: list = field(default_factory=list)
    pending_queue: list = field(default_factory=list)
    retry_queue: list = field(default_factory=list)
    trigger_events: dict = field(default_factory=dict)  # event_name -> last_fired_ts
    # Store fields
    memory_count: int = 0
    last_memory_ts: str | None = None
    store: Any = None  # the Store instance (set by TriggerEngine.evaluate)
    # Mayor task-queue counts (populated by evaluate when available)
    task_pending: int = 0
    task_in_progress: int = 0
    # Misc
    extra: dict = field(default_factory=dict)


# ── Base class ───────────────────────────────────────────────────────────────
class Trigger:
    TYPE: str = "base"

    def __init__(self, name: str, actions: list[dict], **kwargs: Any):
        self.name = name
        self.actions = actions
        self.enabled: bool = kwargs.get("enabled", True)
        self.cooldown: int = int(kwargs.get("cooldown", 0))  # seconds
        self._last_fired: datetime | None = None

    # ── abstract interface ──────────────────────────────────────────────────

    def should_fire(self, ctx: TriggerContext) -> bool:
        """Return True when the trigger condition is met. Override in subclasses."""
        raise NotImplementedError

    def evaluate(self, ctx: TriggerContext) -> list[str]:
        """Evaluate and return a list of action results (or [] if skipped)."""
        if not self.enabled:
            return []
        try:
            if not self.should_fire(ctx):
                return []
        except TriggerCooldown:
            return []
        if self.cooldown and self._last_fired:
            elapsed = (ctx.now - self._last_fired).total_seconds()
            if elapsed < self.cooldown:
                logger.debug("[%s] cooldown active (%ds / %ds elapsed)", self.name, int(elapsed), self.cooldown)
                raise TriggerCooldown  # suppress silently
        return self._execute_actions(ctx)

    def _execute_actions(self, ctx: TriggerContext) -> list[str]:
        results: list[str] = []
        for action_spec in self.actions:
            try:
                res = _dispatch_action(action_spec, ctx, trigger_name=self.name)
                results.append(res)
            except Exception as exc:
                logger.error("[%s] action %r failed: %s", self.name, action_spec, exc, exc_info=True)
        self._last_fired = ctx.now
        return results


# ── TimeTrigger (cron-like) ──────────────────────────────────────────────────
#
# cron_expr supports three formats:
#   "HH:MM"                      – daily at that wall-clock time
#   "* * HH * * 1-5"             – standard 5-field cron (min hr dom mon dow)
#                                  dow: 0=Sun … 6=Sat (matches cron convention)
#   "0 8 * * 1-5"                – same as above (standard cron)
#
# The evaluation granularity is one minute; the daemon thread fires every 60s
# and overlaps the boundary window by ±120s to handle clock drift.

_MINUTES_PER_FIELD = [1, 60, 1440, 43200]  # not used directly; kept for ref

class TimeTrigger(Trigger):
    TYPE = "time"

    def __init__(self, name: str, actions: list[dict], cron_expr: str, **kwargs: Any):
        super().__init__(name, actions, **kwargs)
        self.cron_expr = cron_expr
        self._last_eval_minute: tuple[int, int, int, int, int] | None = None
        self._parse_expr(cron_expr)

    def _parse_expr(self, expr: str) -> None:
        """Determine whether expr is HH:MM or a 5-field cron string."""
        parts = expr.strip().split()
        if len(parts) == 1 and ":" in parts[0]:
            # HH:MM format – fire daily
            self._mode = "daily"
            try:
                h, m = (int(x) for x in parts[0].split(":"))
            except ValueError as exc:
                raise TriggerError(f"Invalid HH:MM time '{parts[0]}': {exc}") from exc
            self._hour, self._minute = h, m
        elif len(parts) == 5:
            self._mode = "cron"
            self._fields = parts  # [min, hr, dom, mon, dow]
            self._validate_cron_fields()
        else:
            raise TriggerError(
                f"TimeTrigger '{expr}' must be HH:MM or 'min hr dom mon dow'."
            )

    def _validate_cron_fields(self) -> None:
        """Validate cron field syntax at parse/load time.

        Rejects a zero/negative step (e.g. the ``*/0`` typo) so the trigger
        fails fast with a clear config error instead of raising a
        ZeroDivisionError on every tick (which gets swallowed per-trigger and
        silently prevents the trigger from ever firing).
        """
        field_names = ("minute", "hour", "day-of-month", "month", "day-of-week")
        for index, field_expr in enumerate(self._fields):
            for tok in field_expr.split(","):
                if "/" not in tok:
                    continue
                base, step = tok.split("/", 1)
                try:
                    step_val = int(step)
                except ValueError as exc:
                    raise TriggerError(
                        f"Invalid non-integer cron step '{step}' in "
                        f"{field_names[index]} field '{field_expr}'."
                    ) from exc
                if step_val <= 0:
                    raise TriggerError(
                        f"Invalid cron step '{step}' in {field_names[index]} "
                        f"field '{field_expr}': step must be a positive integer "
                        f"(e.g. '*/5'; '*/{step}' is not allowed)."
                    )
                if base != "*":
                    try:
                        int(base)
                    except ValueError as exc:
                        raise TriggerError(
                            f"Invalid non-integer cron base '{base}' in "
                            f"{field_names[index]} field '{field_expr}'."
                        ) from exc

    def _field_matches(self, field_expr: str, current_value: int, max_val: int) -> bool:
        """Match a single cron field (e.g. '*/2', '1-5', '0', '*')."""
        if field_expr == "*":
            return True
        if "," in field_expr:
            return any(
                self._field_matches(tok, current_value, max_val)
                for tok in field_expr.split(",")
            )
        if "/" in field_expr:
            base, step = field_expr.split("/", 1)
            base_val = 0 if base == "*" else int(base)
            step_val = int(step)
            return current_value >= base_val and (current_value - base_val) % step_val == 0
        if "-" in field_expr:
            lo_str, hi_str = field_expr.split("-", 1)
            return int(lo_str) <= current_value <= int(hi_str)
        return int(field_expr) == current_value

    def _cron_matches(self, dt: datetime) -> bool:
        fields = self._fields
        now_min  = dt.minute
        now_hr   = dt.hour    # 0-23
        now_dom  = dt.day
        now_mon  = dt.month
        now_dow  = dt.isoweekday() % 7   # convert: Mon=1..Sun=0; cron: Sun=0..Sat=6
        values   = [now_min, now_hr, now_dom, now_mon, now_dow]
        max_vals = [59, 23, 31, 12, 6]
        return all(
            self._field_matches(f, v, mx)
            for f, v, mx in zip(fields, values, max_vals)
        )

    def should_fire(self, ctx: TriggerContext) -> bool:
        dt = ctx.now
        # ── dedup across same-minute calls ─────────────────────────────────
        eval_key = (dt.year, dt.month, dt.day, dt.hour, dt.minute)
        if eval_key == self._last_eval_minute:
            return False  # already evaluated this minute
        self._last_eval_minute = eval_key
        # ── match ─────────────────────────────────────────────────────────
        if self._mode == "daily":
            matches = (dt.hour == self._hour and dt.minute == self._minute)
        else:
            matches = self._cron_matches(dt)
        if not matches:
            return False
        logger.debug("[%s] TimeTrigger matched at %s", self.name, dt.isoformat())
        return True


# ── EventTrigger (one-shot) ──────────────────────────────────────────────────
#
# Fires once when the named event has been raised.
# Events are set externally (e.g. the daemon calls trigger_engine.raise_event()).
# A ``reset_on`` time window (seconds) resets the lock so the trigger can fire
# again after the window expires.

class EventTrigger(Trigger):
    TYPE = "event"

    def __init__(self, name: str, actions: list[dict], event_name: str, **kwargs: Any):
        super().__init__(name, actions, **kwargs)
        self.event_name = event_name
        self.reset_after: int = int(kwargs.get("reset_after", 0))  # s; 0 = fire once forever

    def should_fire(self, ctx: TriggerContext) -> bool:
        events: dict = ctx.trigger_events
        entry = events.get(self.event_name)
        if not entry:
            return False
        fired_ts_str = entry.get("fired_at")
        if fired_ts_str is None:
            return True
        if not self.reset_after:
            return False   # one-shot; already fired, never reset
        fired_ts = datetime.fromisoformat(fired_ts_str)
        elapsed = (ctx.now - fired_ts).total_seconds()
        return elapsed >= self.reset_after


# ── PollTrigger (fixed interval) ─────────────────────────────────────────────
#
# Fires when at least ``interval_seconds`` have elapsed since the last time.
# A ``count`` field limits total firings (0 = unlimited).

class PollTrigger(Trigger):
    TYPE = "poll"

    def __init__(self, name: str, actions: list[dict], interval_seconds: int, **kwargs: Any):
        super().__init__(name, actions, **kwargs)
        self.interval_seconds = int(interval_seconds)
        self.max_count: int = int(kwargs.get("count", 0))  # 0 = unlimited
        self._fire_count: int = 0
        # last_poll_ts stored in trigger_events by the engine

    def should_fire(self, ctx: TriggerContext) -> bool:
        if self.max_count and self._fire_count >= self.max_count:
            return False
        events: dict = ctx.trigger_events
        entry = events.get(self.name)
        last_ts_str = entry.get("last_poll_ts") if entry else None
        if last_ts_str is None:
            return True  # first fire
        last_ts = datetime.fromisoformat(last_ts_str)
        elapsed = (ctx.now - last_ts).total_seconds()
        return elapsed >= self.interval_seconds

    def _execute_actions(self, ctx: TriggerContext) -> list[str]:
        results = super()._execute_actions(ctx)
        self._fire_count += 1
        return results


# ── Action dispatcher ────────────────────────────────────────────────────────

_ACTION_HANDLERS: dict[str, Callable[..., Any]] = {}

def _register_action(name: str):
    """Decorator to register an action handler."""
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _ACTION_HANDLERS[name] = fn
        return fn
    return decorator


@_register_action("notify")
def _action_notify(
    spec: dict,
    ctx: TriggerContext,
    **kwargs: Any,
) -> str:
    title = spec.get("title", kwargs.get("trigger_name", "Jarvis"))
    body  = spec.get("body", "")
    # Allow body to be a template-like string referencing context fields
    body = _render_template(body, ctx)
    send_notification(title, body)
    return f"notify:{title}"


def _query_upcoming_events(ctx: TriggerContext, window_minutes: int) -> list[dict]:
    """Return calendar-sourced memories whose start time falls within the window.

    Calendar events are stored as ``source = 'calendar'`` rows whose ``timestamp``
    is the event start time (ISO-format, same convention used by the
    ``check_calendar`` tool). We bound the query to ``[now, now + window]`` so a
    poll only fires when a real event is imminent. Falls back to an empty list on
    any store/query error (best-effort, never raises).
    """
    store = ctx.store
    if store is None:
        return []
    now = ctx.now
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    start = now.isoformat()
    end = (now + timedelta(minutes=window_minutes)).isoformat()
    try:
        rows = store.conn.execute(
            "SELECT content, timestamp FROM memories "
            "WHERE source = 'calendar' AND superseded = 0 "
            "AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp ASC LIMIT 20",
            (start, end),
        ).fetchall()
    except Exception:  # best-effort; never break the poll
        logger.debug("Could not query upcoming calendar events", exc_info=True)
        return []
    events = []
    for row in rows:
        content = row["content"] or ""
        title = content.split("\n")[0].strip() or "Event"
        events.append({"title": title, "timestamp": row["timestamp"]})
    return events


def _format_ts(ts: str) -> str:
    """Render an ISO timestamp as a short HH:MM clock time (best-effort)."""
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M")
    except Exception:  # noqa: BLE001 - fall back to the raw string
        return ts or ""


def _format_events(events: list[dict], cap: int = 5) -> str:
    """Render a compact bullet list of upcoming events for a notification body."""
    lines = [f"• {e['title']} @ {_format_ts(e['timestamp'])}" for e in events[:cap]]
    if len(events) > cap:
        lines.append(f"…and {len(events) - cap} more")
    return "\n".join(lines)


@_register_action("calendar_poll")
def _action_calendar_poll(
    spec: dict,
    ctx: TriggerContext,
    **kwargs: Any,
) -> str:
    """Poll the store for calendar events in the next window and notify only if any.

    Returns a non-empty result even when no events are found so the poll still
    counts as "fired" (the engine advances ``last_poll_ts``), but only calls
    ``send_notification`` when at least one real event exists — no more static
    boilerplate body on a timer.
    """
    window = int(spec.get("window_minutes", 120))
    title = spec.get("title", kwargs.get("trigger_name", "📅 Upcoming Events"))
    events = _query_upcoming_events(ctx, window)
    if not events:
        logger.debug(
            "[%s] no upcoming calendar events in next %d min",
            kwargs.get("trigger_name", title), window,
        )
        return "calendar_poll:no_events"
    send_notification(title, _format_events(events))
    return f"calendar_poll:{len(events)}"


@_register_action("brief")
def _action_brief(
    spec: dict,
    ctx: TriggerContext,
    **kwargs: Any,
) -> str:
    title   = spec.get("title", kwargs.get("trigger_name", "Briefing"))
    content = spec.get("content", "")
    # Allow content to be a template-like string referencing context fields
    content = _render_template(content, ctx)
    path = write_briefing(title, content)
    return f"brief:{path}"


@_register_action("escalate")
def _action_escalate(
    spec: dict,
    ctx: TriggerContext,
    **kwargs: Any,
) -> str:
    reason = spec.get("reason", "Manual review required")
    reason = _render_template(reason, ctx)
    send_notification(
        f"[ESCALATE] {kwargs.get('trigger_name', 'Jarvis')}",
        reason,
    )
    return f"escalate:{reason[:60]}"


@_register_action("digest")
def _action_digest(
    spec: dict,
    ctx: TriggerContext,
    **kwargs: Any,
) -> str:
    """LLM-synthesized digest for morning/end-of-day, then notify + brief + store.

    spec: kind (morning_brief | end_of_day), title. LLM failure falls back to
    the static summary returned by Brain.build_digest.
    """
    kind = spec.get("kind", "morning_brief")
    title = spec.get("title", kwargs.get("trigger_name", "Digest"))
    store = ctx.store
    owns_store = store is None
    if owns_store:
        from jarvis.store import Store
        store = Store()
    try:
        from jarvis.brain import Brain
        text = Brain(store).build_digest(kind=kind)

        path = write_briefing(title, text)

        # Durable session-tier memory so the digest is searchable later.
        try:
            from jarvis.embed import get_embedding
            from jarvis.store import fingerprint
            now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            fid = fingerprint("brief", title, text, now_iso)
            if not store.exists(fid):
                emb = get_embedding(text[:4000])
                store.add(
                    fid, "brief", title, now_iso, text,
                    ["brief", kind], {"title": title}, emb, tier="session",
                )
        except Exception:            # noqa: BLE001 - memory write is best-effort
            pass

        send_notification(title, text[:200])
        return f"digest:{kind}:{path}"
    finally:
        if owns_store:
            store.close()


def _dispatch_action(
    spec: dict,
    ctx: TriggerContext,
    trigger_name: str = "",
) -> str:
    type_ = spec.get("type")
    if not type_:
        raise TriggerError(f"Action spec missing 'type': {spec}")
    handler = _ACTION_HANDLERS.get(type_)
    if handler is None:
        raise TriggerError(f"Unknown action type '{type_}'")
    return handler(spec, ctx, trigger_name=trigger_name)


def _render_template(text: str, ctx: TriggerContext) -> str:
    """Simple {field} substitution from the TriggerContext."""
    if "{" not in text:
        return text
    mapping: dict[str, Any] = {
        "memory_count":  ctx.memory_count,
        "last_ingest":   ctx.last_ingest_ts or "never",
        "last_memory":   ctx.last_memory_ts or "never",
        "pending_count": len(ctx.pending_queue),
        "task_pending":  ctx.task_pending,
        "task_in_progress": ctx.task_in_progress,
        "activity_lines": "; ".join(
            a["msg"] for a in ctx.activity_log[-5:] if isinstance(a, dict)
        ),
    }
    for key, val in mapping.items():
        text = text.replace(f"{{{key}}}", str(val))
    return text


# Token names rendered by _render_template that require the Mayor task-queue.
# Opening the TaskQueue (connect + WAL commit) every tick is wasteful unless at
# least one action template actually references these counts.
_TASK_COUNT_TOKENS = ("{task_pending}", "{task_in_progress}")


def _actions_need_task_counts(actions: list[dict]) -> bool:
    """Return True when any action template references a task count token."""
    for action in actions:
        for value in action.values():
            if isinstance(value, str) and any(
                tok in value for tok in _TASK_COUNT_TOKENS
            ):
                return True
    return False


# ── TriggerEngine ────────────────────────────────────────────────────────────

class TriggerEngine:
    """Evaluates all registered triggers every ``poll_interval`` seconds.

    Usage (from daemon)::

        engine = TriggerEngine(triggers)
        engine.evaluate(store=store, state=daemon_state)
    """

    def __init__(self, triggers: list[Trigger]):
        self.triggers = triggers
        self._events: dict[str, dict] = {}   # event_name -> {fired_at: iso, ...}
        self._events_lock = threading.Lock()
        self.logger = logging.getLogger("jarvis.triggers.engine")
        # Only open the Mayor TaskQueue (connect + WAL round-trip) when some
        # action template actually references task counts.
        self._needs_task_counts = any(
            _actions_need_task_counts(t.actions) for t in self.triggers
        )

    def raise_event(self, event_name: str, payload: dict | None = None) -> None:
        """Mark an event as fired. Thread-safe."""
        with self._events_lock:
            entry = self._events.setdefault(event_name, {})
            entry["fired_at"] = datetime.now(timezone.utc).isoformat()
            if payload:
                entry.update(payload)
        self.logger.debug("Event raised: %s", event_name)

    def evaluate(self, store: Any, state: Any) -> None:
        """Run every trigger; catch exceptions per-trigger; never propagate."""
        # ── build context ─────────────────────────────────────────────────
        now = datetime.now(timezone.utc)

        # Memory stats (defensive against missing columns/methods)
        memory_count = 0
        last_memory_ts = None
        try:
            memory_count = store.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE superseded = 0"
            ).fetchone()[0]
            row = store.conn.execute(
                "SELECT MAX(timestamp) FROM memories WHERE superseded = 0"
            ).fetchone()
            if row and row[0]:
                last_memory_ts = row[0]
        except Exception as exc:           # noqa: BLE001
            self.logger.debug("Could not read store stats: %s", exc)

        # Mayor task-queue counts (best-effort; missing -> 0). Only open the
        # queue (connect + WAL round-trip) when some action template references
        # task counts; otherwise skip it entirely.
        task_pending = task_in_progress = 0
        if self._needs_task_counts:
            try:
                from jarvis.task_queue import TaskQueue
                tq = TaskQueue()
                try:
                    task_pending = len(tq.list_tasks(status="pending_review"))
                    task_in_progress = len(tq.list_tasks(status="in_progress"))
                finally:
                    tq.close()
            except Exception:            # noqa: BLE001, S110 - task counts best-effort
                pass

        ctx = TriggerContext(
            now=now,
            last_ingest_ts=getattr(state, "last_ingest_ts", None),
            activity_log=getattr(state, "activity_log", []),
            pending_queue=getattr(state, "pending_queue", []),
            retry_queue=getattr(state, "retry_queue", []),
            trigger_events=dict(self._events),  # snapshot
            memory_count=memory_count,
            last_memory_ts=last_memory_ts,
            store=store,
            task_pending=task_pending,
            task_in_progress=task_in_progress,
        )

        self.logger.debug(
            "Evaluating %d triggers  |  memories=%d  |  pending=%d",
            len(self.triggers), memory_count, len(ctx.pending_queue),
        )

        for trigger in self.triggers:
            try:
                results = trigger.evaluate(ctx)
                if results:
                    self.logger.info(
                        "[%s] fired → %s", trigger.name, results
                    )
                    # Update poll_ts for PollTriggers so they don't re-fire
                    if trigger.TYPE == "poll":
                        with self._events_lock:
                            ev = self._events.setdefault(trigger.name, {})
                            ev["last_poll_ts"] = now.isoformat()
                    # Update event state for EventTriggers
                    if trigger.TYPE == "event":
                        with self._events_lock:
                            ev = self._events.setdefault(
                                trigger.event_name, {}
                            )
                            ev["fired_at"] = now.isoformat()
            except TriggerCooldown:
                pass  # expected; not an error
            except Exception as exc:
                self.logger.error(
                    "[%s] evaluate failed: %s", trigger.name, exc, exc_info=True
                )


# ── Config loader ────────────────────────────────────────────────────────────
#
# File: ~/.config/jarvis/triggers.toml
#
# Example:
#   [[triggers]]
#   type = "time"
#   name = "morning-brief"
#   cron_expr = "0 8 * * 1-5"
#   cooldown = 3600
#   actions = [{ type = "notify", title = "Good morning", body = "…" }]
#
# Hardcoded HARDCODED_TRIGGERS are merged when a user file is absent.

HARDCODED_TRIGGERS: list[dict[str, Any]] = [
    {
        "type": "time",
        "name": "morning-brief",
        "cron_expr": "0 8 * * 1-5",
        "cooldown": 3600,
        "actions": [
            {
                "type": "digest",
                "kind": "morning_brief",
                "title": "☀️ Morning Brief",
            }
        ],
    },
    {
        "type": "time",
        "name": "end-of-day-wrap",
        "cron_expr": "0 18 * * 1-5",
        "cooldown": 3600,
        "actions": [
            {
                "type": "digest",
                "kind": "end_of_day",
                "title": "📋 End-of-Day Wrap",
            }
        ],
    },
    {
        "type": "poll",
        "name": "upcoming-events-poll",
        "interval_seconds": 1800,   # every 30 min
        "cooldown": 1800,
        "actions": [
            {
                "type": "calendar_poll",
                "title": "📅 Upcoming Events",
                "window_minutes": 120,   # next 2 hours
            }
        ],
    },
]


def load_triggers(config_path: Path | None = None) -> list[Trigger]:
    """Load triggers from a TOML file, merge with hardcoded defaults.

    User-defined triggers take precedence (same name → user wins).
    Returns a list of instantiated Trigger objects.
    """
    if config_path is None:
        from jarvis.paths import config_file
        config_path = config_file("triggers.toml")

    raw: list[dict[str, Any]] = []

    if config_path.exists():
        try:
            import tomllib
            with config_path.open("rb") as fh:
                data = tomllib.load(fh)
            raw = data.get("triggers", [])
            logger.info("Loaded %d trigger(s) from %s", len(raw), config_path)
        except Exception as exc:       # noqa: BLE001
            logger.warning("Could not load triggers.toml: %s – falling back to defaults", exc)

    if not raw:
        raw = [dict(t) for t in HARDCODED_TRIGGERS]
        logger.info("Using %d hardcoded trigger(s)", len(raw))

    # ── merge strategy: hardcoded backfill for missing names ──────────────
    user_names = {t["name"] for t in raw}
    merged: list[dict[str, Any]] = list(raw)
    for hc in HARDCODED_TRIGGERS:
        if hc["name"] not in user_names:
            merged.append(hc)

    return [_instantiate(d) for d in merged]


# ── Trigger factory ─────────────────────────────────────────────────────────

CLASS_MAP: dict[str, type] = {
    "time":   TimeTrigger,
    "event":  EventTrigger,
    "poll":   PollTrigger,
}


def _instantiate(raw: dict) -> Trigger:
    ttype = raw.get("type")
    cls   = CLASS_MAP.get(ttype)
    if cls is None:
        raise TriggerError(f"Unknown trigger type '{ttype}'")
    name = raw.get("name", f"unnamed-{ttype}")
    actions = raw.get("actions", [])
    # Remaining keys are kwargs
    kwargs = {k: v for k, v in raw.items() if k not in ("type", "name", "actions")}
    return cls(name=name, actions=actions, **kwargs)


# ── TriggerLoop (daemon background thread) ───────────────────────────────────

DEFAULT_POLL_INTERVAL = 60  # seconds between trigger evaluations


class TriggerLoop(threading.Thread):
    """Background thread that evaluates triggers on a fixed cadence.

    Owns a :class:`TriggerEngine` built from :func:`load_triggers` and calls
    ``engine.evaluate(store, state)`` every ``interval`` seconds. Each tick the
    engine builds a :class:`TriggerContext` from the Store/state snapshot and
    dispatches due actions (notify/brief/escalate) on schedule.

    Usage (from the daemon)::

        loop = TriggerLoop(store=store, state=daemon_state)
        loop.start()
        ...
        loop.stop()
        loop.join(timeout=5)

    Iteration errors are logged and never kill the loop; ``stop()`` signals a
    clean shutdown and the thread exits on its next wakeup (daemon thread, so
    it never blocks interpreter exit on its own).
    """

    def __init__(
        self,
        store: Any,
        state: Any = None,
        *,
        interval: int = DEFAULT_POLL_INTERVAL,
        config_path: Path | None = None,
        engine: TriggerEngine | None = None,
        store_factory: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(name="trigger-loop", daemon=True)
        self.store = store
        self.state = state
        self.store_factory = store_factory
        interval = float(interval)
        if interval <= 0:
            raise TriggerError(
                f"TriggerLoop interval must be positive, got {interval}"
            )
        self.interval = int(interval) if interval.is_integer() else interval
        self._stop_event = threading.Event()
        self.engine = (
            engine
            if engine is not None
            else TriggerEngine(load_triggers(config_path=config_path))
        )
        self.trigger_count = len(self.engine.triggers)

    @property
    def triggers(self) -> list[Trigger]:
        """The triggers loaded into this loop's engine."""
        return self.engine.triggers

    def stop(self) -> None:
        """Signal the loop to stop; the thread exits on its next wakeup."""
        self._stop_event.set()

    def run(self) -> None:
        logger.info(
            "Trigger loop started (interval=%ss, %d trigger(s))",
            self.interval, self.trigger_count,
        )
        while not self._stop_event.is_set():
            tick = time.monotonic()
            # When a store_factory is supplied we open a fresh Store per tick and
            # close it afterwards (same serialized open/close pattern as the inbox
            # ingester) so we never hold a second persistent Chroma handle.
            store = self.store_factory() if self.store_factory is not None else self.store
            try:
                try:
                    self.engine.evaluate(store, self.state)
                except Exception:
                    logger.exception("Trigger loop iteration failed")
            finally:
                if self.store_factory is not None and store is not None:
                    try:
                        store.close()
                    except Exception:
                        logger.debug("Trigger loop store close failed", exc_info=True)
            # Sleep out the remainder of the interval; stop() wakes us early.
            elapsed = time.monotonic() - tick
            self._stop_event.wait(max(0.0, self.interval - elapsed))
        logger.info("Trigger loop stopped")


def start_trigger_loop(
    interval: int = DEFAULT_POLL_INTERVAL,
    config_path: Path | None = None,
) -> TriggerLoop | None:
    """Start the trigger loop **inside the `jarvis server` process** (server side).

    Gated by env ``JARVIS_TRIGGERS=1`` — default OFF so existing deployments are
    untouched. Uses a *per-tick* Store open/close (``store_factory``) so we never
    hold a second persistent Chroma handle on the single-brain box — the same
    serialized open/close pattern the inbox ingester already runs successfully.

    RAM/model-tier note: the hardcoded defaults fire LLM digests at 08:00/18:00 and a
    calendar notify poll every 30 min. Keep the model-tier discipline (small models /
    not while a 7B is resident) when enabling on the RAM-tight box.

    Returns the running TriggerLoop (for tests/manual stop), or None when disabled.
    """
    if os.environ.get("JARVIS_TRIGGERS", "0") != "1":
        logger.info("Trigger loop DISABLED (set JARVIS_TRIGGERS=1 to enable)")
        return None

    def _store_factory():
        from jarvis.store import Store
        return Store()

    engine = TriggerEngine(load_triggers(config_path=config_path))
    loop = TriggerLoop(
        store=None,                 # no persistent store; use per-tick factory
        store_factory=_store_factory,
        interval=interval,
        engine=engine,
    )
    loop.start()
    logger.info(
        "Trigger loop started in-process (interval=%ss, %d trigger(s))",
        interval, loop.trigger_count,
    )
    return loop

