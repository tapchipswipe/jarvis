"""Tests for jarvis.collectors (deep/bookmarks) and device_id helpers.

Pure/testable functions — no real scans, reads, or network.
"""
from __future__ import annotations

import json
from pathlib import Path

from jarvis import device_id as dev
from jarvis.collectors import bookmarks, deep

# ── deep._should_exclude ──────────────────────────────────────────────────────

def test_deep_excludes_known_dirs():
    assert deep._should_exclude(Path("/u/code/.git/objects/x")) is True
    assert deep._should_exclude(Path("/u/code/node_modules/y.js")) is True
    assert deep._should_exclude(Path("/u/code/.venv/lib/x.py")) is True


def test_deep_includes_normal_source():
    assert deep._should_exclude(Path("/u/code/src/main.py")) is False
    assert deep._should_exclude(Path("/u/code/notes.txt")) is False


# ── bookmarks tree-walkers ────────────────────────────────────────────────────

def test_walk_safari_recurses_and_collects():
    node = {"Children": [
        {"Name": "A", "URLString": "https://a.dev",
         "URIDictionary": {"lastVisitedDate": 12345.0}},
        {"Children": [
            {"Name": "B", "URLString": "https://b.dev", "URIDictionary": {}},
        ]},
    ]}
    out = bookmarks._walk_safari(node)
    assert {b["name"] for b in out} == {"A", "B"}
    assert out[0]["date"] == "12345.0"


def test_walk_chrome_children_handles_url_and_folder():
    tree = {"children": [
        {"type": "url", "name": "Site", "url": "https://site.io", "date_added": "d1"},
        {"type": "folder", "children": [
            {"type": "url", "name": "Nested", "url": "https://n.dev", "date_added": "d2"},
        ]},
        {"type": "other", "name": "skip me"},
    ]}
    out = bookmarks._walk_chrome_children(tree)
    assert {b["name"] for b in out} == {"Site", "Nested"}
    assert {"url": "https://site.io", "name": "Site", "date": "d1"} in out


def test_walk_chrome_reads_json_file(tmp_path):
    bm = tmp_path / "Bookmarks"
    bm.write_text(json.dumps({"roots": {"bookmark_bar": {"children": [
        {"type": "url", "name": "X", "url": "https://x.dev", "date_added": "a"},
    ]}}}), encoding="utf-8")
    out = bookmarks._walk_chrome(bm)
    assert out and out[0]["name"] == "X"


def test_walk_chrome_empty_file_returns_empty(tmp_path):
    bm = tmp_path / "Bookmarks"
    bm.write_text("not json", encoding="utf-8")
    assert bookmarks._walk_chrome(bm) == []


# ── device_id helpers ─────────────────────────────────────────────────────────

def test_get_hostname_uses_nodename(monkeypatch):
    class _U:
        nodename = "host-xyz"
    monkeypatch.setattr(dev.os, "uname", lambda: _U())
    assert dev._get_hostname() == "host-xyz"


def test_mac_address_falls_back_to_unknown(monkeypatch):
    # On macOS there is no /sys/class/net; the lookup raises and yields "unknown".
    monkeypatch.setattr(dev.os, "listdir", lambda p: (_ for _ in ()).throw(FileNotFoundError()))
    assert dev._mac_address() == "unknown"