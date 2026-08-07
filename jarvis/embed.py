import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_EMBED_MODEL = "nomic-embed-text"
_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


_OLLAMA_PORT = _env_int("OLLAMA_PORT", 11434)

# In-process cache so repeated calls within one run never touch disk.
_mem_cache: dict[str, list[float]] = {}


def _ollama_embed(model: str, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    results: list[list[float]] = []
    for text in texts:
        payload = json.dumps({"model": model, "prompt": text, "stream": False}).encode()
        req = urllib.request.Request(
            f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
                if "embedding" in data:
                    results.append(data["embedding"])
                elif "embeddings" in data:
                    # API may return multiple; we sent one prompt, take first
                    embeddings = data["embeddings"]
                    results.append(embeddings[0] if embeddings else [])
                else:
                    results.append([])
        except (urllib.error.URLError, urllib.error.HTTPError):
            pass
    return results


# ---------------------------------------------------------------------------
# Disk-backed embedding cache (SQLite under the jarvis data dir)
# ---------------------------------------------------------------------------

def _cache_path() -> Path:
    """Location of the SQLite cache. Override with JARVIS_EMBED_CACHE."""
    env = os.environ.get("JARVIS_EMBED_CACHE")
    if env:
        return Path(env)
    from jarvis.paths import data_dir
    return data_dir("data", "embed_cache.db")


def _cache_key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


def _cache_conn() -> sqlite3.Connection:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS embed_cache ("
        " key TEXT PRIMARY KEY,"
        " model TEXT,"
        " text TEXT,"
        " embedding TEXT,"
        " created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    return conn


def _cache_get(key: str) -> list[float] | None:
    """Return a cached embedding, or None on miss / cache failure."""
    if key in _mem_cache:
        return _mem_cache[key]
    try:
        conn = _cache_conn()
        try:
            row = conn.execute(
                "SELECT embedding FROM embed_cache WHERE key = ?", (key,)
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            emb = json.loads(row[0])
            _mem_cache[key] = emb
            return emb
    except Exception:
        # Cache unavailable -> fall through to a live embed.
        pass
    return None


def _cache_put(key: str, model: str, text: str, embedding: list[float]) -> None:
    """Store an embedding. Write errors are non-fatal (never raise)."""
    if not embedding:
        return
    _mem_cache[key] = embedding
    try:
        conn = _cache_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO embed_cache (key, model, text, embedding)"
                " VALUES (?, ?, ?, ?)",
                (key, model, text, json.dumps(embedding)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def get_embeddings(texts: list[str], model: str = DEFAULT_EMBED_MODEL) -> list[list[float]]:
    if not texts:
        return []
    out: list[tuple[int, list[float]]] = []
    to_fetch: list[tuple[int, str, str]] = []
    for i, text in enumerate(texts):
        key = _cache_key(text, model)
        cached = _cache_get(key)
        if cached is not None:
            out.append((i, cached))
        else:
            to_fetch.append((i, key, text))
    if to_fetch:
        fresh = _ollama_embed(model, [t for _, _, t in to_fetch])
        for (idx, key, text), emb in zip(to_fetch, fresh):
            if emb:
                _cache_put(key, model, text, emb)
            out.append((idx, emb))
    out.sort(key=lambda pair: pair[0])
    return [emb for _, emb in out]


def get_embedding(text: str, model: str = DEFAULT_EMBED_MODEL) -> list[float]:
    result = get_embeddings([text], model=model)
    if result:
        return result[0]
    return [0.0] * 768
