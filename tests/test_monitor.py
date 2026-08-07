"""
Tests for jarvis/monitor.py

Covers:
  - _time_ago produces friendly relative strings for naive and aware timestamps
  - _time_ago handles the "never" and unparseable cases
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from jarvis.monitor import _time_ago


def test_time_ago_naive_timestamp_is_friendly():
    """A naive (tz-less) timestamp must yield a friendly relative string
    rather than the raw ISO timestamp."""
    naive = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)).isoformat()
    out = _time_ago(naive)
    assert out != naive
    assert re.fullmatch(r"\d+m ago", out), f"unexpected output: {out!r}"


def test_time_ago_aware_timestamp_is_friendly():
    aware = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    out = _time_ago(aware)
    assert out != aware
    assert re.fullmatch(r"\d+h ago", out), f"unexpected output: {out!r}"


def test_time_ago_seconds():
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    assert re.fullmatch(r"\d+s ago", _time_ago(recent))


def test_time_ago_days():
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert re.fullmatch(r"\d+d ago", _time_ago(old))


def test_time_ago_none_is_never():
    assert _time_ago(None) == "never"


def test_time_ago_unparseable_returns_raw():
    assert _time_ago("not-a-timestamp") == "not-a-timestamp"