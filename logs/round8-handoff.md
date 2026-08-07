# Round 8 — Night-Autonomous pass (2026-08-06→07, agent-only)

This session runs while the user is away. All work is agent-driven, committed to
`bot`, kept in sync with `main`, and the pytest suite is kept green after every change.

## Live-state verification (start of session)
- Box (Lightspeed) is ALREADY on `966d7b2` == local `bot` == `origin/bot` (all synced).
- The inbox backlog ingester (Round 7 step 1) is ALREADY deployed and running in the
  `jarvis server` process: `/api/health/deep` memories climbed 3950 → 3954 during the
  first ~30 min of observation. `com.user.jarvis-backup` + `com.user.jarvis-health`
  are the only Mac LaunchAgents (thin-client confirmed).
- **Gap found:** `STATUS.md` / `UPGRADES.md` / docs are stale — they still describe the
  inbox backlog as un-ingested, contradicting the deployed ingester.

## Rounds planned
1. Fix the failing CLI test (mode isolation for the Round 7 thin-client cutover) +
   add remote-path coverage.
2. Sync STATUS.md / UPGRADES.md / handoff docs with the real Round 7 step 1/2 state.
3. Server token-enforcement consistency (`_host_ok` on currently-open mutating routes).
4. Restore thin-client ambient collection (Mac `JARVIS_MODE=client` collector job).
5. Hygiene/polish (datetime.utcnow deprecations, offline-read banner, etc.).

## Progress log
- [x] **Round 1 (2a6dca8):** Fixed the CLI remember regression test (deterministic
  `remote.is_remote()` pinning for the local vs thin-client paths) + new remote path
  test. Suite green.
- [x] **Round 2 (73e0a1f):** Synced `docs/STATUS.md` + `UPGRADES.md` with the real
  state (inbox ingester deployed + running at `966d7b2`); resequenced next actions.
- [x] **Round 3 (05d4377):** Added `_host_ok` token guards to the previously-open
  mutating/sensitive routes — `/api/idea`, `/api/tasks/approve`, `/api/tasks/reject`,
  `/api/sessions`, and `/api/export`. `/api/health` stays open (Mac health checker).
  2 new server tests. Suite 334 passed, 1 skipped.
- [x] **Round 4 (561345e):** Restored thin-client ambient collection —
  `jarvis/collectors/thin.py` (files→outbox→server, content-hash + fingerprint
  idempotent, bounded) + `jarvis collect` CLI (refuses outside client mode) + 5
  hermetic tests + opt-in `scripts/jarvis-collect.sh` + LaunchAgent plist (NOT
  auto-started). Live isolated smoke test OK. Suite 339 passed, 1 skipped.
- [x] **Round 5 (708ee65):** `/api/ingest/status` endpoint + thread-safe ingester
  progress registry so the box inbox drain is observable once the server restarts
  (verified the running box server predates the ingester — inbox still 5,458 files,
  no cursor). Suite 342 passed, 1 skipped.
- [x] **Round 6 (6333664):** CLI search offline-read hardening — a real HTTP
  error (e.g. 403 from the new token guards) is now reported as "Server error (code)"
  instead of being mislabelled "Offline (cached subset)". 2 new tests. Suite 344
  passed, 1 skipped. Confirmed the offline fallback itself was already implemented.
- [x] **Round 7 (9264f24):** `jarvis status` is now remote-aware — in client
  mode it reports the live box (`/api/health/deep` memories/mode/uptime) + local
  outbox backlog instead of the stale local (rollback) store. 2 new tests; ruff clean.
  Suite 346 passed, 1 skipped.
- [x] **Round 8 (31bc485):** Reconciled stale 2025 `[planned]` UPGRADES entries
  (reindex/promote, delivered 2026-08-05) → marked done.
- [x] **Round 9 (9041d97):** `docs/runtime-audit.md` — verified what runs in the
  box server: API, Mayor loop, Mayor idle-maintenance (reindex/promote) YES;
  inbox ingester WIRED-BUT-NEEDS-RESTART (5,458 files, no cursor);
  triggers/digests (`TriggerLoop`) NOT running (died with the daemon). STATUS
  next-actions reordered (restart box first). Docs only; no code change.
- [x] **Round 10 (7003146):** modernized all `datetime.utcnow()` uses (22 source
  files, ~67 call sites) to `datetime.now(timezone.utc).replace(tzinfo=None)` —
  string-identical naive-UTC semantics, so no behavior change. Warnings dropped
  370 → 2 (both from 3rd-party deps). Suite 346 passed, 1 skipped.
- [x] **Round 11 (f3d6a1d):** `jarvis ingest-status` CLI + `remote.ingest_status()`
  so the box inbox-drain progress is observable from the Mac without curl. 2 new
  tests; ruff-clean in new regions. Also removed a duplicated `__main__` guard.
  Suite 348 passed, 1 skipped.
- [x] **Round 12 (c978ebe):** health-check now also reports `box ingest:` progress
  (read-only; n/a on the pre-restart server). Verified syntax + live run.
- [x] **Round 13 (pending commit):** test-coverage round — new `tests/test_remote.py`
  (transport wrappers + token header, no network), `tests/test_decision_log.py`,
  and server.py shim tests (app identity + `run` delegation). Added coverage for the
  client API surface. Suite 358 passed, 1 skipped.


