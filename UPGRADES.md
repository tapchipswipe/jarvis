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
- `[done]` 2025-01-15 — Add incremental vector re-indexing so new memories get embedded without full re-sync (superseded by the 2026-08-05 `jarvis reindex` delivery below)
- `[done]` 2025-01-15 — Implement memory decay / tier promotion schedule so raw memories auto-promote to session tier after 7 days (superseded by the 2026-08-05 `jarvis promote` delivery below)
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
- `[done]` 2026-08-06 — Thin-client read/write API (`/api/remember`, `/api/search`, `/api/chat`, `/api/sessions`, `/api/export`, `/api/health`) served by the dashboard app; async → sync handlers so heavy Store/LLM work never blocks the event loop (Round 5)
- `[done]` 2026-08-06 — Disposable client cache (`jarvis/cache.py`): durable idempotent write-outbox + rolling read-tail; `JARVIS_CACHE` resolved lazily so tests/modes can override it (Round 5)
- `[done]` 2026-08-06 — Thin remote client (`jarvis/remote.py`) + `collectors.capture()`; CLI remember/search/export honor `JARVIS_MODE=client` (`JARVIS_REMOTE`) (Round 5)
- `[planned]` 2026-08-06 — Full server relocation: run triggers/Mayor/digests in the Lightspeed `jarvis server` process; retire Mac-local store/push (thin-client cutover)
- `[done]` 2026-08-06 — Offline read fallback in CLI search (rolling tail + substring glance + "offline — cached subset" banner); Round 8 hardening: a reachable server that rejects us (e.g. HTTP 403 from the token guards) is now surfaced as "Server error (<code>)" instead of being mislabelled as offline
- `[done]` 2026-08-06 — `jarvis status` is thin-client aware: in client mode it reports the live box (`/api/health/deep` memories/mode/uptime) + local outbox backlog instead of the stale local (rollback) store snapshot
- `[done]` 2026-08-06 — `jarvis doctor` — one-shot local+box diagnostics (env mode, outbox backlog, box health/memories, ingest status, os info) for quick operational triage
- `[done]` 2026-08-06 — Modernized every `datetime.utcnow()` call (naive-UTC) to `datetime.now(timezone.utc).replace(tzinfo=None)` across all source files — same naive-UTC strings, no behavior change; pytest warnings fell from ~370 to 2 (both 3rd-party)

- `[done]` 2026-08-06 — Pure-liveness `/api/health` (no store access, never stalls under inference load) + store-aware `/api/health/deep`; **all** API handlers converted to sync `def` so blocking Store/LLM work runs in the threadpool and never blocks the uvicorn event loop (Round 5b — fixes intermittent 000s on every endpoint, root cause of earlier health blips)
- `[done]` 2026-08-06 — Phase 0 topology decision documented (`docs/topology.md`): Lightspeed = single source of truth + single writer (FULL-THIN); probe shows ~8 GB free RAM steady-idle, ~259 GB disk free, Ollama running nomic-embed on CPU
- `[done]` 2026-08-06 — Reusable Lightspeed probe script `scripts/lightspeed-probe.ps1` + `jarvis server --check` deploy validation passing locally
- `[planned]` 2026-08-06 — Deploy `jarvis server` to Lightspeed (Task Scheduler / start-jarvis.bat), cut Mac over to `JARVIS_MODE=client`, relocate triggers/Mayor/digests server-side, hash-verified backfill, then retire Mac-local store/push

## Round 8 (2026-08-06→07) — Night-autonomous pass (agent-only)

- `[done]` 2026-08-06 — Inbox backlog ingester (`jarvis/inbox_ingest.py`) built, committed, **deployed to the box**, and running **in-process** in the `jarvis server` process (embed-only with `nomic-embed-text`, sidecar-driven route/tags, throttled batches, idempotent on content-hash + memory-id, cursor-persisted). Box reached `966d7b2` == `bot` == `origin/bot`; `/api/health/deep` already climbing as it drains `C:/data/jarvis/inbox` (Round 7 step 1 — marked done)
- `[done]` 2026-08-06 — CLI regression test mode isolation: the Round 7 thin-client cutover persisted `JARVIS_MODE=client`, so the local path test started taking the remote branch; tests now pin `remote.is_remote()` per case and add explicit thin-client coverage
- `[done]` 2026-08-06 — **Server token-enforcement consistency**: `/api/remember`, `/api/backfill`, `/api/search`, `/api/chat` were already guarded by `_host_ok`; extended the same guard to the previously-open mutating/sensitive routes — `/api/idea`, `/api/tasks/approve`, `/api/tasks/reject`, `/api/sessions`, and `/api/export` — config-gated (enforced only when `JARVIS_TOKEN` is set; loopback always allowed; `/api/health` stays open for the Mac health checker)
- `[done]` 2026-08-06 — **Thin-client ambient collection restored** (`jarvis/collectors/thin.py` + `jarvis collect` CLI): Mac walks user-authored roots (Documents/notes/obsidian), queues new file text into the disposable outbox, and flushes to the server on request — no local Store/Chroma handle. Bounded (`--max-files`), unchanged-file fingerprint skip, content-hash idempotent on both the outbox and the server (`store.add`). Ship + opt-in LaunchAgent (`scripts/jarvis-collect.sh` + `com.user.jarvis-collect.plist`, 30-min) — not auto-started
- `[planned]` 2026-08-06 — Enable `JARVIS_TOKEN` end-to-end (box env + Mac `~/.zshrc`) now that all mutating/sensitive routes are guarded; register the `com.user.jarvis-collect` LaunchAgent
- `[planned]` 2026-08-06 — Verify triggers/digests/idle-maintenance are all running inside the `jarvis server` process (Mayor is; standalone sync daemon retired)


## Round 7 (2026-08-06) — Thin-client cutover (backfill + retire Mac brain)


- `[done]` 2026-08-06 — Field-preserving `/api/backfill` endpoint (server) that imports full memory records — original id, source, source_id, timestamp, tier, route, tags, metadata — with local embedding recompute (deterministic shared embed model), unlike `/api/remember` which re-timestamps/re-chunks/re-tiers (Round 7)
- `[done]` 2026-08-06 — `jarvis backfill` CLI (client): reads the local Mac store, computes a hash manifest (per-row sha256 + aggregate), posts field-preserving batches, verifies server active count; `--dry-run` / `--limit` / `--batch`
- `[done]` 2026-08-06 — Hash-verified backfill Mac → Lightspeed: 3,951 rows / 3,950 distinct memory contents migrated with **ids + timestamps + tiers + routes preserved**; content-set equality verified (0 box-only, 0 mac-only); one duplicate junk error-string collapsed by content-hash dedupe. Box store snapshotted pre-run (`C:/data/jarvis-backup-20260806-185806`) and post-backfill
- `[done]` 2026-08-06 — Mac cutover to thin client: `JARVIS_MODE=client` + `JARVIS_REMOTE=http://100.102.0.99:8766` persisted in `~/.zshrc`; write + read round-trip verified (outbox → `/api/remember` → box store); Mac-local brain retired — `com.user.jarvis` (daemon 8765), `com.jarvis.dashboard` (8766), watcher, sync unloaded and plists moved to `~/jarvis/rollback-launchagents-<ts>/`; Mac-local scan/push+promote cron retired (box is single writer); Mac store left intact for rollback
- `[done]` 2026-08-06 — Resilience layer (Mac-side, no Chroma multi-process risk): `scripts/jarvis-backup.sh` (warm on-box snapshot to `C:/data/jarvis-rollback/<ts>` + daily off-box copy to `~/jarvis/backups`, rolling prune) and `scripts/jarvis-health-check.sh` (box `/api/health` + outbox backlog + box disk free; alerts to `logs/health-alerts.log`); registered as LaunchAgents `com.user.jarvis-backup` (daily 4:05) and `com.user.jarvis-health` (every 30 min)
- `[planned]` 2026-08-06 — Ingest the box inbox backlog (`C:/data/jarvis/inbox`, ~2,730 txt + 2,728 json sidecars) into the box store. **Deferred:** a separate-process ingester would open a second Chroma handle on the live single-brain box (multi-process lock risk). Must be integrated into the `jarvis server` process (Round 7 plan step 4) or run in a maintenance window, not run ad-hoc parallel.
