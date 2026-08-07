"""Tests for routes.classify_existing (route/classify application on a memory)."""
from __future__ import annotations

from jarvis.routes import classify_existing


def _env_for(route):
    return {"route": route, "slug": None, "source_url_list": [], "inbox_path": None,
            "target_list": None, "action_atom": None, "tag_seeds": ["idea"],
            "confidence": "high", "escalate_reason": None, "notes": None}


def test_classify_existing_applies_valid_envelope(monkeypatch):
    import jarvis.classifier as clf

    envelope = _env_for("idea_capture")
    monkeypatch.setattr(clf, "classify", lambda content, source_id="unknown", model=None: envelope)
    monkeypatch.setattr(clf, "validate_envelope", lambda env: True)
    applied = []
    monkeypatch.setattr(clf, "apply_envelope",
                        lambda store, memory_id, envelope, log=True: applied.append((memory_id, envelope["route"])))

    store = object()
    result = classify_existing(store, {"id": "m1", "content": "idea: build a tool", "source_id": "src-1"})
    assert result["route"] == "idea_capture"
    assert applied == [("m1", "idea_capture")]


def test_classify_existing_escalates_on_bad_envelope(monkeypatch):
    import jarvis.classifier as clf

    monkeypatch.setattr(clf, "classify", lambda content, source_id="unknown", model=None: {})
    monkeypatch.setattr(clf, "validate_envelope", lambda env: False)
    applied = []
    monkeypatch.setattr(clf, "apply_envelope",
                        lambda store, memory_id, envelope, log=True: applied.append((memory_id, envelope["route"])))

    store = object()
    result = classify_existing(store, {"id": "m2", "content": "unparseable note", "source_id": "src-2"})
    assert result["route"] == "escalate"
    assert "envelope validation failed" in result["escalate_reason"]
    assert applied == [("m2", "escalate")]
