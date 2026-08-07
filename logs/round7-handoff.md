# Round 7 — Handoff / Session Narrative

_2026-08-06. This session executed the thin-client cutover: hash-verified the Mac→Lightspeed backfill, retired the Mac-local brain, and stood up a resilience layer._

## Decisions reached
1. **Faithful migration over `/api/remember`.** `Brain.remember` re-timestamps, re-chunks, re-tiers, and derives a new id — wrong for a source-of-truth move. Built a field-preserving server `/api/backfill` that keeps original id/source/source_id/timestamp/tier/route/tags/metadata and recomputes the embedding locally (deterministic for the shared `nomic-embed-text`), so SQLite rows are byte-equivalent.
2. **`jarvis backfill` CLI** reads the *local* Mac store (not via `/api/remember`), computes a hash manifest (per-row sha256 + aggregate), posts batches, and reconciles the server active count.
3. **Do NOT run a separate-process inbox ingester on the live box.** A second Chroma `PersistentClient` handle on the same dir (server holds one) is a multi-process lock/inconsistency risk on the single-brain box. Inbox ingestion is deferred to server-side integration.
4. **Resilience runs on the Mac** (thin client) reading the box over HTTP/SSH — no second Chroma handle anywhere.

## What was done
1. **Backfill mechanism (Mac repo) + deploy:**
   - `jarvis/dashboard.py`: `/api/backfill` (field-preserving bulk import, batched embeddings).
   - `jarvis/remote.py`: `backfill_batch`.
   - `jarvis/cli.py`: `jarvis backfill` command.
   - Tests added (`test_server`, `test_client`); suite 325 → **328 passed** (+1 skip). Ruff-clean for changed regions.
   - Fixed a real bug found via the new test: real Chroma rejects `None` metadata values (a record with no timestamp crashed `store.add`) — endpoint now coerces `timestamp` to `""`.
   - Committed `b42e659`, pushed `main`/`bot`; box `git pull` → `b42e659`; restarted box server; `/api/backfill` verified live (200 on empty batch).
2. **Hash-verified backfill Mac → Lightspeed:**
   - Box store snapshotted pre-run (`C:/data/jarvis-backup-20260806-185806`).
   - `jarvis backfill --batch 100` pushed all 3,951 rows (0 skipped); ~20+ min wall-clock (box recomputes embeddings on CPU).
   - Verified: MAC 3,951 rows/3,950 distinct contents == BOX 3,950 rows/3,950 distinct contents; **content-set equality True, 0 box-only, 0 mac-only**. One Mac duplicate (junk `[ollama connection error...]` row) correctly collapsed by content-hash dedupe. Removed two transient test-artifact memories (`cutover round-trip` test, `hello capture`) from the box. Final box count 3,950.
3. **Mac cutover to thin client:**
   - Verified client round-trip: `remember` (outbox → `/api/remember`) + `search` against the box.
   - Persisted `JARVIS_MODE=client` + `JARVIS_REMOTE=http://100.102.0.99:8766` in `~/.zshrc`.
   - Retired Mac-local brain: unloaded + moved plists (`com.user.jarvis`, `com.jarvis.dashboard`, `com.user.jarvis-watcher`, `com.user.jarvis-sync`) → `~/jarvis/rollback-launchagents-20260806-204835/`. Removed Mac-local `scan`/`push_memories`/`promote` cron jobs (box is single writer; antigravity cron preserved). Mac store left intact for rollback.
4. **Resilience:**
   - `scripts/jarvis-backup.sh` — warm on-box snapshot to `C:/data/jarvis-rollback/<ts>` + daily off-box copy to `~/jarvis/backups/store-<date>` (rolling prune 14 on-box / 7 off-box). Tested end-to-end (off-box copy verified: 3,950 memories + chroma, 147 MB).
   - `scripts/jarvis-health-check.sh` — box `/api/health`+`/api/health/deep`, thin-client outbox backlog, box C: disk free; alerts to `logs/health-alerts.log`. Tested clean.
   - Registered as LaunchAgents `com.user.jarvis-backup` (daily 4:05) + `com.user.jarvis-health` (every 30 min); loaded, `launchctl list` healthy.
   - (Note: macOS `crontab` write hangs in non-tty shells — switched to LaunchAgents.)

## State at end
- Box: `jarvis server` @ `b42e659` on 8766, `/api/health` instant, **3,950 memories**, `/api/backfill` live; auto-start task intact; inbox backlog (~5,458 files) un-ingested (by design).
- Mac: thin client env persisted; local brain retired (only `jarvis-backup` + `jarvis-health` LaunchAgents remain); Mac store preserved for rollback.
- Git: `main` == `bot` == `b42e659` (pushed) + Round 7 docs/scripts commit pending. Tests 328 passing.
- Resilience: daily backup + 30-min health check scheduled.

## NOT done (intentionally deferred — see docs/STATUS.md)
- Inbox backlog ingestion (needs server-side integration, not ad-hoc parallel process).
- Verify triggers/digests/idle-maintenance all run inside `jarvis server` (Mayor does; sync-daemon machinery retired — confirm/relocate).
- Offload pilot with the box Cline CLI.
- Restore thin-client ambient collection (currently the Mac scan cron is off) + `JARVIS_TOKEN` for the exposed API.
- Hardened 3-2-1 (encrypted/strict-consistent) backup.

## To-dos for next session
- Read `docs/STATUS.md` + `AGENTS.md`; verify live box `/api/health/deep` == 3,950 and LaunchAgent-only jarvis on the Mac; then run the priority list in STATUS (inbox/server integration first).
