import json
import urllib.request
import urllib.error

DEFAULT_EMBED_MODEL = "nomic-embed-text"
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434


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


def get_embeddings(texts: list[str], model: str = DEFAULT_EMBED_MODEL) -> list[list[float]]:
    if not texts:
        return []
    return _ollama_embed(model, texts)


def get_embedding(text: str, model: str = DEFAULT_EMBED_MODEL) -> list[float]:
    result = get_embeddings([text], model=model)
    if result:
        return result[0]
    return [0.0] * 768
