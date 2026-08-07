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
- **DEPLOYED (2026-08-07 morning):** the box now runs `bot`@`d82145b`+ (`git pull` fast-forwarded 40 commits; `JarvisServer` restarted). The in-process inbox ingester is **live and draining** — watch `scripts/monitor-ingest.py` (or `jarvis ingest-status`) until `remaining` → 0; reconcile counts.
- **✅ Connectivity RESOLVED (2026-08-07):** the box had two `Python — Action: Block`
  Windows Firewall rules (Private profile, shared by both the LAN + Tailscale interfaces)
  that silently dropped inbound TCP 8766 to the python server (SYN timeout on both LAN and
  Tailscale, while ICMP/SSH worked). Deleted those block rules; **LAN + Tailscale `8766` both
  return 200 now** and `jarvis status`/`search` work over Tailscale. (Consider re-adding a
  narrower block for other ports if hardening needed.)
- `/api/health` on the box is instant (async-pure).
- Duplicate-content memories collapse on migration (content-hash dedupe).
- Token auth is config-gated: **`JARVIS_TOKEN` is now SET end-to-end** (see actions).

## Immediate next actions (priority order)
1. **Inbox drain DONE (2026-08-07):** the box's in-process ingester drained the full
   backlog to `remaining: 0, errors: 0`; brain went **3954 → 4119** (+165 net-new, ~2,565
   deduped by content-hash). Use `scripts/monitor-ingest.py` for future drains.
2. **Connectivity RESOLVED:** the box had two `Python — Block` firewall rules dropping
   inbound 8766 on the shared Private profile (LAN + Tailscale); removed → both paths 200.
3. **`JARVIS_TOKEN` ENABLED end-to-end (2026-08-07):** 48-hex token in
   `~/.config/jarvis/token` (+ `~/.zshrc` export) on the Mac and in the box's user env
   (`setx JARVIS_TOKEN`, inherited by the restarted server). Verified: no-token `/api/search`
   → 403, with-token → 200, `jarvis search`/`status` work over Tailscale. Health endpoints
   stay open for the notifier/monitor.
4. **Ambient collection REGISTERED (2026-08-07):** `com.user.jarvis-collect` LaunchAgent
   loaded (30-min `collect --flush`; validated end-to-end with the token — 45 memories
   pushed, 0 failed). Box brain now ~4,120+ and growing.
5. **Server-side digests/triggers BUILT (Round 9):** config-gated `start_trigger_loop()` in
   `jarvis/triggers.py` (per-tick Store open/close, no persistent second Chroma handle) wired
   into `run_dashboard`, deployed to the box (OFF by default). **Enable on the box with
   `JARVIS_TRIGGERS=1`** in `server-start.bat` + env, then restart — digests at 08:00/18:00
   (weekdays) + 30-min calendar poll. Keep model-tier discipline.
6. **Offload pilot DONE (2026-08-07):** `ssh … 'cline --cwd C:\Users\despo\jarvis --json "…"'`
   returned structured JSON (`done/completed, text: "pong"`) in 4.2s using a
   **Cline-hosted model (muse-spark-1.2)** — zero local box RAM. Mechanism validated.
7. **Hardened backup OPT-IN ready (2026-08-07):** `scripts/jarvis-backup.sh` now produces an
   **age-encrypted archive** when `age` + `~/.config/jarvis/backup-key.age.pub` exist
   (`age-keygen -o ~/.config/jarvis/backup-key.age` once). TrueNAS/3rd copy still optional.

## How to resume after a reboot
1. `cd /Users/lucasdespot/jarvis` (venv `.venv/bin/python`).
2. Read this file + `logs/round8-handoff.md` + `logs/round7-handoff.md` + `docs/runtime-audit.md` + `docs/deployment-lightspeed.md` + `docs/topology.md`.
3. Verify live: `git status`; `launchctl list | grep -E 'jarvis-(backup|health)'` (resilience); `curl http://100.102.0.99:8766/api/health` + `/api/health/deep` (box); `.venv/bin/python -m jarvis.cli doctor` (thin-client diagnostics).
4. Continue with the priority list above; keep `main`/`bot` in sync and tests green (`.venv/bin/python -m pytest -p no:cacheprovider`).

