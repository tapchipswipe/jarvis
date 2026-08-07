# Round 9 handoff — Finish-the-backlog hardening pass (2026-08-07)

## Mission (from the user): "work thru it all, quality over quantity"
All 10 items from the improvement plan were delivered, each with tests, committed
on `bot` == `main`, suite green at **450 passed / 1 skipped**, coverage **54%**.
The box still runs the previous commit — **one `git pull` + a `JarvisServer` restart
activates all of this** (every change is backward-compatible; token stays enforced).

## Delivered this round (Round 9b, 2026-08-07)
1. **HTTPS server + pinned client (config-gated).** `jarvis server --tls-cert/--tls-key`
   (or `JARVIS_TLS_CERT/KEY`) → uvicorn HTTPS; `--gen-cert` mints a self-signed pair +
   prints the SHA256 fingerprint to pin. Client `remote.py` handles `https://` and pins the
   fingerprint (`JARVIS_TLS_FINGERPRINT` or `~/.config/jarvis/server-fingerprint`) — real
   MitM resistance without a CA. `.pem` secrets gitignored. Tested end-to-end against a real
   local TLS server (9 tests incl. token-over-TLS).
2. **Ingester idle fast-path.** `_inbox_marker` (count + max mtime_ns) lets a drained,
   unchanged inbox skip re-scans; `/api/ingest/status.idle`; `JARVIS_INBOX_IDLE` (default 30s).
3. **Crash-consistent backups.** `jarvis backup` snapshots SQLite files (meta.db,
   embed_cache.db, chroma.sqlite3) via the online-backup API — valid while live; Chroma HNSW
   bins copied best-effort. Token-gated in-process `POST /api/admin/backup` avoids invoking a
   second Python on the box (its Windows Store-Python alias refuses non-interactive exec).
   `scripts/jarvis-backup.sh` calls the endpoint; `JARVIS_BACKUP_STRICT=1` briefly pauses the
   `JarvisServer` scheduled task for a fully consistent HNSW snapshot and always restarts it.
4. **Digest model guard.** `JARVIS_DIGEST_MODEL` (default = chat model); large-tier → warning;
   `[ollama …]` error strings are never digested (fall back to chat model, then static). Fixed
   a real bug where a dead model's error text became the digest.
5. **`jarvis ask "<question>"`** — one-shot, always grounded on the brain; prints answer +
   grounding sources; `--json-out`; works in client mode (asks the box).
6. **`jarvis delegate "<task>"`** — offload to Lightspeed `cline` via SSH, returns JSON.
   ⚠️ The box's cline currently reports **Cline Credits balance $0** — mechanism validated,
   but real offloads need credits funded.
7. **Multi-user isolation hardening** — `ensure_private_dir` locks 0700 on every directory
   Jarvis creates (whole `~/jarvis/users/<user>` chain), not just the leaf.
8. **Coverage 50 → 54%** (extract_entities 0→96%, mayor 37→57%; 24 new tests) and
   **ruff debt 344 → ~220** (131 mechanical auto-fixes; the rest are intentional catch-all
   `except Exception` guards + a few style items).
9. **Alerting** — health-check now sends a macOS Notification Center banner + optional
   `JARVIS_ALERT_WEBHOOK`, rate-limited per alert-type (default 30 min).
10. **Token-over-HTTPS integration test** locked in the suite.

## To activate on the box (safe, backward-compatible)
```
ssh despo@100.102.0.99 'git -C C:/Users/despo/jarvis pull'     # fast-forwards ~11 commits
# restart the scheduled task (or re-run C:\data\jarvis\server-start.bat)
schtasks /end /tn JarvisServer ; schtasks /run /tn JarvisServer
curl http://100.102.0.99:8766/api/health                        # expect ok
curl -X POST http://100.102.0.99:8766/api/admin/backup -H "X-Jarvis-Token: $TOKEN" -d '{}'  # snapshot works
```
Optional TLS: on the box run `python -m jarvis.cli server --gen-cert`, then set
`JARVIS_TLS_CERT`/`JARVIS_TLS_KEY`, switch `JARVIS_REMOTE` to `https://` + set
`JARVIS_TLS_FINGERPRINT` on the Mac (see docs/STATUS.md item 8).

## Manual / external (still open)
- Fund **Cline Credits** on the box (or configure another provider) to use `jarvis delegate`.
- Tailscale admin-console ACL to scope `:8766` (optional; HTTPS + token make plain-HTTP moot).
- TrueNAS / 3rd backup copy (age-encrypted archive is the off-box copy).
- Ruff debt reduction beyond the mechanical pass (BLE001 etc.) if desired.

## Session state
- Repo: `bot` == `main` == origin (pushed). Suite 450 passed / 1 skipped.
- Mac: caffeinate up, LaunchAgents (collect/backup/health) loaded, token at
  `~/.config/jarvis/token` (0600), age key at `~/.config/jarvis/backup-key.age` (0600).
- Box: healthy, 4,120+ memories, digests enabled, ingester idle (drained).
