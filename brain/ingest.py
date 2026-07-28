from semantic_text_chunker import SemanticTextChunker

DEFAULT_CHUNK_SIZE = 2048
DEFAULT_OVERLAP = 200


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP):
    chunker = SemanticTextChunker(chunk_size=chunk_size, overlap=overlap)
    return chunker.chunk(text)


def chunk_document(text: str, metadata: dict | None = None):
    chunks = chunk_text(text)
    return [{"text": c, "metadata": metadata or {}} for c in chunks]
