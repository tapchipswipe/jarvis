"""Tests for the chunking primitives used by the ingester + brain."""
from __future__ import annotations

import pytest

from jarvis.ingest import _fallback_chunk, chunk_document, chunk_text


@pytest.fixture(autouse=True)
def _deterministic_chunker(monkeypatch):
    # Pin the pure-python fallback so results are exact regardless of whether the
    # optional semantic_text_chunker package is installed.
    monkeypatch.setattr("jarvis.ingest._HAS_SEMANTIC_CHUNKER", False)


def test_empty_returns_empty():
    assert chunk_text("") == []
    assert _fallback_chunk("") == []


def test_short_text_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_fallback_chunks_with_overlap():
    text = "x" * 100
    chunks = chunk_text(text, chunk_size=50, overlap=10)
    assert chunks[0] == "x" * 50
    assert chunks[-1] == "x" * 20  # trailing remainder
    # overlap causes the concatenated body to be longer than the original
    assert sum(len(c) for c in chunks) > 100
    # every subsequent chunk starts before its predecessor ended (overlap)
    assert chunks[1][0] == "x"


def test_fallback_exact_multiple_no_overlap_spill():
    # 60 chars, size 30, overlap 5 -> chunks at [0:30],[25:55],[50:60]
    text = "".join(str(i % 10) for i in range(60))
    overlap = 5
    chunks = _fallback_chunk(text, chunk_size=30, overlap=overlap)
    assert len(chunks) == 3
    # de-overlapped reconstruction (drop `overlap` from each subsequent chunk) == text
    recon = chunks[0] + "".join(c[overlap:] for c in chunks[1:])
    assert recon == text


def test_chunk_document_attaches_metadata():
    docs = chunk_document("a short body", metadata={"path": "/tmp/a.txt", "tier": "raw"})
    assert len(docs) == 1
    assert docs[0]["text"] == "a short body"
    assert docs[0]["metadata"] == {"path": "/tmp/a.txt", "tier": "raw"}


def test_chunk_document_default_metadata():
    docs = chunk_document("just text")
    assert docs[0]["metadata"] == {}
