# Jarvis — STATUS / Resume Snapshot

_Updated 2026-08-06/07 (Round 8). This is the canonical resume doc. Read AGENTS.md for how to use it._

## Topology (agreed and NOW LIVE)
**FULL-THIN is executed.** Lightspeed = single source of truth + single writer + the LLM brain (holds the active memories). Mac = thin terminal + collectors + disposable cache (outbox + rolling tail). Mac-local brain retired. See `docs/topology.md`.

## What is deployed / where / how to reach
- **Lightspeed server** (`jarvis server`, branch `bot` @ `966d7b2`): Tailscale `100.102.0.99:8766`.
  - Auto-start: scheduled task `JarvisServer` (on logon). Launcher `C:\data\jarvis\server-start.bat`.
  - Env: `OLLAMA_HOST=127.0.0.1`, `OLLAMA_PORT=11434` (user env + in the bat). Canonical store at `C:\Users\despo\jarvis\data\` — active memory count via `/api/health/deep` (was 3,950, growing as the inbox ingester drains the backlog).
  - API: `/api/backfill` (field-preserving import), `/api/remember`/`/api/search`/`/api/chat` (token-guarded), plus dashboard/task/session routes. **Inbox backlog ingester is live in-process** (embed-only, throttled) — drains `C:\data\jarvis\inbox` (TXT/MD/CSV + v2 JSON sidecars), idempotent on content-hash.
  - Runbook: `docs/deployment-lightspeed.md`.
- **Mac = thin client.** `~/.zshrc` sets `JARVIS_MODE=client`, `JARVIS_REMOTE=http://100.102.0.99:8766`. CLI remember/search/export route to the box via `jarvis/cache.py` (outbox + rolling tail). Mac-local services (daemon 8765, dashboard 8766, watcher, sync) are **stopped + plists retired**; Mac store (`~/jarvis/data`, 3,951 rows) left intact for rollback.
- **Remote agent:** headless Cline on the box (`cline` v3.0.51): `ssh despo@100.102.0.99 'cline --cwd C:\Users\despo\jarvis --json "<task>"'`. Delegate one task at a time (box RAM-tight).

## Branches / git
- Work on `bot`; `main` is ff-mirrored to `bot`; both pushed to `tapchipswipe/jarvis`. HEAD `966d7b2` + Round 8 commits.

## Known issues
- **Inbox backlog ingestion is IN PROGRESS** on the box (no action needed; in-process + throttled + idempotent). Do *not* run a separate-process scanner that opens a second Chroma handle. Reconcile counts once drained.
- `/api/health` on the box is instant (async-pure); the old Mac-local health blip is moot (Mac dashboard retired).
- Duplicate-content memories collapse on migration (content-hash dedupe) — the Mac's one dup error-string is intentionally absent on the box.
- Token auth is config-gated: no `JARVIS_TOKEN` is set on the box yet, so the network API is effectively open (except loopback rules). See Round 8 notes on enabling it consistently.

## Immediate next actions (priority order)
1. **Restart the box server to activate the in-process inbox ingester** (git is current at `966d7b2`; the running process predates it — inbox still 5,458 files, no cursor). Restart task `JarvisServer` / re-run `server-start.bat`, then watch `/api/ingest/status` until `remaining` → 0; reconcile counts.
2. **Verify inbox-backlog reconcile** once the ingester drains `C:\data\jarvis\inbox` (~2,730 files): compare ingested count vs file count, confirm no orphans.
3. **Restore server-side digests/triggers**: `docs/runtime-audit.md` confirms Mayor idle-maintenance (reindex/promote) runs in the server, but `TriggerLoop` (morning/end-of-day digests) is NOT started by `run_dashboard` — add a config-gated `TriggerLoop` in the server (default OFF; model-tier discipline), OR run on a maintenance window.
4. **Enable `JARVIS_TOKEN`** end-to-end (set the same value in the box env + `server-start.bat` and in `~/.zshrc` on the Mac) now that token enforcement is consistent across all mutating API routes.
5. **Offload pilot:** delegate ONE heavy task to the Lightspeed Cline CLI and confirm its JSON result returns to the Mac.
6. **Hardened backup:** consider a strict consistent snapshot (stop server during copy) or TrueNAS age-encrypted archive for the 3-2-1 (current `jarvis-backup.sh` is a warm copy).

## How to resume after a reboot
1. `cd /Users/lucasdespot/jarvis` (venv `.venv/bin/python`).
2. Read this file + `logs/round8-handoff.md` + `logs/round7-handoff.md` + `docs/runtime-audit.md` + `docs/deployment-lightspeed.md` + `docs/topology.md`.
3. Verify live: `git status`; `launchctl list | grep -E 'jarvis-(backup|health)'` (resilience); `curl http://100.102.0.99:8766/api/health` + `/api/health/deep` (box). Confirmation that Mac-local brain is retired: `launchctl list | grep jarvis` should only show backup+health.
4. Continue with the priority list above; keep `main`/`bot` in sync and tests green (`.venv/bin/python -m pytest -p no:cacheprovider`).

