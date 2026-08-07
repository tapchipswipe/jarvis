"""Tests for jarvis/classifier.py — the read-only memory classifier.

No network/LLM: `_ollama_generate` is monkeypatched. Covers `_ollama_generate`,
`classify` escalation paths, envelope parsing/validation, and `apply_envelope`
tag+metadata merging.
"""
from __future__ import annotations

import json

import jarvis.classifier as clf

# ── _ollama_generate ──────────────────────────────────────────────────────────

def test_ollama_generate_returns_response(monkeypatch):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"response": "hello"}'
    captured = {}
    def _fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data)
        return _Resp()
    monkeypatch.setattr("jarvis.classifier.urllib.request.urlopen", _fake)
    assert clf._ollama_generate("hi") == "hello"
    assert captured["url"] == "http://127.0.0.1:11434/api/generate"
    assert captured["payload"]["model"] == "qwen2.5:7b-instruct-q4_K_M"


def test_ollama_generate_returns_empty_on_error(monkeypatch):
    monkeypatch.setattr("jarvis.classifier.urllib.request.urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(
                            TimeoutError("slow")))
    assert clf._ollama_generate("hi") == ""


def test_ollama_generate_uses_env_defaults(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "tiny")
    monkeypatch.setenv("OLLAMA_HOST", "box")
    monkeypatch.setenv("OLLAMA_PORT", "1234")
    captured = {}

    def _fake(req, timeout=None):
        captured["u"] = req.full_url
        return _dummy()

    monkeypatch.setattr("jarvis.classifier.urllib.request.urlopen", _fake)
    clf._ollama_generate("hi")
    assert captured["u"] == "http://box:1234/api/generate"


def _dummy():
    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"response": "x"}'
    return _R()


# ── classify ──────────────────────────────────────────────────────────────────

def _envelope(route="idea_capture"):
    return {"route": route, "slug": "test-slug", "source_url_list": [],
            "inbox_path": None, "target_list": None, "action_atom": None,
            "tag_seeds": [], "confidence": "high", "escalate_reason": None,
            "notes": None}


def test_classify_happy_path(monkeypatch):
    env = _envelope()
    monkeypatch.setattr(clf, "_ollama_generate", lambda *a, **k: json.dumps(env))
    out = clf.classify("some note", source_id="inbox/note.txt")
    assert out == env


def test_classify_escalates_when_ollama_fails(monkeypatch):
    monkeypatch.setattr(clf, "_ollama_generate", lambda *a, **k: "")
    out = clf.classify("note", source_id="x")
    assert out["route"] == "escalate"
    assert out["escalate_reason"] == "classifier_failed"


def test_classify_escalates_on_malformed_json(monkeypatch):
    monkeypatch.setattr(clf, "_ollama_generate", lambda *a, **k: "not json at all")
    out = clf.classify("note", source_id="x")
    assert out["route"] == "escalate"
    assert out["escalate_reason"] == "parse_failed"


def test_classify_escalates_on_validation_failure(monkeypatch):
    bad = {"route": "bogus_route", "slug": None}  # route not in ROUTES
    monkeypatch.setattr(clf, "_ollama_generate", lambda *a, **k: json.dumps(bad))
    out = clf.classify("note", source_id="x")
    assert out["route"] == "escalate"
    assert out["escalate_reason"] == "validation_failed"

# ── _parse_envelope ───────────────────────────────────────────────────────────

def test_parse_envelope_extracts_from_prose():
    raw = 'Sure! Here is your JSON:\n{"route": "escalate", "slug": null}\nHope that helps.'
    env = clf._parse_envelope(raw)
    assert env is not None and env["route"] == "escalate"


def test_parse_envelope_none_on_malformed():
    assert clf._parse_envelope("no braces here") is None
    assert clf._parse_envelope("{invalid json") is None


# ── validate_envelope ─────────────────────────────────────────────────────────

def test_validate_requires_route():
    assert clf.validate_envelope({}) is False
    assert clf.validate_envelope({"route": "idea_capture", "slug": "ok"}) is True


def test_validate_rejects_unknown_route():
    assert clf.validate_envelope({"route": "nope", "slug": "ok"}) is False


def test_validate_rejects_long_escalate_reason():
    env = _envelope("escalate")
    env["escalate_reason"] = "x" * 201
    assert clf.validate_envelope(env) is False
    env["escalate_reason"] = "x" * 200
    assert clf.validate_envelope(env) is True


def test_validate_context_list_update_requires_valid_target_and_atom():
    env = _envelope("context_list_update")
    assert clf.validate_envelope(env) is False  # no target_list
    env["target_list"] = "nope.md"             # not in VALID_CONTEXT_LISTS
    env["action_atom"] = "buy milk"
    assert clf.validate_envelope(env) is False
    env["target_list"] = "errands.md"          # valid list + atom
    assert clf.validate_envelope(env) is True
    env["action_atom"] = None                   # atom now missing
    assert clf.validate_envelope(env) is False


def test_validate_slug_for_idea_and_reference():
    for route in ("idea_capture", "reference_note"):
        env = _envelope(route)
        env["slug"] = "not-valid Slug!"
        assert clf.validate_envelope(env) is False
        env["slug"] = "valid-slug-1"
        assert clf.validate_envelope(env) is True
        env["slug"] = "x" * 51
        assert clf.validate_envelope(env) is False


def test_validate_escalate_requires_reason():
    env = _envelope("escalate")
    env["escalate_reason"] = None
    assert clf.validate_envelope(env) is False
    env["escalate_reason"] = "cannot tell"
    assert clf.validate_envelope(env) is True


# ── _escalate_envelope ────────────────────────────────────────────────────────

def test_escalate_envelope_shape():
    env = clf._escalate_envelope("some_reason", "some notes")
    assert env["route"] == "escalate"
    assert env["escalate_reason"] == "some_reason"
    assert env["notes"] == "some notes"
    assert env["confidence"] == "low"

# ── apply_envelope ────────────────────────────────────────────────────────────

class _Row(dict):
    pass


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def fetchone(self): return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.committed = False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if sql.startswith("SELECT"):
            return _FakeResult([_Row(tags='["existing"]')])
        return None

    def commit(self): self.committed = True


class _FakeStore:
    def __init__(self):
        self.conn = _FakeConn()
        self.decisions = []

    def log_decision(self, memory_id, route, confidence, envelope):
        self.decisions.append((memory_id, route, confidence))


def test_apply_envelope_merges_tags_metadata_and_logs():
    store = _FakeStore()
    env = _envelope("idea_capture")
    env["tag_seeds"] = ["llm", "sync"]
    env["confidence"] = "high"
    assert clf.apply_envelope(store, "m1", env, log=True) is True

    update = [entry for s, entry in store.conn.executed if s.startswith("UPDATE")]
    assert len(update) == 1
    params = update[0]
    merged = json.loads(params[1])
    # existing tag + seeds + route tag ("idea") merged + sorted
    assert merged == ["existing", "idea", "llm", "sync"]
    assert params[0] == "idea_capture"
    assert store.conn.committed is True
    assert store.decisions == [("m1", "idea_capture", "high")]


def test_apply_envelope_defaults_route_to_escalate():
    store = _FakeStore()
    env = {"tag_seeds": []}  # no route
    clf.apply_envelope(store, "m2", env, log=False)
    update = [entry for s, entry in store.conn.executed if s.startswith("UPDATE")]
    assert update[0][0] == "escalate"

