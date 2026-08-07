# Jarvis — STATUS / Resume Snapshot

_Updated 2026-08-06/07 (Rounds 8–9). This is the canonical resume doc. Read AGENTS.md for how to use it._

## Topology (agreed and NOW LIVE)
**FULL-THIN is executed.** Lightspeed = single source of truth + single writer + the LLM brain (holds the active memories). Mac = thin terminal + collectors + disposable cache (outbox + rolling tail). Mac-local brain retired. See `docs/topology.md`.

## What is deployed / where / how to reach
- **Lightspeed server** (`jarvis server`, branch `bot` @ `f8cede5`): Tailscale `100.102.0.99:8766`.
  - Auto-start: scheduled task `JarvisServer` (on logon). Launcher `C:\data\jarvis\server-start.bat`.
  - Env: `OLLAMA_HOST=127.0.0.1`, `OLLAMA_PORT=11434` (user env + in the bat). Canonical store at `C:\Users\despo\jarvis\data\` — active memory count 3,954 (`/api/health/deep`), **stable until the running server is restarted onto the pushed commit**.
  - API: `/api/backfill`, `/api/remember`, `/api/search`, `/api/chat`, `/api/memories`, `/api/export`, `/api/ingest/status`, `/api/health(+deep)`, plus dashboard/task/session routes — mutating + sensitive reads token-guarded.
  - Runbook: `docs/deployment-lightspeed.md`. What-runs-where: `docs/runtime-audit.md`.
- **Mac = thin client.** `~/.zshrc` sets `JARVIS_MODE=client`, `JARVIS_REMOTE=http://100.102.0.99:8766`. CLI remember/search/status/export/memories/timeline/collect/flush/ingest-status/doctor route to the box (or queue to the disposable outbox). Mac-local services (daemon 8765, dashboard 8766, watcher, sync) are **stopped + plists retired**; Mac store (`~/jarvis/data`, 3,951 rows) left intact for rollback.
- **Remote agent:** headless Cline on the box (`cline` v3.0.51): `ssh despo@100.102.0.99 'cline --cwd C:\Users\despo\jarvis --json "<task>"'`. Delegate one task at a time (box RAM-tight).

## Branches / git
- Work on `bot`; `main` is ff-mirrored to `bot`; both pushed to `tapchipswipe/jarvis`. HEAD `f8cede5`. Tests: **374 passed, 1 skipped**, 2 warnings (3rd-party). All thin-client work is on `bot`==`main` (pushed).

## Known issues
- **Box-restart gating:** the running `jarvis server` predates the inbox ingester + `/api/memories` + `/api/ingest/status`. Inbox `C:\data\jarvis\inbox` is untouched (5,458 files) until the server is restarted onto the pushed commit. Until then the new endpoints return 404 and the CLI (memories/timeline/ingest-status) gracefully reports "not present / Could not reach the box". **One action gates everything: restart task `JarvisServer` (or re-run `server-start.bat`) after `git pull` (or just pull — it's already at HEAD).**
- `/api/health` on the box is instant (async-pure).
- Duplicate-content memories collapse on migration (content-hash dedupe) — the Mac's one dup error-string is intentionally absent on the box.
- Token auth is config-gated: no `JARVIS_TOKEN` is set on the box yet, so the network API is effectively open on Tailscale (loopback rules only). See Round 8 notes to enable consistently.

## Immediate next actions (priority order)
1. **Restart the box server** to load the pushed commit (inbox ingester + `/api/memories` + `/api/ingest/status`). On the box: `git pull` (already at HEAD → no-op) then restart task `JarvisServer` / re-run `C:\data\jarvis\server-start.bat`.
2. **Let the ingester drain** `C:\data\jarvis\inbox` and watch `/api/ingest/status` (via `jarvis ingest-status`) until `remaining` → 0; then reconcile counts (ingested == files, no orphans).
3. **Restore server-side digests/triggers**: `docs/runtime-audit.md` confirms Mayor idle-maintenance (reindex/promote) runs in the server, but `TriggerLoop` (morning/end-of-day digests) is NOT started by `run_dashboard` — add a config-gated `TriggerLoop` (default OFF; model-tier discipline), OR run on a maintenance window.
4. **Enable `JARVIS_TOKEN`** end-to-end (same value in the box env + `server-start.bat` and `~/.zshrc` on the Mac) now that all mutating/sensitive routes are guarded.
5. **Register thin-client ambient collection**: `launchctl load ~/Library/LaunchAgents/com.user.jarvis-collect.plist` (30-min `jarvis collect --flush`).
6. **Offload pilot:** delegate ONE heavy task to the Lightspeed Cline CLI and confirm its JSON result returns to the Mac.
7. **Hardened backup:** consider a strict consistent snapshot (stop server during copy) or TrueNAS age-encrypted archive for the 3-2-1 (current `jarvis-backup.sh` is a warm copy).

## How to resume after a reboot
1. `cd /Users/lucasdespot/jarvis` (venv `.venv/bin/python`).
2. Read this file + `logs/round8-handoff.md` + `logs/round7-handoff.md` + `docs/runtime-audit.md` + `docs/deployment-lightspeed.md` + `docs/topology.md`.
3. Verify live: `git status`; `launchctl list | grep -E 'jarvis-(backup|health)'` (resilience); `curl http://100.102.0.99:8766/api/health` + `/api/health/deep` (box); `.venv/bin/python -m jarvis.cli doctor` (thin-client diagnostics).
4. Continue with the priority list above; keep `main`/`bot` in sync and tests green (`.venv/bin/python -m pytest -p no:cacheprovider`).

