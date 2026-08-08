# Jarvis — STATUS / Resume Snapshot

_Updated 2026-08-07 (Rounds 8–10). This is the canonical resume doc. Read AGENTS.md for how to use it._

## Topology (agreed and NOW LIVE)
**FULL-THIN is executed.** Lightspeed = single source of truth + single writer + the LLM brain (holds the active memories). Mac = thin terminal + collectors + disposable cache (outbox + rolling tail). Mac-local brain retired. See `docs/topology.md`.

## What is deployed / where / how to reach
- **Lightspeed server** (`jarvis server`, branch `bot` @ `ab465f2`): Tailscale `100.102.0.99:8766`.
  - Auto-start: scheduled task `JarvisServer` (on logon). Launcher `C:\data\jarvis\server-start.bat`.
  - Env: `OLLAMA_HOST=127.0.0.1`, `OLLAMA_PORT=11434` (user env + in the bat). Canonical store at `C:\Users\despo\jarvis\data\` — active memory count 3,954 (`/api/health/deep`), **stable until the running server is restarted onto the pushed commit**.
  - API: `/api/backfill`, `/api/remember`, `/api/search`, `/api/query`, `/api/chat`, `/api/sessions`, `/api/memories`, `/api/export`, `/api/ingest/status`, `/api/digest`, `/api/admin/backup`, `/api/health(+deep)`, plus dashboard/task/entity routes — mutating + sensitive reads token-guarded.
  - Runbook: `docs/deployment-lightspeed.md`. What-runs-where: `docs/runtime-audit.md`.
- **Mac = thin client.** `~/.zshrc` sets `JARVIS_MODE=client`, `JARVIS_REMOTE=https://100.102.0.99:8766`. CLI remember/search/status/export/memories/timeline/collect/flush/ingest-status/doctor route to the box (or queue to the disposable outbox). Mac-local services (daemon 8765, dashboard 8766, watcher, sync) are **stopped + plists retired**; Mac store (`~/jarvis/data`, 3,951 rows) left intact for rollback.
- **Remote agent:** headless Cline on the box (`cline` v3.0.51): `ssh despo@100.102.0.99 'cline --cwd C:\Users\despo\jarvis --json "<task>"'`. Delegate one task at a time (box RAM-tight).

## Branches / git
- Work on `bot`; `main` is ff-mirrored to `bot`; both pushed to `tapchipswipe/jarvis`. HEAD `ab465f2`. Tests: **583 passed, 1 skipped** (hermetic suite — autouse fixture clears `JARVIS_*` env so tests never touch the live brain). All thin-client work is on `bot`==`main` (pushed).

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
8. **HTTPS live + pinned client (2026-08-07):** self-signed cert at
   `C:\Users\despo\jarvis\server-cert.pem` (key `.pem` private); box serves `https://100.102.0.99:8766`.
   Mac `~/.zshrc`: `JARVIS_REMOTE=https://…`, `JARVIS_TLS_FINGERPRINT=` the cert SHA256
   (client pins it); copy of the cert at `~/.config/jarvis/server-cert.pem`. Ops scripts
   (`health-check`, `backup`, `monitor-ingest`) use HTTPS (`-k`/unverified — encrypted transport);
   the CLI client uses real fingerprint pinning (MitM-resistant). Plain HTTP is gone.
9. **Ingester idle fast-path** (drained inbox stops re-scanning; `/api/ingest/status.idle`).
10. **Crash-consistent backups:** `jarvis backup` + token-gated `POST /api/admin/backup`
    (SQLite online-backup); `scripts/jarvis-backup.sh` uses it; `JARVIS_BACKUP_STRICT=1`
    pauses/restarts the scheduled task for a consistent HNSW snapshot.
11. **`jarvis ask "<q>"`** (always grounded), **`jarvis backup [dst]`**, digest model guard
    (`JARVIS_DIGEST_MODEL`), per-user isolation chain locked 0700, desktop+webhook alerting
    (rate-limited), coverage 54%, ruff debt 344→~220.
12. **`jarvis delegate` RETIRED (2026-08-07):** removed — the box's `cline` hosted provider
    requires **Cline Credits** (`$0` balance; the Cline Pass doesn't clear it for Muse Spark),
    and delegate-on-Ollama is pointless (same box). Jarvis is now **zero external
    dependencies**; tasks go through the Mayor's local agents. (Note: offload could come back
    later if credits/Pass-covered model are supplied — a documented future option.)

## Round 10 (2026-08-07, autonomous team) — correctness + Iron-Man polish
13. **Hermetic test suite** (`5c11bc6`): an autouse fixture clears `JARVIS_*` env vars so
    the whole suite runs against tmp dirs / mocks and never opens a second Chroma handle on
    the live brain. Suite grew to **583 passed / 1 skipped** (was 450 Round 9b; +133 tests).
14. **Chroma pruning** (`dda46ad`): when memories expire or are superseded, their vectors are
    now deleted from Chroma (previously orphaned vectors lingered and could surface stale hits).
15. **Dedup fixes** (`1280f05`, `98493c6`, `e5033f3`): stable content fingerprints for bundle
    cids, consolidation clusters, and save_session/correct/upgrade so distinct memories never
    collide and identical ones reliably collapse.
16. **Graph fixes** (`d4cac5a`, `d616194`, `83095bd`): reversed `co_participant` edges collapse
    to one canonical row per unordered pair; junk "Organization reference" entity no longer
    pollutes the graph; `get_related` honors depth and stops noisy person-domain edges.
17. **Thin-client chat** (`93554c6`, `e3b1bf3`): `jarvis chat`/console now route through the
    box in client mode (was memory-less local); `run_turn` NameError in the local path fixed.
18. **Iron-Man console/chat polish** (`c030656`, `f7d5378`, `4b03dda`, `ef16e9c`, `5a93e7e`,
    `da2ca2a`, `a4ee3a8`): configurable `JARVIS_CHAT_MODEL` (box set to `llama3.2:1b` for snappy
    reply), tiered model auto-routing by complexity (fast/medium/big) + `/model` override,
    sources hidden by default for natural chat (`/sources` toggle, auto-shown on recall),
    context-aware greeting banner, and proactive follow-up suggestions from grounded entities.
19. **Robustness pass** (many `fix(…)`): consolidate/mayor no longer open a second Store/Chroma
    handle (`5ca30ad`, `4329bc7`); scan file-size cap so huge files don't OOM/bloat the outbox
    (`3df7021`); per-file failures don't abort a scan (`0955563`); failed embeddings aren't
    committed as zero-vectors (`bf4f290`); ingest cursor doesn't advance past failed files
    (`ed7c4bf`); stable memory ids from record timestamps (`59c308c`); Chrome/Safari window
    epoch fix (`7974859`); OCR runs independently of exiftool (`c32bca8`); notify/trigger
    fixes (template vars, single popup, real delivery channel) (`c9a8a52`, `34a9b73`,
    `f73cdee`); `/api/chat` returns 400 on malformed history instead of silently dropping it
    (`ab465f2`).

## How to resume after a reboot
1. `cd /Users/lucasdespot/jarvis` (venv `.venv/bin/python`).
2. Read this file + `logs/round8-handoff.md` + `logs/round7-handoff.md` + `docs/runtime-audit.md` + `docs/deployment-lightspeed.md` + `docs/topology.md`.
3. Verify live: `git status`; `launchctl list | grep -E 'jarvis-(backup|health)'` (resilience); `curl https://100.102.0.99:8766/api/health` + `/api/health/deep` (box); `.venv/bin/python -m jarvis.cli doctor` (thin-client diagnostics).
4. Continue with the priority list above; keep `main`/`bot` in sync and tests green (`.venv/bin/python -m pytest -p no:cacheprovider`).

