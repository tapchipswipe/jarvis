# Jarvis — RESUME "the jarvis project"

Self-contained context pack. Say **"resume the jarvis project"** to an agent (Cline on
the Mac, or `cline` on Lightspeed) to bootstrap a session. **If on Lightspeed: `git pull`
first** so the working tree is at HEAD (the box is normally several commits behind).

## 1-line topology
**FULL-THIN.** Lightspeed (`100.102.0.99:8766`, Windows, branch `bot`) = single
**brain / source-of-truth / single writer**: canonical store + Chroma + the read/write
API + Mayor (idle reindex/promote) + the **inbox ingester** + opt-in **digests**.
The **Mac** is a thin client (`JARVIS_MODE=client`, disposable outbox + rolling tail).
See `docs/topology.md`, `docs/runtime-audit.md`.

## Where things live
- **Server (box):** `C:\Users\despo\jarvis` (git `bot`); canonical store at
  `C:\Users\despo\jarvis\data\` (`meta.db` + `chroma`). Launcher
  `C:\data\jarvis\server-start.bat` (Task Scheduler `JarvisServer`). Server log
  `...\logs\server.out.log`. Box user-env (set via `setx`): `JARVIS_TOKEN`,
  `JARVIS_TRIGGERS=1`, `OLLAMA_HOST=127.0.0.1`, `OLLAMA_PORT=11434`.
- **Mac client:** repo `~/jarvis` (venv `.venv/bin/python`); `~/.zshrc` exports
  `JARVIS_MODE=client`, `JARVIS_REMOTE=https://100.102.0.99:8766`, and
  `JARVIS_TOKEN=$(cat ~/.config/jarvis/token)`. **Secrets live in `~/.config/jarvis/`:**
  `token`, `backup-key.age` (private), `backup-key.age.pub` (recipient).

## Query jarvis MEMORY — this is the agent's memory / context
On the **box** (local mode — use the system python that has deps, run from repo root):
```
python -m jarvis.cli search "<topic>" --json
python -m jarvis.cli memories -n 30
python -m jarvis.cli timeline --days 7
cat C:\Users\despo\jarvis\data\meta.db     # SQLite directly if desired
```
On the **Mac** (thin client, needs the token in env as above):
```
.venv/bin/python -m jarvis.cli search "<topic>" --json
.venv/bin/python -m jarvis.cli memories -n 30 ; .venv/bin/python -m jarvis.cli status
```
Current brain: **~4,120 memories**, tiered raw → session → reflection → arc.
`jarvis search` is the fastest way to pull relevant memories for any task.

## Backups / restore / keys (hardened 3-2-1, Round 9)
- Encrypted off-box archives: `~/jarvis/backups/store-<date>.tar.gz.age` (produced daily
  by `scripts/jarvis-backup.sh` when `age` + `backup-key.age.pub` are present; warm on-box
  snapshots kept under `C:/data/jarvis-rollback/` with rolling prune).
- **Private recovery key:** `~/.config/jarvis/backup-key.age` — treat as the master secret
  needed to decrypt archives; **back it up to a safe off-machine vault.** Generate:
  `age-keygen -o ~/.config/jarvis/backup-key.age` (the `.pub` sibling is the recipient).
- **Restore drill (validated):**
  ```
  age -d -i ~/.config/jarvis/backup-key.age < ~/jarvis/backups/store-*.tar.gz.age \
    | tar -xzC /tmp/jarvis-restore
  # -> /tmp/jarvis-restore/store-<date>/meta.db + chroma/
  ```
  Point `JARVIS_DATA_DIR`/`JARVIS_CONFIG_DIR` at it (or copy into a fresh `data/`) to run
  against the restored snapshot.

## Session state — current (2026-08-07)
The whole "next-up" backlog is done:
- Inbox backlog **drained** (`remaining:0,errors:0`) — brain 3,954 → 4,119 (dedupe).
- **Connectivity fixed** (the box had `Python — Block` firewall rules dropping 8766; removed).
- **`JARVIS_TOKEN` enabled end-to-end** (box + Mac; no-token → 403, with-token → 200).
- **Ambient collection registered** (`com.user.jarvis-collect` LaunchAgent, 30-min flush).
- **Digests/triggers enabled** on the box (`JARVIS_TRIGGERS=1`): loop live (60s, 3 triggers).
- **`jarvis delegate` RETIRED** (offload tested; box `cline` needs Cline Credits, Pass didn't
  clear it → removed so Jarvis stays zero external dependencies).
- **Hardened backup active** (`age` + key; encrypted archives validated).
- Suite **398 passed / 1 skipped**; git `bot` == `main` == origin.

## Round 9b (2026-08-07) — hardening pass, all delivered
- **HTTPS enabled end-to-end**: self-signed cert, box serves `https://100.102.0.99:8766`;
  Mac client pins the cert fingerprint (`JARVIS_TLS_FINGERPRINT`); ops scripts use HTTPS
  (`-k`/encrypted transport). Plain HTTP is gone. Cert at `~/.config/jarvis/server-cert.pem`.
- **`jarvis backup [dst]`** — crash-consistent SQLite online-backup; `POST /api/admin/backup`
  (token-gated) runs in-process; `scripts/jarvis-backup.sh` uses it (`JARVIS_BACKUP_STRICT=1`
  pauses the scheduled task for a consistent HNSW snapshot, always restarts).
- **`jarvis ask "<q>"`** — one-shot answer ALWAYS grounded on the brain (local or box).
  `--session` threads; `--save` (default) writes Q&A back to memory; `--no-save` opts out;
  shows grounding sources + related entities. Uses the box's grounded `/api/query` (NOT the
  agentic chat), so it returns a clean answer — this fixed the "ask → To" fragment bug.
- **`jarvis console`** — interactive Iron-Man-style terminal (grounded Q&A, persistent thread,
  `/help /session /clear /save /digest /status /quit`).
- **`jarvis digest --now`** — generate a digest on demand (via `/api/digest`).
- **`jarvis delegate` RETIRED** — removed (the box's `cline` needs Cline Credits; the Pass
  didn't clear it for Muse Spark, and Ollama-on-same-box offload is pointless). Jarvis is
  **zero external dependencies**.
- **Ingest marker fix** — the ingester persists a drain-marker so a server restart idles
  instead of re-draining the whole inbox (which caused Chroma lock contention that stalled
  concurrent queries). `scripts/verify-restore.sh` smoke-tests the encrypted restore path.
- **Digest model guard** (`JARVIS_DIGEST_MODEL`), **ingester idle fast-path**, **per-user
  isolation 0700 chain**, **desktop+webhook alerting** (`JARVIS_ALERT_WEBHOOK`, rate-limited).
- Coverage **54%** (extract_entities 96%, mayor 57%); ruff 344 → ~220; suite **450 passed**.
- Full details: `UPGRADES.md` Round 9b + `logs/round9-handoff.md`. Box needs a
  `git pull` + `JarvisServer` restart to activate (all backward-compatible).

## Config / secrets — locations (not values)
`~/.config/jarvis/token`, `~/.config/jarvis/backup-key.age(.pub)`, and optionally
`~/.config/jarvis/server-fingerprint` on the Mac; box env via `setx JARVIS_TOKEN` /
`setx JARVIS_TRIGGERS`. Nothing secret is committed to the repo.

## For cline-on-Lightspeed specifically
You have **memory + context**: read this file, then query the brain with
`python -m jarvis.cli search "<topic>"` (local mode reads the canonical store directly).
Respect the single-writer rule: writes go through `/api/remember` (thin client) or the
server; never open a second Chroma handle on the live brain except as the server does.
