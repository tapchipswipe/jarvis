# Brain Upgrades

Feature requests and planned upgrades for the Jarvis. Each entry is also stored as a memory with the `upgrade` tag.

## Format

- `[status]` — `requested`, `planned`, `in-progress`, `done`
- `[date]` — when the request was made
- **[feature]** — description

---

---

- `[done]` 2025-01-15 — Enable SQLite WAL mode for concurrent read/write safety (critical foundation fix)
- `[done]` 2025-01-15 — Fix consolidation.py NameError crash (jarvis_store undefined var on line 31)
- `[done]` 2025-01-15 — Fix embed.py Ollama multi-text bug — iterate one text at a time instead of sending Python list
- `[planned]` 2025-01-15 — Add incremental vector re-indexing so new memories get embedded without full re-sync
- `[planned]` 2025-01-15 — Implement memory decay / tier promotion schedule so raw memories auto-promote to session tier after 7 days
- `[requested]` 2025-01-15 — Multi-user support with isolated per-user storage directories and access control
- `[done]` 2026-07-31 — CLI `/export` command to dump all memories as JSON or Markdown for portability
- `[done]` 2026-07-31 — Embedding cache (SQLite-backed + in-memory) so repeated embeddings are served without re-calling Ollama
- `[done]` 2026-07-31 — Daemon trigger loop thread evaluating time/poll/event triggers on a fixed cadence
- `[done]` 2026-07-31 — Knowledge graph HTTP endpoints: `/api/entities` and `/api/entities/{id}/relationships`
- `[done]` 2026-08-05 — Incremental vector re-indexing (`jarvis reindex`) — embeds only memories missing from the vector store (no full re-sync)
- `[done]` 2026-08-05 — Memory decay / tier promotion (`jarvis promote`) — raw memories auto-promote to session tier after 7 days
- `[done]` 2026-08-05 — Multi-user support with isolated per-user storage directories and access control
- `[done]` 2026-08-05 — Daily memory maintenance scheduled via cron (promote + reindex) on macOS and Lightspeed
- `[done]` 2026-08-05 — Device sync hardening: photos skip gracefully without exiftool; deep scan no longer walks huge Library dirs / spams Errno 11
- `[done]` 2026-08-05 — Durable push queue (push_memories): retry/backoff, batch tar upload, sync_log writes, never drop when offline (Round 4)
- `[done]` 2026-08-05 — LLM-synthesized morning/end-of-day digests from real memories + task counts (Round 4)
- `[done]` 2026-08-05 — Mayor idle maintenance (reindex ~5m, promote ~6h) with Ollama VRAM guard (Round 4)
- `[done]` 2026-08-05 — Knowledge graph surfaced in search, chat RAG, and dashboard memories (Round 4)
