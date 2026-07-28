# Second Brain

Local first brain agent. Ambient collection + local LLM reasoning.

## Quick start

```bash
python -m brain.cli status
python -m brain.cli chat
python -m brain.cli search "what did I do last week"
python -m brain.cli sync
python -m brain.cli remember "something important"
```

## Architecture

- All data local (ChromaDB + SQLite)
- Ollama for embeddings and LLM
- Continuous collection: files, shell, browser, Kilo sessions
- Sunday deep sync + manual `/sync`
