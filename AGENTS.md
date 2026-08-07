# Jarvis — Agent Memory / Start-Here

You are Cline working on the **Jarvis** project (repo root = this directory).

> **Trigger — resume:** if the user says **"resume the jarvis project"** (or `continue` / `resume` /
> "pick up where we left off"), immediately do **all** of the RESUME steps in the next section —
> read the listed files in order, verify live state, then continue with the STATUS next-actions.
> Do not ask clarifying questions first.

## ⚡ RESUME / START HERE (a fresh session must read these first)
If this is a new/continued session, reconstruct context from these, in order:
1. **`docs/STATUS.md`** — the canonical snapshot: topology, what's deployed+where, branches,
   services, known issues, and the concrete **next actions**.
2. **`logs/round7-handoff.md`** — the newest session narrative (what was done last + decisions).
3. **`docs/deployment-lightspeed.md`** — the Lightspeed server runbook (paths, restart, reverse).
4. **`docs/topology.md`** — the agreed architecture (FULL-THIN: Lightspeed = brain, Mac = thin client).
5. `docs/system-diagram.md` / `docs/architecture.md` — overall layout.
6. `UPGRADES.md` — feature history / `[planned]` vs `[done]`.

To resume after a reboot (context was reset): read those files, check live state
(`git status`, `launchctl list | grep jarvis`, `curl <server>/api/health`), then continue.

## Current architecture (top level) — cutover DONE (Round 7)
- **Lightspeed (Dell G7, 16 GB, Tailscale 100.102.0.99) = single source of truth + single writer.**
  Runs `jarvis server` on `:8766` (branch `bot`) via scheduled task `JarvisServer`;
  canonical store at `C:\Users\despo\jarvis\data\` (3,950 memories); Ollama local (`OLLAMA_HOST=127.0.0.1`).
  See `docs/deployment-lightspeed.md`.
- **Mac = thin client (cut over).** CLI read/write the server over Tailscale via `jarvis/cache.py`
  (disposable outbox + rolling tail). Mac-local daemon/dashboard/watcher/sync are **retired** (plists in
  `~/jarvis/rollback-launchagents-*/`); the local `~/jarvis/data` store is kept only as a rollback copy.
  `JARVIS_MODE=client` + `JARVIS_REMOTE=http://100.102.0.99:8766` live in `~/.zshrc`.
- **Remote agent:** a headless Cline CLI is installed on Lightspeed (`cline` v3.0.51) and can be
  driven from the Mac via `ssh despo@100.102.0.99 'cline --cwd <repo> --json "<task>"'` to offload
  maintenance/Windows-native work off the Mac. Delegate ONE task at a time (box RAM-tight).

## git
- Default working branch: `bot`. `main` is fast-forward-mirrored to `bot` and both pushed to origin
  (`tapchipswipe/jarvis`). Keep them in sync: `git checkout main && git merge --ff-only bot && git push origin main && git checkout bot && git push origin bot`.

## Commands (CLI at `python -m jarvis.cli ...`; use the venv at `.venv/bin/python`)
- Chat/search/remember/status/sessions/graph via `jarvis.cli`
- Server: `jarvis server --check` (deploy validation), `jarvis server --port 8766` (run)
- Maintenance: `jarvis reindex`, `jarvis promote`, `jarvis profiles`
- Tests: `.venv/bin/python -m pytest -p no:cacheprovider` (keep @ 300+; target green)
- Lint: `/opt/homebrew/bin/ruff check jarvis/ tests/`

## Rules
- Respect the thin-client rule: the server is the canonical writer; the Mac cache is disposable.
- Never send user data outside the machine during reasoning (Tailscale/SSH for inter-machine ops).
- Prefer Python stdlib + existing deps; keep files focused.
- Tiered memory: raw < session < reflection < arc. Corrections make new memories, never edit raw.
- Push/migrate data only with hash-verified backfill + a kept rollback copy.
- Keep `main`/`bot` in sync before finishing a session.

