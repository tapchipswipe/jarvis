# Jarvis Topology Decision (Phase 0)

## Probe results — Lightspeed (Dell G7, Windows)
| Metric | Value |
|---|---|
| Total RAM (usable) | ~15.8 GB (16,533,184 KB) |
| Free RAM (steady-idle, Ollama up) | ~8.0 GB (8,380,232 KB) |
| Free RAM floor (7B chat model loaded) | ~3 GB estimated |
| Disk C: free | ~259 GB |
| Ollama | running; only `nomic-embed-text:latest` loaded (vram=0, CPU) |

## Decision: FULL-THIN
Lightspeed already hosts the canonical store (SQLite + Chroma) and Ollama, and already
ingests pushed memories. It is the **single source of truth and the single writer**.
The Mac becomes a thin terminal + collectors + disposable cache.

**Rationale (vs data-thin):** the incremental RAM cost of running the FastAPI server
(`jarvis server`) beside the existing store + Ollama is small (~0.3–0.5 GB), leaving a
~3 GB floor with a 7B chat model loaded — adequate. The Mac's own local Ollama + store +
daemon are removed entirely, which *saves* Mac RAM and eliminates write-contention.

## Model-tier discipline (the RAM guard)
- Parsing/classification/embedding stay small (`llama3.2:1b`, `nomic-embed-text`).
- Chat uses `qwen2.5:7b` **only on demand**; it must be unloaded when idle.
- Never load >1 large (7B) model at once.
- The Mayor's existing `keep_alive`/idle-maintenance VRAM guard applies here.

## Single-writer rule
Only the Lightspeed `jarvis server` may write the canonical store. Clients (Mac) write
only to their local **outbox**, which flushes to the server. This kills write-contention.
