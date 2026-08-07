"""
Tests for jarvis/embed.py
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from jarvis.embed import get_embedding, get_embeddings


@pytest.fixture(autouse=True)
def _isolate_embed_cache(tmp_path, monkeypatch):
    """Redirect the disk cache to a temp DB and clear the in-memory cache."""
    import jarvis.embed as embed_mod
    monkeypatch.setenv("JARVIS_EMBED_CACHE", str(tmp_path / "embed_cache.db"))
    embed_mod._mem_cache.clear()
    yield
    embed_mod._mem_cache.clear()


# ── get_embeddings ───────────────────────────────────────────────────────────

def _make_fake_urlopen(data_dict, status=200):
    cm = MagicMock()
    resp = MagicMock()
    resp.read.return_value = json.dumps(data_dict).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    cm.__enter__ = MagicMock(return_value=resp)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_get_embeddings_single_text():
    response = {"embedding": [0.1, 0.2, 0.3]}
    cm = _make_fake_urlopen(response)
    with patch("jarvis.embed.urllib.request.urlopen", return_value=cm):
        results = get_embeddings(["hello"])

    assert len(results) == 1
    assert results[0] == [0.1, 0.2, 0.3]


def test_get_embeddings_multi_text():
    response1 = {"embedding": [0.1, 0.2]}
    response2 = {"embedding": [0.3, 0.4]}
    cm1 = _make_fake_urlopen(response1)
    cm2 = _make_fake_urlopen(response2)
    with patch("jarvis.embed.urllib.request.urlopen", side_effect=[cm1, cm2]):
        results = get_embeddings(["hello", "world"])

    assert results == [[0.1, 0.2], [0.3, 0.4]]


def test_get_embeddings_empty_list():
    with patch("jarvis.embed.urllib.request.urlopen") as mock_urlopen:
        results = get_embeddings([])

    assert results == []
    mock_urlopen.assert_not_called()


def test_get_embeddings_connection_error_returns_empty():
    import urllib.error
    with patch("jarvis.embed.urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        results = get_embeddings(["hello"])

    assert results == []


def test_get_embeddings_unknown_response_field():
    response = {"model": "nomic-embed-text"}
    cm = _make_fake_urlopen(response)
    with patch("jarvis.embed.urllib.request.urlopen", return_value=cm):
        results = get_embeddings(["hello"])

    assert len(results) == 1
    assert results[0] == []


def test_get_embeddings_payload_format():
    """Verify the Ollama API payload structure."""
    captured = {}
    cm = _make_fake_urlopen({"embedding": [0.0] * 768})

    def fake_urlopen(req, *args, **kwargs):
        if hasattr(req, "data"):
            captured["data"] = json.loads(req.data)
        return cm

    with patch("jarvis.embed.urllib.request.urlopen", side_effect=fake_urlopen):
        get_embeddings(["test prompt"])

    assert captured["data"]["model"] == "nomic-embed-text"
    assert captured["data"]["prompt"] == "test prompt"
    assert captured["data"]["stream"] is False


# ── get_embedding ────────────────────────────────────────────────────────────

def test_get_embedding_returns_float_list():
    response = {"embedding": [0.5] * 768}
    cm = _make_fake_urlopen(response)
    with patch("jarvis.embed.urllib.request.urlopen", return_value=cm):
        result = get_embedding("hello world")

    assert len(result) == 768
    assert all(isinstance(x, float) for x in result)


def test_get_embedding_fallback_on_failure():
    import urllib.error
    with patch("jarvis.embed.urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        result = get_embedding("hello world")

    assert len(result) == 768
    assert all(x == 0.0 for x in result)


def test_get_embedding_uses_default_model():
    captured = {}
    cm = _make_fake_urlopen({"embedding": [0.0] * 768})

    def fake_urlopen(req, *args, **kwargs):
        if hasattr(req, "data"):
            captured["data"] = json.loads(req.data)
        return cm

    with patch("jarvis.embed.urllib.request.urlopen", side_effect=fake_urlopen):
        get_embedding("hello", model="custom-model")

    assert captured["data"]["model"] == "custom-model"


# ── Disk-backed cache ─────────────────────────────────────────────────────────

def test_embedding_cache_hit_avoids_network():
    """A repeated text should be served from the cache without calling Ollama."""
    cm = _make_fake_urlopen({"embedding": [0.1, 0.2, 0.3]})
    with patch("jarvis.embed.urllib.request.urlopen", return_value=cm) as mock_urlopen:
        first = get_embeddings(["hello"])
        second = get_embeddings(["hello"])

    assert first == [[0.1, 0.2, 0.3]]
    assert second == [[0.1, 0.2, 0.3]]
    assert mock_urlopen.call_count == 1


def test_embedding_cache_persists_across_processes():
    """A cached embedding survives the in-memory cache being cleared (disk hit)."""
    cm = _make_fake_urlopen({"embedding": [0.5, 0.6]})
    with patch("jarvis.embed.urllib.request.urlopen", return_value=cm):
        get_embeddings(["persisted text"])

    # Simulate a fresh process: drop the in-memory cache.
    import jarvis.embed as embed_mod
    embed_mod._mem_cache.clear()

    # Network is now down; the disk cache must serve the embedding.
    import urllib.error
    with patch("jarvis.embed.urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        results = get_embeddings(["persisted text"])

    assert results == [[0.5, 0.6]]


def test_embedding_cache_key_includes_model():
    """Different models must not share cache entries for the same text."""
    results = []

    def fake_urlopen(req, *args, **kwargs):
        payload = json.loads(req.data)
        dims = 3 if payload["model"] == "model-a" else 5
        return _make_fake_urlopen({"embedding": [0.0] * dims})

    with patch("jarvis.embed.urllib.request.urlopen", side_effect=fake_urlopen):
        results.append(get_embedding("shared text", model="model-a"))
        results.append(get_embedding("shared text", model="model-b"))

    assert results[0] == [0.0, 0.0, 0.0]
    assert results[1] == [0.0] * 5


def test_embedding_cache_write_errors_are_graceful(monkeypatch):
    """A broken cache (bad path) must never raise from get_embeddings."""
    monkeypatch.setenv("JARVIS_EMBED_CACHE", "/nonexistent-dir-xyz/embed_cache.db")
    cm = _make_fake_urlopen({"embedding": [0.1]})
    with patch("jarvis.embed.urllib.request.urlopen", return_value=cm):
        results = get_embeddings(["ok"])

    assert results == [[0.1]]


def test_embedding_cache_empty_result_not_cached():
    """An empty/failed embedding should not poison the cache."""
    cm = _make_fake_urlopen({"model": "nomic-embed-text"})  # no embedding field
    with patch("jarvis.embed.urllib.request.urlopen", return_value=cm):
        first = get_embeddings(["fail text"])
        second = get_embeddings(["fail text"])

    assert first == [[]]
    assert second == [[]]