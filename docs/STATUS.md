# Jarvis — STATUS / Resume Snapshot

_Updated 2026-08-06. This is the canonical resume doc. Read AGENTS.md for how to use it._

## Topology (agreed)
**FULL-THIN.** Lightspeed = single source of truth + single writer + the LLM brain. Mac = thin terminal + collectors + disposable cache (outbox + rolling tail). See `docs/topology.md`.

## What is deployed / where / how to reach
- **Lightspeed server** (`jarvis server`, branch `bot` @ `2d98a1d`): Tailscale `100.102.0.99:8766`.
  - Auto-start: scheduled task `JarvisServer` (on logon). Launcher `C:\data\jarvis\server-start.bat`.
  - Env: `OLLAMA_HOST=127.0.0.1`, `OLLAMA_PORT=11434` (user env + in the bat). Canonical store at `C:\Users\despo\jarvis\data\` (currently `memories=0`).
  - Runbook: `docs/deployment-lightspeed.md`. Reverse/restart there.
- **Mac local services** (still running; to be retired at cutover): daemon `:8765` (com.user.jarvis), dashboard+Mayor `:8766` (com.jarvis.dashboard), watcher (com.user.jarvis-watcher), sync (com.user.jarvis-sync).
- **Remote agent (new):** headless Cline CLI installed on Lightspeed (`cline` v3.0.51). Drive from Mac: `ssh despo@100.102.0.99 'cline --cwd C:\Users\despo\jarvis --json "<task>"'`. Purpose: offload heavy/Windows-native maintenance off the (throttled) Mac onto the box. Caution: box RAM (16 GB) is tight with Ollama + server; delegate ONE task at a time.

## Branches / git
- Work on `bot`; `main` is ff-mirrored to `bot`; both pushed to `tapchipswipe/jarvis`. HEAD ~ `1ad8042` (STATUS/handoff commit pending).

## Known issues
- `/api/health` on the **Mac** local dashboard occasionally takes ~4-8 s on the **first** call after idle (GIL burst from ChromaDB Rust embed worker + Mayor reindex), then instant (0.001 s). NOT a crash; liveness is now async-pure (never blocks the loop). Avoid multi-worker uvicorn (would spawn duplicate Mayors).
- The box server's health is clean/instant (Round 6).

## Immediate next actions (priority order)
1. **Hash-verified backfill** of the Mac store (~3949 memories) into the Lightspeed server (`/api/remember` batches from `JARVIS_MODE=client`), verify counts+hashes, keep a rollback copy. Mac store is at the local default (`~/jarvis/data/meta.db`, ~3949 rows).
2. **Ingest the raw inbox backlog** on Lightspeed (`C:\data\jarvis\inbox\<device>\` — thousands of un-ingested raw files) into the box store.
3. **Cutover:** point the Mac at the server (`JARVIS_MODE=client`, `JARVIS_REMOTE`), wire reads through `jarvis/cache.py` (rolling tail + offline banner), then **retire** Mac-local store/daemon/push. Keep the Mac store ~1 week for rollback (config flag).
4. **Relocate** triggers/Mayor/digests/idle-maintenance to run in the Lightspeed server process (only if box RAM allows; else data-thin split).
5. **Resilience:** 3-2-1 encrypted backup + early-warning notifier (server health / outbox backlog / disk free).
6. **Offload pilot:** delegate ONE heavy task to the Lightspeed Cline CLI and confirm its JSON result returns to the Mac (validates the remote-agent loop).

## How to resume after a reboot
1. `cd /Users/lucasdespot/jarvis` and (re)activate the venv: `.venv/bin/python`.
2. Read this file + `logs/round6-handoff.md` + `docs/deployment-lightspeed.md` + `docs/topology.md`.
3. Verify live: `git status`; `launchctl list | grep jarvis` (Mac local services); `curl http://100.102.0.99:8766/api/health` (Lightspeed server).
4. Continue with the priority list above; keep `main`/`bot` in sync and tests green (`.venv/bin/python -m pytest -p no:cacheprovider`).
