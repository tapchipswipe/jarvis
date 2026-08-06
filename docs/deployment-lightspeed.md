# Lightspeed Deployment — Jarvis Server (Round 6)

Status as of 2026-08-06: **Lightspeed runs the current `bot` code (`2d98a1d`) as a
single consolidated `jarvis server` on port 8766, reachable from the Mac over
Tailscale.** The old per-role runners (`dash_runner.py`, `run_mayor` x2 on 8767)
were consolidated into one process.

## Where things live on Lightspeed
- Code/repo: `C:\Users\despo\jarvis` (git branch `bot`)
- Canonical store: `C:\Users\despo\jarvis\data\` (`meta.db` + `chroma`)
- Raw device inbox (un-ingested): `C:\data\jarvis\inbox\<device>\`
- Server launcher: `C:\data\jarvis\server-start.bat`
- Server log: `C:\Users\despo\jarvis\logs\server.out.log`
- Auto-start: scheduled task `JarvisServer` (on logon). Reverse: `schtasks /delete /tn JarvisServer /f`
- Python: `C:\Users\despo\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe` (deps already present; no venv)

## Env that must be set for the box (imported at runtime)
- `OLLAMA_HOST=127.0.0.1` and `OLLAMA_PORT=11434` (set at user level **and** in the bat).
  Rationale: `mayor.py`/`agent.py`/`agents/base.py` default to `100.102.0.99` (the
  Lightspeed Tailscale IP) which is correct for the *Mac* but fails on the box itself
  (getaddrinfo). The box's Ollama is local.

## The bat (`C:\data\jarvis\server-start.bat`)
```bat
@echo off
set OLLAMA_HOST=127.0.0.1
set OLLAMA_PORT=11434
cd /d C:\Users\despo\jarvis
"<python.exe>" -u -m jarvis.cli server --port 8766 >> "C:\Users\despo\jarvis\logs\server.out.log" 2>&1
```

## Restart procedure
1. Stop current: `ssh despo@100.102.0.99 'powershell -Command "Get-Process | Where-Object {$_.ProcessName -match ''python'' and $_.Id -ne <other>} | Stop-Process -Force"'` (or kill the bat's cmd + python pid on 8766).
2. Start: `ssh despo@100.102.0.99 'cmd /c start "" C:\data\jarvis\server-start.bat'`  (or log off/on to trigger the task).
3. Verify from the Mac: `curl http://100.102.0.99:8766/api/health` and `/api/health/deep`.

## Verified end-to-end (this deploy)
- `jarvis server --check` -> `OK app=loaded memories=0 port=8766`
- Server on 8766; Mayor loaded `llama3.2:1b` + `qwen2.5:7b-instruct-q4_K_M` (no getaddrinfo).
- Mac over Tailscale: `GET /api/health` 200 (0.05s), `/api/health/deep` 200.
- `POST /api/remember` (from Mac) added a memory; `GET /api/search` found it back (vector).
- Backup of prior store: `C:\data\jarvis-backup-20260806-131813` (0.27 MB; the box store was empty).

## Not yet done (next round)
- One-time **backfill** of the Mac store (3949 memories) into the box (hash-verified), then
  cut the Mac over to `JARVIS_MODE=client` (remember->outbox->server; reads via server +
  rolling tail cache + offline banner). Keep the Mac store for ~1 week (rollback = config flag).
- Ingest the raw inbox backlog (`C:\data\jarvis\inbox\*`) into the box store.
- 3-2-1 backup + early-warning notifier.
