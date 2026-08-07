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
- Token auth is config-gated: no `JARVIS_TOKEN` is set yet, so the API is open on the network.

## Immediate next actions (priority order)
1. **Inbox drain DONE (2026-08-07):** the box's in-process ingester drained the full
   backlog to `remaining: 0, errors: 0`; brain went **3954 → 4119** (+165 net-new, ~2,565
   deduped by content-hash). Use `scripts/monitor-ingest.py` for future drains.
2. **Resolve the Tailscale-8766 anomaly** (or adopt the LAN `JARVIS_REMOTE` explicitly).
3. **Enable `JARVIS_TOKEN`** end-to-end (same value in the box env + `server-start.bat` and `~/.zshrc` on the Mac) now that all mutating/sensitive routes are guarded.
4. **Register thin-client ambient collection**: `launchctl load ~/Library/LaunchAgents/com.user.jarvis-collect.plist` (30-min `collect --flush`).
5. **Server-side digests/triggers BUILT (Round 9):** config-gated `start_trigger_loop()` in
   `jarvis/triggers.py` (per-tick Store open/close, no persistent second Chroma handle) wired
   into `run_dashboard`. **Enable on the box by setting `JARVIS_TRIGGERS=1`** in
   `server-start.bat` + env, then restart — digests fire at 08:00/18:00 (weekdays) + a
   30-min calendar notify poll. Keep model-tier discipline (small models / not while a 7B
   is resident).
6. **Offload pilot:** delegate ONE heavy task to the Lightspeed Cline CLI and confirm the JSON round-trip.
7. **Hardened backup:** strict-consistent snapshot or TrueNAS/age-encrypted archive (current backup is a warm copy).

## How to resume after a reboot
1. `cd /Users/lucasdespot/jarvis` (venv `.venv/bin/python`).
2. Read this file + `logs/round8-handoff.md` + `logs/round7-handoff.md` + `docs/runtime-audit.md` + `docs/deployment-lightspeed.md` + `docs/topology.md`.
3. Verify live: `git status`; `launchctl list | grep -E 'jarvis-(backup|health)'` (resilience); `curl http://100.102.0.99:8766/api/health` + `/api/health/deep` (box); `.venv/bin/python -m jarvis.cli doctor` (thin-client diagnostics).
4. Continue with the priority list above; keep `main`/`bot` in sync and tests green (`.venv/bin/python -m pytest -p no:cacheprovider`).

