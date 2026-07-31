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
- `[planned]` 2025-01-15 — CLI `/export` command to dump all memories as JSON or Markdown for portability
