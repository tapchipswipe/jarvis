DEFAULT_CHUNK_SIZE = 2048
DEFAULT_OVERLAP = 200

_HAS_SEMANTIC_CHUNKER = False
try:
    from semantic_text_chunker import SemanticTextChunker
    _HAS_SEMANTIC_CHUNKER = True
except ImportError:
    pass


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP):
    if _HAS_SEMANTIC_CHUNKER:
        try:
            chunker = SemanticTextChunker(chunk_size=chunk_size, overlap=overlap)
            return chunker.chunk(text)
        except Exception:
            pass
    return _fallback_chunk(text, chunk_size, overlap)


def _fallback_chunk(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP):
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        start = end - overlap if end < n else n
    return chunks


def chunk_document(text: str, metadata: dict | None = None):
    chunks = chunk_text(text)
    return [{"text": c, "metadata": metadata or {}} for c in chunks]
