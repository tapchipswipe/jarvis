# Jarvis

Local first jarvis agent. Ambient collection + local LLM reasoning.

## Quick start

```bash
python -m jarvis.cli status
python -m jarvis.cli chat
python -m jarvis.cli search "what did I do last week"
python -m jarvis.cli sync
python -m jarvis.cli remember "something important"
```

## Architecture

- All data local (ChromaDB + SQLite)
- Ollama for embeddings and LLM
- Continuous collection: files, shell, browser, Kilo sessions
- Sunday deep sync + manual `/sync`
