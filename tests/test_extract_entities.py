"""Tests for jarvis/extract_entities.py — source-aware entity extraction.

Pure regex/heuristic — no network/LLM. Takes this module from 0% cover (it
feeds the knowledge-graph entity linking used by query/chat grounding).
"""
from __future__ import annotations

from jarvis import extract_entities as ee


def test_empty_text_returns_empty_list():
    assert ee.extract_entities("") == []
    assert ee.extract_entities("   ") == []


def test_defensive_clean_name_strips_title_prefix():
    assert ee._clean_name("Dr  Alice Smith") == "Alice Smith"
    assert ee._clean_name("Ms Jane Doe").startswith("Jane")


def test_dedupe_is_case_insensitive_per_type():
    ents = [{"name": "Alice Smith", "type": "person", "confidence": 0.9},
            {"name": "alice smith", "type": "person", "confidence": 0.9},
            {"name": "Alice Smith", "type": "org", "confidence": 0.5}]
    out = ee._dedupe(ents)
    assert len(out) == 2
    assert out[1]["type"] == "org"  # different type kept


def test_contacts_extracts_persons():
    text = "Bob Jones and Carol Tran here; also Dr. Alice Ripper."
    ents = ee._from_contacts(text)
    names = {e["name"] for e in ents if e["type"] == "person"}
    assert "Bob Jones" in names and "Carol Tran" in names
    assert all(e["confidence"] >= 0.9 for e in ents)


def test_calendar_attendees_and_no_junk_org():
    text = "Meeting at 3pm. Attendees: Sara Lee, Tom Wu\nTeam sync with Engineering dept"
    ents = ee._from_calendar(text)
    person_names = {e["name"] for e in ents if e["type"] == "person"}
    assert "Sara Lee" in person_names
    # No placeholder 'Organization reference' junk entity should be emitted.
    assert all(e["name"] != "Organization reference" for e in ents)


def test_email_from_to_and_domains():
    text = (
        "From: Jane Cooper <jane@acme.io>\n"
        "To: Bob Rice, Len <b@x.dev>\n"
        "Let's talk after reviewing Acme."
    )
    ents = ee._from_email(text)
    person_names = {e["name"] for e in ents if e["type"] == "person"}
    assert "Jane Cooper" in person_names
    assert "Bob Rice" in person_names
    assert any(e["type"] == "domain" and e["name"] == "acme.io" for e in ents)


def test_browser_domains_and_no_junk_org():
    text = "Visited https://Example.com and https://another.dev — RESEARCH Labs group"
    ents = ee._from_browser(text)
    domains = {e["name"] for e in ents if e["type"] == "domain"}
    assert "example.com" in domains
    # No placeholder 'Organization reference' junk entity should be emitted.
    assert all(e["name"] != "Organization reference" for e in ents)


def test_chat_speakers():
    text = "[Alice Smith] hi\nBob: hello\n[Jane] what's up"
    ents = ee._from_chat(text)
    names = {e["name"] for e in ents}
    assert "Alice Smith" in names and "Jane" in names and "Bob" in names


def test_fallback_detects_persons_and_domains():
    text = "Sarah Kim met Dev Shah at standup. Reach dev@standup.team"
    ents = ee._fallback(text)
    person_names = {e["name"] for e in ents if e["type"] == "person"}
    assert "Sarah Kim" in person_names
    assert any(e["type"] == "domain" and e["name"] == "standup.team" for e in ents)


def test_unknown_source_uses_fallback():
    ents = ee.extract_entities("Ruth Diaz and email r@corp.io", source_type="slack")
    assert any(e["name"] == "Ruth Diaz" and e["type"] == "person" for e in ents)


def test_public_api_dispatches_by_source():
    people = ee.extract_entities("From: Mina Otis <m@cap.me>", source_type="email")
    assert any(e["type"] == "person" and e["name"] == "Mina Otis" for e in people)


def test_extractor_exception_falls_to_empty(monkeypatch):
    def _boom(_text):
        raise RuntimeError("boom")
    monkeypatch.setitem(ee.EXTRACTORS, "chat", _boom)
    assert ee.extract_entities("[X] hello", source_type="chat") == []
