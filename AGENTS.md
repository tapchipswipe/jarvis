# Jarvis — Agent Memory / Start-Here

You are Cline working on the **Jarvis** project (repo root = this directory).

> **Trigger — resume:** if the user says **"resume the jarvis project"** (or `continue` / `resume` /
> "pick up where we left off"), immediately do **all** of the RESUME steps in the next section —
> read the listed files in order, verify live state, then continue with the STATUS next-actions.
> Do not ask clarifying questions first.

## ⚡ RESUME / START HERE (a fresh session must read these first)
If this is a new/continued session, reconstruct context from these, in order:
0. **`docs/RESUME.md`** — self-contained pack: topology, config/secrets *locations*, how to
   **query jarvis memory** (`python -m jarvis.cli search "<topic>" --json`), backup/restore
   + age-key runbook, and current session state. Read this first on any machine.
1. **`docs/STATUS.md`** — the canonical snapshot: topology, what's deployed+where, branches,
   services, known issues, and the concrete **next actions**.
2. **`logs/round8-handoff.md`** (newest) then `logs/round7-handoff.md` — session narratives (what
   was done + decisions).
3. **`docs/runtime-audit.md`** — verified "what runs where" on the server.
4. **`docs/deployment-lightspeed.md`** — the Lightspeed server runbook (paths, restart, reverse).
5. **`docs/topology.md`** — the agreed architecture (FULL-THIN: Lightspeed = brain, Mac = thin client).
6. `docs/system-diagram.md` / `docs/architecture.md` — overall layout.
7. `UPGRADES.md` — feature history / `[planned]` vs `[done]`.

### Read the agent's memory (the jarvis brain)
Jarvis's own memories ARE the long-term context. Query them (see docs/RESUME.md):
- Box (local): `python -m jarvis.cli search "<topic>" --json`
- Mac (client): `.venv/bin/python -m jarvis.cli search "<topic>" --json`
Use `memories -n 30` / `timeline --days 7` for recent activity. Do this before proposing
anything that depends on what Jarvis knows.

To resume after a reboot (context was reset): read those files, check live state
(`git status`, `launchctl list | grep jarvis`, `curl <server>/api/health` + `/api/health/deep`), then continue.

## Current architecture (top level) — cutover DONE (Round 7)
- **Lightspeed (Dell G7, 16 GB, Tailscale 100.102.0.99) = single source of truth + single writer.**
  Runs `jarvis server` on `:8766` (branch `bot`) via scheduled task `JarvisServer`;
  canonical store at `C:\Users\despo\jarvis\data\` (~4,120 memories); Ollama local (`OLLAMA_HOST=127.0.0.1`).
  Env via `setx`: `JARVIS_TOKEN`, `JARVIS_TRIGGERS=1` (digests on), `OLLAMA_*`.
  See `docs/deployment-lightspeed.md`. `AGENTS.md` + `docs/RESUME.md` below are read by
  `cline` on Lightspeed too — so "resume the jarvis project" there has full context + memory.
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
- Thin client: `jarvis collect` (ambient files → outbox → server), `jarvis flush` (push outbox),
  `jarvis ingest-status` (box inbox-drain progress), `jarvis status` (live box view)
- Server: `jarvis server --check` (deploy validation), `jarvis server --port 8766` (run)
- Maintenance: `jarvis reindex`, `jarvis promote`, `jarvis backfill --dry-run`, `jarvis profiles`
- Tests: `.venv/bin/python -m pytest -p no:cacheprovider` (keep @ 350+; target green)
- Lint: `/opt/homebrew/bin/ruff check jarvis/ tests/`

## Rules
- Respect the thin-client rule: the server is the canonical writer; the Mac cache is disposable.
- Never send user data outside the machine during reasoning (Tailscale/SSH for inter-machine ops).
- Prefer Python stdlib + existing deps; keep files focused.
- Tiered memory: raw < session < reflection < arc. Corrections make new memories, never edit raw.
- Push/migrate data only with hash-verified backfill + a kept rollback copy.
- Keep `main`/`bot` in sync before finishing a session.

