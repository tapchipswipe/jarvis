# Jarvis

Local-first ambient-memory agent, running **FULL-THIN**: the Lightspeed server is the
single brain/source-of-truth + single writer; the Mac is a thin terminal + collectors
+ disposable cache.

## Topology (in one line)
`jarvis server` on **Lightspeed** (`100.102.0.99:8766`, branch `bot`) owns the store
(SQLite + Chroma) + the read/write/search/chat API + the Mayor loop. The **Mac** runs
`JARVIS_MODE=client` → `JARVIS_REMOTE` and only queues to a disposable outbox. See
`docs/topology.md` and `docs/runtime-audit.md`.

## Quick start (thin client — the Mac)
```bash
cd ~/jarvis            # repo root
.venv/bin/python -m jarvis.cli doctor          # local+box diagnostics
.venv/bin/python -m jarvis.cli status          # live box view + outbox backlog
.venv/bin/python -m jarvis.cli search "what did I do this week"
.venv/bin/python -m jarvis.cli remember "something important"
.venv/bin/python -m jarvis.cli collect --flush   # ambient files → outbox → box
.venv/bin/python -m jarvis.cli ingest-status     # box inbox-backlog drain progress
```

## Server (the box / Lightspeed)
```bash
jarvis server --check          # deploy validation (app loads, memory count)
jarvis server --port 8766      # run (FastAPI + Mayor + inbox ingester)
```
After a deploy (`git pull` on the box) **restart** this process so it picks up the new
inbox ingester + API endpoints. Full runbook: `docs/deployment-lightspeed.md`.

## Resume points
- `docs/STATUS.md` — canonical resume snapshot (topology, known issues, next actions).
- `logs/round8-handoff.md` — latest session narrative + morning TL;DR.
- `UPGRADES.md` — feature history (`[planned]` vs `[done]`).

## Tests / lint
```bash
.venv/bin/python -m pytest -p no:cacheprovider     # ~378 passing
/opt/homebrew/bin/ruff check jarvis/ tests/
```

## Notes
- The Mac never writes a local brain (single-writer rule); the local store at
  `~/jarvis/data` is a rollback copy only.
- Security: mutating + sensitive reads are token-guarded (enforced when `JARVIS_TOKEN`
  is configured) — enable it on both sides before exposing beyond Tailscale.

