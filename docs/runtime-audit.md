# Jarvis — Server Runtime Audit (what actually runs where)

_Compiled 2026-08-07 (agent-only pass) as an authoritative handoff for the FULL-THIN
topology. "The server" = the Lightspeed `jarvis server` process on `100.102.0.99:8766`
(scheduled task `JarvisServer`, launcher `C:\data\jarvis\server-start.bat`). Must be
kept current with `docs/STATUS.md` and `docs/topology.md`.

## Inside the `jarvis server` process (once it runs `966d7b2`+)

| Component | Status | Where |
|---|---|---|
| FastAPI HTTP API (`/api/*`, dashboard) | RUNS | `dashboard.app` |
| Mayor loop (mode switching coding/memory) | RUNS | `run_dashboard → _start_mayor().run_loop()` |
| Mayor idle maintenance (reindex ~ every 5 min, promote ~ every 6 h, VRAM-guarded) | RUNS | `mayor.run_loop → _maybe_idle_maintenance()` (verified in `mayor.py`) |
| Inbox backlog ingester (embed-only, throttled, content-hash idempotent) | WIRED, **needs process restart** | `run_dashboard → start_background_ingester()` |
| Time/poll/event **trigger loop + digests** (morning/end-of-day briefings) | **NOT running** | `TriggerLoop` is **not** started by `run_dashboard`; it died with the retired daemon |

### Verified observations (2026-08-07)
- Box git working tree is `966d7b2` (== `bot` == `origin/bot`), but the *running*
  process predates the ingester commits. Evidence: inbox `C:/data/jarvis/inbox` still
  has **5,458 files** untouched, and no `inbox_ingest_cursor.txt` exists in
  `C:/Users/despo/jarvis/data`. **Restarting the scheduled task `JarvisServer` (or
  re-running `server-start.bat`) is all that's needed to activate it** — then watch
  `curl http://100.102.0.99:8766/api/ingest/status` as it drains.
- `/api/health/deep` reports 3,954 active memories; the count is stable because the
  backlog hasn't been drained yet.

### Digest/trigger gap (STATUS #2)
- `TriggerLoop` (`jarvis/triggers.py`) is a daemon thread that evaluates time/event/
  poll triggers and fires digests (`brief`/`notify`/`escalate`). It is constructed
  "from the daemon" — and the daemon is retired. The server only starts Mayor + the
  ingester, so **no morning/end-of-day digests are generated server-side today**.
- Mayor idle maintenance (reindex/promote) **is** running in the server — that part
  of STATUS #2 is already satisfied.
- To restore digests safely: start a `TriggerLoop` inside `run_dashboard`, gated by an
  env flag (default OFF) and keep the model-tier discipline (digests must not load a
  7B model while another is resident; reuse small models / idle window). This is
  deliberately left as an explicit opt-in given the RAM-tight box.

## On the Mac (thin client)
- Disposable cache (outbox + rolling tail), CLI (`remember/search/status/collect/...`).
- Resilience LaunchAgents only: `com.user.jarvis-backup` (daily 4:05) + 
  `com.user.jarvis-health` (every 30 min). No local brain.
- Offline-read: CLI `search` falls back to the cached tail with a
  "Offline (cached subset)" banner; real HTTP errors (e.g. 403) are surfaced as
  "Server error (code)".

## Security
- All mutating + sensitive-read API routes are guarded by `_host_ok` — enforced **only**
  when `JARVIS_TOKEN` is configured on the box. It is NOT configured yet, so the API is
  effectively open on Tailscale. Enable by setting the same `JARVIS_TOKEN` in the box
  env (and `server-start.bat`) and in `~/.zshrc` on the Mac.

## Operational cautions (FULL-THIN single-writer)
- **`jarvis graph build`** extracts entities and writes to the *local* store. On the Mac
  that is the disposable rollback copy, not the canonical box store — so it is misleading
  (and wasted) to run it on the Mac. Graph building is a single-writer (box) operation.
- **`jarvis reindex` / `promote`** already run automatically inside the box's `jarvis
  server` via Mayor's idle maintenance (`_maybe_idle_maintenance`, reindex ~5 min,
  promote ~6 h). No need to run them manually on the Mac.
- **`jarvis backfill`** is the one-time hash-verified migration path (Mac local store → box
  with fields preserved). Not a routine op.
- Thin-client writes always go through the disposable outbox → server; they never touch a
  local Chroma handle.
