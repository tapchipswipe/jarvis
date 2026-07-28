# Second Brain — Distributed Architecture

```
Any Device (Mac / laptop)
  │
  │ watchdog observes local folders
  │ on change: scp to Lightspeed
  ▼
Lightspeed (Dell G7) — brain runtime
  ├── Ollama (Qwen 2.5 7B)
  ├── ChromaDB + SQLite
  ├── Inbox watcher (device pushes)
  ├── Local watchers (Documents, Obsidian)
  ├── Cron: daily 04:00 → session summaries
  ├── Cron: Sunday 06:00 → weekly reflections
  ├── Cron: 1st of month 06:00 → monthly arcs
  ├── Agent loop + CLI
  │
  └── Memory tiers:
        Arc (weight=1.5) → Reflection (1.0) → Session (0.6) → Raw (0.3)
         ▲
         │
    You chat via SSH / CLI
```

All data lives on Lightspeed. Devices are dumb collectors.
