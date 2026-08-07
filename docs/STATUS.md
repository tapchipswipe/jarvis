# Jarvis — STATUS / Resume Snapshot

_Updated 2026-08-06 (Round 7). This is the canonical resume doc. Read AGENTS.md for how to use it._

## Topology (agreed and NOW LIVE)
**FULL-THIN is executed.** Lightspeed = single source of truth + single writer + the LLM brain (holds all 3,950 memories). Mac = thin terminal + collectors + disposable cache (outbox + rolling tail). Mac-local brain retired. See `docs/topology.md`.

## What is deployed / where / how to reach
- **Lightspeed server** (`jarvis server`, branch `bot` @ `b42e659`): Tailscale `100.102.0.99:8766`.
  - Auto-start: scheduled task `JarvisServer` (on logon). Launcher `C:\data\jarvis\server-start.bat`.
  - Env: `OLLAMA_HOST=127.0.0.1`, `OLLAMA_PORT=11434` (user env + in the bat). Canonical store at `C:\Users\despo\jarvis\data\` — **3,950 active memories** (`/api/health/deep`).
  - New: `/api/backfill` (field-preserving import) live.
  - Runbook: `docs/deployment-lightspeed.md`.
- **Mac = thin client.** `~/.zshrc` sets `JARVIS_MODE=client`, `JARVIS_REMOTE=http://100.102.0.99:8766`. CLI remember/search/export route to the box via `jarvis/cache.py` (outbox + rolling tail). Mac-local services (daemon 8765, dashboard 8766, watcher, sync) are **stopped + plists retired**; Mac store (`~/jarvis/data`, 3,951 rows) left intact for rollback.
- **Remote agent:** headless Cline on the box (`cline` v3.0.51): `ssh despo@100.102.0.99 'cline --cwd C:\Users\despo\jarvis --json "<task>"'`. Delegate one task at a time (box RAM-tight).

## Branches / git
- Work on `bot`; `main` is ff-mirrored to `bot`; both pushed to `tapchipswipe/jarvis`. HEAD `b42e659` (+ Round 7 docs/scripts commit).

## Known issues
- Box inbox backlog (`C:\data\jarvis\inbox`, ~2,730 txt + 2,728 json) is **not yet ingested**. Do NOT run a separate-process ingester (would open a second Chroma handle on the live brain → lock risk). Integrate into the server process (plan step 4) or run in a maintenance window.
- `/api/health` on the box is instant (async-pure); the old Mac-local health blip is moot (Mac dashboard retired).
- Duplicate-content memories collapse on migration (content-hash dedupe) — the Mac's one dup error-string is intentionally absent on the box.

## Immediate next actions (priority order)
1. **Ingest the box inbox backlog into the `jarvis server` process** (server-side integration, so no second Chroma handle): ~2,730 inbox files → route/tags via sidecars, embed + store; then verify counts + reconcile.
2. **Relocate triggers/Mayor/digests/idle-maintenance INTO the `jarvis server` process** (currently Mayor runs in the server; verify triggers/digests are too — the standalone sync daemon was retired). Only if box RAM allows (~3 GB floor guard).
3. **Offload pilot:** delegate ONE heavy task to the Lightspeed Cline CLI and confirm its JSON result returns to the Mac.
4. **Restore ambient collection on the thin client** (currently Mac scan/push cron is retired): add a `JARVIS_MODE=client` collector job that feeds the outbox → server. Consider an HTTPS token (`JARVIS_TOKEN`) now that the write/read API is exposed on the network.
5. **Hardened backup:** consider a strict consistent snapshot (stop server during copy) or TrueNAS age-encrypted archive for the 3-2-1 (current `jarvis-backup.sh` is a warm copy).

## How to resume after a reboot
1. `cd /Users/lucasdespot/jarvis` (venv `.venv/bin/python`).
2. Read this file + `logs/round7-handoff.md` + `docs/deployment-lightspeed.md` + `docs/topology.md`.
3. Verify live: `git status`; `launchctl list | grep -E 'jarvis-(backup|health)'` (resilience); `curl http://100.102.0.99:8766/api/health` + `/api/health/deep` (box). Confirmation that Mac-local brain is retired: `launchctl list | grep jarvis` should only show backup+health.
4. Continue with the priority list above; keep `main`/`bot` in sync and tests green (`.venv/bin/python -m pytest -p no:cacheprovider`).
