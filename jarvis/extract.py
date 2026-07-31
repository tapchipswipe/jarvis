import json
import urllib.request
import urllib.error
from functools import lru_cache

PROMPT_EXTRACT = """You are a knowledge extraction engine. Read the text below and output ONLY valid JSON with two arrays:
{"tags": ["topic1", "topic2", ...], "entities": ["Person", "Place", "Concept", ...]}

Rules:
- tags: 3-7 topical tags (lowercase, no spaces)
- entities: 3-7 named entities or key concepts
- No explanations, no markdown, just the JSON

TEXT:
{text}"""

_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434


def _ollama_generate(model: str, prompt: str) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "")
    except (urllib.error.URLError, urllib.error.HTTPError):
        return ""


@lru_cache(maxsize=None)
def extract_metadata(text: str, model: str = "qwen2.5:7b-instruct-q4_K_M") -> dict:
    try:
        raw = _ollama_generate(model, PROMPT_EXTRACT.format(text=text[:2000]))
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        tags = data.get("tags", [])[:7]
        entities = data.get("entities", [])[:7]
        return {"tags": tags, "entities": entities}
    except Exception:
        return {"tags": [], "entities": []}
