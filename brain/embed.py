import ollama

DEFAULT_EMBED_MODEL = "nomic-embed-text"


def get_embeddings(texts: list[str], model: str = DEFAULT_EMBED_MODEL) -> list[list[float]]:
    if not texts:
        return []
    response = ollama.embed(model=model, input=texts)
    return response.get("embeddings", [])


def get_embedding(text: str, model: str = DEFAULT_EMBED_MODEL) -> list[float]:
    return get_embeddings([text], model=model)[0]
