"""Tests for the thin-client remote transport (jarvis/remote.py).

Fakes urllib so no network is touched, and asserts each wrapper issues the right
method/path and (critically, after Round 3's token guards) attaches the token header.
"""
from __future__ import annotations

import json
import urllib.request

from jarvis import remote


def _fake_urlopen(monkeypatch, payload=None):
    captured = {}

    class _Resp:
        def __init__(self, p):
            self._p = p if p is not None else {"ok": True}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self._p).encode()

    def _urlopen(req, timeout=None):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        header_items = dict(req.header_items())
        captured["token"] = next(
            (v for k, v in header_items.items() if k.lower() == "x-jarvis-token"),
            None,
        )
        return _Resp(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return captured


def _client_env(monkeypatch):
    monkeypatch.setenv("JARVIS_MODE", "client")
    monkeypatch.setenv("JARVIS_REMOTE", "http://100.102.0.99:8766")


def test_health_and_deep_paths(monkeypatch):
    _client_env(monkeypatch)
    c = _fake_urlopen(monkeypatch)
    remote.health()
    assert c["method"] == "GET" and c["url"].endswith("/api/health")
    remote.health_deep()
    assert c["url"].endswith("/api/health/deep")
    remote.ingest_status()
    assert c["url"].endswith("/api/ingest/status")


def test_write_wrappers_use_post(monkeypatch):
    _client_env(monkeypatch)
    c = _fake_urlopen(monkeypatch, {"added": 1, "skipped": 0})
    remote.remember_batch([{"content": "x"}])
    assert c["method"] == "POST" and c["url"].endswith("/api/remember")
    remote.backfill_batch([{"id": "b1", "content": "y"}])
    assert c["method"] == "POST" and c["url"].endswith("/api/backfill")


def test_search_query_string(monkeypatch):
    _client_env(monkeypatch)
    c = _fake_urlopen(monkeypatch, {"memories": [], "count": 0})
    remote.search("hello world", n=5, source="deep")
    assert c["method"] == "GET"
    assert "q=hello+world" in c["url"]
    assert "n=5" in c["url"]
    assert "source=deep" in c["url"]


def test_token_header_sent_when_configured(monkeypatch):
    _client_env(monkeypatch)
    monkeypatch.setenv("JARVIS_TOKEN", "sekret")
    c = _fake_urlopen(monkeypatch)
    remote.search("hi", n=3)
    assert c["token"] == "sekret"


def test_no_token_header_when_not_configured(monkeypatch):
    _client_env(monkeypatch)
    monkeypatch.setenv("JARVIS_TOKEN", "")
    c = _fake_urlopen(monkeypatch)
    remote.health()
    assert c["token"] is None


def test_is_remote_requires_mode_and_url(monkeypatch):
    monkeypatch.setenv("JARVIS_MODE", "client")
    monkeypatch.setenv("JARVIS_REMOTE", "")
    assert remote.is_remote() is False
    monkeypatch.setenv("JARVIS_MODE", "local")
    monkeypatch.setenv("JARVIS_REMOTE", "http://box:8766")
    assert remote.is_remote() is False
    monkeypatch.setenv("JARVIS_MODE", "client")
    monkeypatch.setenv("JARVIS_REMOTE", "http://box:8766")
    assert remote.is_remote() is True
