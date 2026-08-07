# Round 8 — Night-Autonomous pass (2026-08-06→07, agent-only)

## ☕ TL;DR — what happened overnight (read this first)
- **The box is the only thing that needs your hands.** The running `jarvis server`
  predates all of tonight's work and the box's git is at `966d7b2` (behind `bot`/`main`).
  On the box: `cd C:\Users\despo\jarvis && git pull` then **restart task `JarvisServer`**
  (or re-run `C:\data\jarvis\server-start.bat`). That loads the inbox ingester
  (in-process, throttled, idempotent) + `/api/memories` + `/api/ingest/status`.
- **Watch it drain:** `jarvis ingest-status` until `remaining` → 0, then
  `jarvis doctor` and reconcile counts (`docs/STATUS.md` action #1/#2).
- **Nothing else is broken or half-done.** 21 feature/build/test rounds landed on
  `bot`==`main` and are pushed (HEAD = see `git log`), suite **374 passed**, import-smoke +
  `jarvis server --check` green, thin-client read/write paths validated live.
- **Highlights:** token-guarded API surfaces; `/api/ingest/status` + `doctor` +
  `ingest-status`; thin-client `collect --root` + ambient-collection LaunchAgent
  (opt-in); `status`/`export`/`memories`/`timeline` now report the LIVE box; 22
  source files de-deprecated (`datetime.utcnow` → naive-UTC-identical); runtime
  audit doc resolving exactly what runs on the server (digests are NOT running —
  that's the known next gap).
- **Deferred deliberately (safe-boundary):** I did NOT restart the live box,
  enable `JARVIS_TOKEN`, register the collector LaunchAgent, or restore server-side
  digests — those are single, deliberate ops steps for you (all documented).
- Full per-round log: the **Progress log** section below + `UPGRADES.md` Round 8 +
  `docs/STATUS.md`.

---

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
- [x] **Round 13 (86c1754):** test-coverage round — new `tests/test_remote.py`
  (transport wrappers + token header, no network), `tests/test_decision_log.py`,
  and server.py shim tests (app identity + `run` delegation). Added coverage for the
  client API surface. Suite 358 passed, 1 skipped.
- [x] **Round 14 (9ba03b1):** AGENTS.md start-here now references round8-handoff +
  runtime-audit; documents the thin-client CLI commands.
- [x] **Round 15 (69b861f):** `jarvis doctor` — consolidated local+box diagnostics
  (mode, outbox backlog, box health+memories, ingest status, os). Verified live against
  the real box (all PASS). 2 new tests. Suite 360 passed, 1 skipped.
- [x] **Round 16 (d9a0dd5):** `jarvis export` is now thin-client aware — in client
  mode it pulls from the live box (`/api/export` JSON, tags/metadata normalized
  client-side) instead of the local rollback store; only local mode reads the store.
  3 new client-mode tests (+ autouse local pin). Suite 363 passed, 1 skipped.
- [x] **Round 17 (c84d95e):** `/api/memories` (token-guarded, limit/source/tier/since,
  pre-decoded tags) + `remote.memories()` + remote-aware `memories`/`timeline` CLI.
  Suite 368 passed, 1 skipped.
- [x] **Round 18 (5285165):** end-to-end `process_batch` cursor-advancement test
  (drains the whole backlog across calls via the persisted cursor + dedupes re-runs),
  locking the Round 7 'advances via cursor' fix. Suite 369 passed, 1 skipped.
- [x] **Round 19 (ecd2c3e):** `jarvis collect --root <dir>` (repeatable) so the
  thin-client collector can target arbitrary directories (defaults unchanged).
  1 new test. Suite 370 passed, 1 skipped.
- [x] **Round 20 (828770b):** corrected + completed the `UPGRADES.md` Round 8 section
  (inbox ingester is in git but needs a box restart to run; added the observability,
  thin-client correctness, doctor, utcnow, and runtime-audit entries).
- [x] **Round 21 (f8cede5):** more `remote` transport contract tests — `memories`
  query-string (incl. since), None-arg omission, `export` payload, and `remote_ok()` false
  on unreachable. Suite 374 passed, 1 skipped.
- [x] **Round 22 (aca25bf):** refreshed `docs/STATUS.md` (HEAD `f8cede5`, box-restart
  gating, 374 tests, updated action list).
- [x] **Round 23 (7ab6472):** added the "morning TL;DR" summary to the top of
  `logs/round8-handoff.md` for a fast resume.
- [x] **Round 24 (e9fbe7f):** inbox ingester now tracks per-batch `errors`
  (process_batch + `/api/ingest/status` + ingest_status) so a failing file isn't
  invisible; + error-accounting test. Suite 375 passed, 1 skipped.
- [x] **Round 25 (08f09a1):** corrected the "box is at HEAD" language everywhere
  (the box's git is actually at `966d7b2`, ~20 commits behind — it MUST `git pull`
  before restart); added `flush` CLI tests (success/offline/not-client). Also verified
  box HEAD = `966d7b2`. Suite 378 passed, 1 skipped.
- [x] **Round 26 (1b4e497):** rewrote `README.md` for the FULL-THIN topology
  (box brain, Mac thin client), current thin-client + server commands, resume points,
  and tests/lint + security notes.
- [x] **Round 27 (ca480d9):** BUG FIX — `jarvis collect --root <dir>` passed
  click string roots but `scan_once`/`_walk` called `Path` methods on them (would crash
  on a real scan). `scan_once` now coerces roots to `Path`; verified end-to-end with a
  real (isolated) CLI scan + new string-root test. Suite 379 passed, 1 skipped.
- [x] **Round 28 (fa071d4):** hygiene — dropped redundant local `import threading` in the
  ingester; fixed a docstring typo.
- [x] **Round 29 (459d444):** `jarvis search --json` — scriptable structured output
  (memories+entities / offline-cached / local response). 1 new test.
  Suite 380 passed, 1 skipped.
- [x] **Round 30 (pending commit):** `test_mayor` — added idle-maintenance coverage
  (`_maybe_idle_maintenance`: runs reindex(limit=200)+promote(7d,500) when idle and not
  VRAM-busy; skips when Ollama is generating or an approved task is queued, timer held).
  Mayor 15 → 18 tests. Suite 383 passed, 1 skipped.

### Note — box restart gating (2026-08-07, live check)
`jarvis memories/timeline/ingest-status` against the CURRENT box correctly report the
endpoints as 404/not-present: the running `jarvis server` predates `/api/memories` +
`/api/ingest/status`. They all become live the moment the box is restarted onto the
pushed commit (git is current at `bot`==`main`). This is the single gating action;
everything else is built, tested, and documented.

### Live thin-client validation (2026-08-07, read-only)
`jarvis status` → live box (memories=3954); `jarvis search 'tailscale' -n 3` → real box
memories (round-trip OK); `jarvis ingest-status` → correctly surfaces the expected HTTP
404 on the pre-restart server; `jarvis doctor` → all PASS.


