import ollama

PROMPT_EXTRACT = """You are a knowledge extraction engine. Read the text below and output ONLY valid JSON with two arrays:
{"tags": ["topic1", "topic2", ...], "entities": ["Person", "Place", "Concept", ...]}

Rules:
- tags: 3-7 topical tags (lowercase, no spaces)
- entities: 3-7 named entities or key concepts
- No explanations, no markdown, just the JSON

TEXT:
{text}"""


def extract_metadata(text: str, model: str = "qwen2.5:7b-instruct-q4_K_M") -> dict:
    try:
        response = ollama.generate(model=model, prompt=PROMPT_EXTRACT.format(text=text[:2000]), stream=False)
        raw = response.get("response", "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        tags = data.get("tags", [])[:7]
        entities = data.get("entities", [])[:7]
        return {"tags": tags, "entities": entities}
    except Exception:
        return {"tags": [], "entities": []}
