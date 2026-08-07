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
- [x] **Round 3 (pending commit):** Added `_host_ok` token guards to the previously-open
  mutating/sensitive routes — `/api/idea`, `/api/tasks/approve`, `/api/tasks/reject`,
  `/api/sessions`, and `/api/export`. `/api/health` stays open (Mac health checker).
  2 new server tests. Suite 334 passed, 1 skipped.

