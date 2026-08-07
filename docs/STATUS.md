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
5. **Server-side digests/triggers ENABLED on the box (2026-08-07):** `start_trigger_loop()` in
   `jarvis/triggers.py` (per-tick Store open/close, no persistent second Chroma handle) is
   live — set `JARVIS_TRIGGERS=1` in the box user env + restarted; log confirms
   `Trigger loop started in-process (interval=60s, 3 trigger(s))` and the 30-min
   `upcoming-events-poll` fired a notify on startup. Digests at 08:00/18:00 (weekdays) +
   calendar poll. Keep model-tier discipline (small models / not while a 7B is resident).
6. **Offload pilot DONE (2026-08-07):** `ssh … 'cline --cwd C:\Users\despo\jarvis --json "…"'`
   returned structured JSON (`done/completed, text: "pong"`) in 4.2s using a
   **Cline-hosted model (muse-spark-1.2)** — zero local box RAM. Mechanism validated.
7. **Hardened backup ACTIVE (2026-08-07):** installed `age` (v1.3.1) and generated the key
   at `~/.config/jarvis/backup-key.age` (+ `.pub` recipient); `jarvis-backup.sh` produced a
   **validated age-encrypted archive** (`~/jarvis/backups/store-<date>.tar.gz.age`, decrypt +
   tar-list OK). TrueNAS / 3rd copy still optional.

## Round 9b hardening pass (2026-08-07) — all delivered, box NOT yet restarted onto it
8. **HTTPS server (config-gated) + pinned client:** `jarvis server --gen-cert` → self-signed
   pair + SHA256 fingerprint; serve with `--tls-cert/--tls-key` (or `JARVIS_TLS_CERT/KEY`).
   Client `remote.py` pins the fingerprint (`JARVIS_TLS_FINGERPRINT`) over `https://` →
   MitM-resistant without a CA. `.pem` secrets are gitignored. (Not enabled on the box yet —
   plain HTTP + token remains until the next deploy.)
9. **Ingester idle fast-path** (drained inbox stops re-scanning; `/api/ingest/status.idle`).
10. **Crash-consistent backups:** `jarvis backup` + token-gated `POST /api/admin/backup`
    (SQLite online-backup); `scripts/jarvis-backup.sh` uses it; `JARVIS_BACKUP_STRICT=1`
    pauses/restarts the scheduled task for a consistent HNSW snapshot.
11. **`jarvis ask "<q>"`** (always grounded), **`jarvis delegate "<task>"`** (offload → cline
    JSON), **`jarvis backup [dst]`**, digest model guard (`JARVIS_DIGEST_MODEL`), per-user
    isolation chain locked 0700, desktop+webhook alerting (rate-limited), coverage 54%,
    ruff debt 344→~220.
12. **`jarvis delegate` caveat:** the box's cline reports **Cline Credits balance $0** — the
    SSH/cline/JSON mechanism works, but offload needs credits funded (or another provider).

## How to resume after a reboot
1. `cd /Users/lucasdespot/jarvis` (venv `.venv/bin/python`).
2. Read this file + `logs/round8-handoff.md` + `logs/round7-handoff.md` + `docs/runtime-audit.md` + `docs/deployment-lightspeed.md` + `docs/topology.md`.
3. Verify live: `git status`; `launchctl list | grep -E 'jarvis-(backup|health)'` (resilience); `curl http://100.102.0.99:8766/api/health` + `/api/health/deep` (box); `.venv/bin/python -m jarvis.cli doctor` (thin-client diagnostics).
4. Continue with the priority list above; keep `main`/`bot` in sync and tests green (`.venv/bin/python -m pytest -p no:cacheprovider`).

