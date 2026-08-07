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
- `[done]` 2026-08-06 — `jarvis collect --root <dir>` (repeatable) to point the thin-client collector at arbitrary directories; defaults to Documents/notes/obsidian
- `[done]` 2026-08-06 — `jarvis export` is thin-client aware: in client mode it pulls from the live box (`/api/export` JSON, fields normalized + filtered client-side); only local mode reads the local store
- `[done]` 2026-08-06 — Modernized every `datetime.utcnow()` call (naive-UTC) to `datetime.now(timezone.utc).replace(tzinfo=None)` across all source files — same naive-UTC strings, no behavior change; pytest warnings fell from ~370 to 2 (both 3rd-party)

- `[done]` 2026-08-06 — Pure-liveness `/api/health` (no store access, never stalls under inference load) + store-aware `/api/health/deep`; **all** API handlers converted to sync `def` so blocking Store/LLM work runs in the threadpool and never blocks the uvicorn event loop (Round 5b — fixes intermittent 000s on every endpoint, root cause of earlier health blips)
- `[done]` 2026-08-06 — Phase 0 topology decision documented (`docs/topology.md`): Lightspeed = single source of truth + single writer (FULL-THIN); probe shows ~8 GB free RAM steady-idle, ~259 GB disk free, Ollama running nomic-embed on CPU
- `[done]` 2026-08-06 — Reusable Lightspeed probe script `scripts/lightspeed-probe.ps1` + `jarvis server --check` deploy validation passing locally
- `[planned]` 2026-08-06 — Deploy `jarvis server` to Lightspeed (Task Scheduler / start-jarvis.bat), cut Mac over to `JARVIS_MODE=client`, relocate triggers/Mayor/digests server-side, hash-verified backfill, then retire Mac-local store/push

## Round 8 (2026-08-06→07) — Night-autonomous pass (agent-only)

- `[done]` 2026-08-06 — Inbox backlog ingester (`jarvis/inbox_ingest.py`) built + committed: in-process, embed-only (`nomic-embed-text`), sidecar-driven route/tags, throttled batches, content-hash + memory-id idempotent, cursor-persisted so repeated batches drain the whole backlog. **Pushed to `bot`==`main` but the box's git is at `966d7b2` (~20 commits behind) and its *running* `jarvis server` predates it — it needs `git pull` + a process restart to load** (verified: `C:/data/jarvis/inbox` still 5,458 files, no `inbox_ingest_cursor.txt`). Once restarted it drains in-process; watch `/api/ingest/status` (Round 7 step 1 — built + pushed, activation pending)
- `[done]` 2026-08-06 — **Inbox-ingester observability**: thread-safe `ingest_status()` progress registry + `GET /api/ingest/status` (active/enabled/processed/added/remaining/done) + `jarvis ingest-status` CLI + health-check `box ingest:` line + `jarvis doctor` surfacing it — so the drain is visible the moment the box restarts
- `[done]` 2026-08-06 — **Server token-enforcement consistency**: extended `_host_ok` to the previously-open mutating/sensitive routes — `/api/idea`, `/api/tasks/approve`, `/api/tasks/reject`, `/api/sessions`, `/api/export`, and new `/api/memories` — config-gated (enforced only when `JARVIS_TOKEN` set; loopback always allowed; `/api/health`/`/api/ingest/status` stay open)
- `[done]` 2026-08-06 — **Thin-client ambient collection restored** (`jarvis/collectors/thin.py` + `jarvis collect` with `--root`/`--max-files`/`--flush`): queues new file text into the disposable outbox → server; bounded, unchanged-file fingerprint skip, content-hash idempotent on outbox + server; no local Chroma handle. Opt-in LaunchAgent shipped (`scripts/jarvis-collect.sh` + `com.user.jarvis-collect.plist`, 30-min) — not auto-started
- `[done]` 2026-08-06 — Thin-client CLI correctness: `status`, `export`, `memories`, `timeline` now report the **live box** (not the stale local rollback store) in client mode; `search` surfaces real HTTP errors vs offline; new `/api/memories` endpoint (limit/source/tier/since, pre-decoded tags)
- `[done]` 2026-08-06 — `jarvis doctor` — one-shot local+box diagnostics (env mode, outbox backlog, box health/memories, ingest status, os)
- `[done]` 2026-08-06 — Modernized every `datetime.utcnow()` call (22 source files) to `datetime.now(timezone.utc).replace(tzinfo=None)` — string-identical naive-UTC; pytest warnings fell from ~370 to 2 (both 3rd-party)
- `[done]` 2026-08-06 — SAFE-boundary decision + `docs/runtime-audit.md`: verified exactly what runs in the `jarvis server` process (API, Mayor loop, Mayor idle-maintenance YES; inbox ingester wired-but-needs-restart; `TriggerLoop` triggers/digests NOT running — died with the daemon) and confirmed the thin-client read/write/status/export/memories/doctor paths live against the box
- `[planned]` 2026-08-06 — Enable `JARVIS_TOKEN` end-to-end (same value in box env + `server-start.bat` and in `~/.zshrc` on the Mac) + register the `com.user.jarvis-collect` LaunchAgent
- `[done]` 2026-08-07 — **Server-side digests/triggers restored (Round 9):** config-gated `start_trigger_loop()` (env `JARVIS_TRIGGERS=1`, default OFF) runs a `TriggerLoop` **inside `jarvis server`** — per-tick Store open/close (no persistent second Chroma handle, same pattern as the ingester). Defaults: LLM morning-brief (08:00) + end-of-day wrap (18:00) weekdays, 30-min calendar notify poll. Wired into `run_dashboard`; 3 new tests.

## Round 9b (2026-08-07) — Finish-the-backlog hardening pass

- `[done]` **HTTPS + pinned client (config-gated):** `jarvis server --tls-cert/--tls-key` (or `JARVIS_TLS_CERT/KEY`) serves HTTPS; `jarvis server --gen-cert` mints a self-signed pair + fingerprint. Client `remote.py` supports `https://` with **SHA256 cert-fingerprint pinning** (`JARVIS_TLS_FINGERPRINT` or `~/.config/jarvis/server-fingerprint`) → real MitM resistance without a CA. TLS secrets are gitignored (`*.pem`). Tests: end-to-end against a real local TLS server (9 tests).
- `[done]` **Ingester idle fast-path:** a drained/unchanged inbox now idles on a cheap fingerprint (count + max mtime_ns) instead of re-scanning the tree every cycle; `idle` exposed in `/api/ingest/status`; `JARVIS_INBOX_IDLE` configurable.
- `[done]` **Crash-consistent backups:** new `jarvis backup` (SQLite online-backup for meta.db/embed_cache/chroma.sqlite3 — valid even while live) + token-gated in-process `POST /api/admin/backup` (avoids invoking a second Python on the Windows Store-Python box). `scripts/jarvis-backup.sh` now uses it; `JARVIS_BACKUP_STRICT=1` pauses the `JarvisServer` scheduled task for a fully consistent HNSW snapshot and always restarts it.
- `[done]` **Digest model guard:** `JARVIS_DIGEST_MODEL` configurable (default chat model); large-tier models log a RAM-discipline warning; an `[ollama …]` error string is never digested (falls back to chat model then static). Bug fixed: a dead model previously got digested as error text.
- `[done]` **`jarvis ask "<question>"`** — one-shot, ALWAYS grounded on the brain: retrieves top memories, answers via LLM, prints answer + grounding sources (local or box in client mode; `--json-out`).
- `[done]` **`jarvis delegate "<task>"`** — productized offload: runs `cline` on the box via SSH and returns its JSON. NOTE: the box's cline currently reports **Cline Credits balance $0** — mechanical path validated; needs credits funded to actually run work.
- `[done]` **Multi-user isolation hardening:** `ensure_private_dir` now locks 0700 on *every* directory Jarvis creates (whole `~/jarvis/users/<user>` chain), not just the leaf.
- `[done]` **Coverage 50 → 54%** (extract_entities 0→96%, mayor 37→57%) — 24 new tests; ruff debt 344 → ~220 (131 mechanical auto-fixes applied; remaining are intentional catch-all `except Exception` guards — BLE001 — plus a few style items, documented).
- `[done]` **Alerting:** `scripts/jarvis-health-check.sh` now reaches you — macOS Notification Center banner + optional `JARVIS_ALERT_WEBHOOK` (ntfy/Telegram/Discord), rate-limited per alert-type (default 30 min) so a persistent outage pages you once.
- `[done]` **Token-over-HTTPS integration test** locked in the suite (client sends `X-Jarvis-Token` over a real TLS channel).
- `[deferred]` Tailscale ACL console edit (scopes who can hit `:8766`) — requires the Tailscale admin console; docs note it. HTTPS + token make the LAN/plain-HTTP exposure moot.


## Round 7 (2026-08-06) — Thin-client cutover (backfill + retire Mac brain)


- `[done]` 2026-08-06 — Field-preserving `/api/backfill` endpoint (server) that imports full memory records — original id, source, source_id, timestamp, tier, route, tags, metadata — with local embedding recompute (deterministic shared embed model), unlike `/api/remember` which re-timestamps/re-chunks/re-tiers (Round 7)
- `[done]` 2026-08-06 — `jarvis backfill` CLI (client): reads the local Mac store, computes a hash manifest (per-row sha256 + aggregate), posts field-preserving batches, verifies server active count; `--dry-run` / `--limit` / `--batch`
- `[done]` 2026-08-06 — Hash-verified backfill Mac → Lightspeed: 3,951 rows / 3,950 distinct memory contents migrated with **ids + timestamps + tiers + routes preserved**; content-set equality verified (0 box-only, 0 mac-only); one duplicate junk error-string collapsed by content-hash dedupe. Box store snapshotted pre-run (`C:/data/jarvis-backup-20260806-185806`) and post-backfill
- `[done]` 2026-08-06 — Mac cutover to thin client: `JARVIS_MODE=client` + `JARVIS_REMOTE=http://100.102.0.99:8766` persisted in `~/.zshrc`; write + read round-trip verified (outbox → `/api/remember` → box store); Mac-local brain retired — `com.user.jarvis` (daemon 8765), `com.jarvis.dashboard` (8766), watcher, sync unloaded and plists moved to `~/jarvis/rollback-launchagents-<ts>/`; Mac-local scan/push+promote cron retired (box is single writer); Mac store left intact for rollback
- `[done]` 2026-08-06 — Resilience layer (Mac-side, no Chroma multi-process risk): `scripts/jarvis-backup.sh` (warm on-box snapshot to `C:/data/jarvis-rollback/<ts>` + daily off-box copy to `~/jarvis/backups`, rolling prune) and `scripts/jarvis-health-check.sh` (box `/api/health` + outbox backlog + box disk free; alerts to `logs/health-alerts.log`); registered as LaunchAgents `com.user.jarvis-backup` (daily 4:05) and `com.user.jarvis-health` (every 30 min)
- `[planned]` 2026-08-06 — Ingest the box inbox backlog (`C:/data/jarvis/inbox`, ~2,730 txt + 2,728 json sidecars) into the box store. **Deferred:** a separate-process ingester would open a second Chroma handle on the live single-brain box (multi-process lock risk). Must be integrated into the `jarvis server` process (Round 7 plan step 4) or run in a maintenance window, not run ad-hoc parallel.
