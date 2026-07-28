import json
import urllib.request
import urllib.error

DEFAULT_EMBED_MODEL = "nomic-embed-text"
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434


def _ollama_embed(model: str, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    payload = json.dumps({"model": model, "prompt": texts if len(texts) > 1 else texts[0], "stream": False}).encode()
    req = urllib.request.Request(
        f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            if "embedding" in data:
                return [data["embedding"]]
            if "embeddings" in data:
                return data["embeddings"]
            return []
    except (urllib.error.URLError, urllib.error.HTTPError):
        return []


def get_embeddings(texts: list[str], model: str = DEFAULT_EMBED_MODEL) -> list[list[float]]:
    if not texts:
        return []
    return _ollama_embed(model, texts)


def get_embedding(text: str, model: str = DEFAULT_EMBED_MODEL) -> list[float]:
    result = get_embeddings([text], model=model)
    return result[0] if result else []
