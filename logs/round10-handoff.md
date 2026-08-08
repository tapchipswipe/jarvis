# Round 10 handoff — Hardening, hermeticity, and Iron-Man UX push (2026-08-07)

## TL;DR
The circle ran **2026-08-07 ~18:37–23:00**, landed **47 commits** on branch `bot`
(HEAD `c38a2b2`). The suite went **450 → 614 passed / 1 skipped** (now **hermetic** —
clears `JARVIS_*` env vars in an autouse fixture so it is green regardless of host
state), ruff debt **219 → 185**, and docs updated (STATUS/RESUME committed to Round 10
state in `8554a6f`). The box still runs the previous commit — **one `git pull` + a
`JarvisServer` restart activates all of this** (every change is backward-compatible;
token stays enforced).

## Delivered this round (grouped by theme)
**Hermetic, stable test/CI foundation**
- `5c11bc6` suite is now hermetic — autouse fixture clears `JARVIS_*` env vars.
- `d4c495a` gitignore ingest runtime marker/cursor files.
- `37a3023` fixed genuine F821/F811/PLW1510 lint instances (ruff 219 → 185).

**Vector / embedding health**
- `bf4f290` don't commit failed embeddings as zero-vectors; leave them un-embedded for retry (zero-vector fix).
- `dda46ad` delete vectors from Chroma when memories expire/supersede (Chroma pruning).
- `b3e339e` cap search results at `n_results` regardless of `re_rank`; bound extract cache.
- `df763b0` re-rank search by (tier weight, similarity) so equal-tier results keep vector order.
- `a8b28fb` recency tiebreak in search ranking so recent memories win ties.
- `b70739d` stable fingerprint from file mtime so unchanged sessions don't re-embed.

**Dedup fixes across collectors / brain / consolidation**
- `2a91643` stable fingerprint for shell/system — file mtime so skip fires and idle is fast.
- `208edd5` key calendar/email `source_id` on native unique id so distinct items aren't dropped.
- `59c308c` derive memory id from record timestamp so ids are stable across runs.
- `ed7c4bf` don't advance cursor past failed files so they get retried.
- `98493c6` / `e5033f3` / `6cc4b58` stable content fingerprints so consolidation / save_session / correct / upgrade dedup actually fires.
- `1280f05` use full content hash for push bundle cid so distinct memories never collide.

**Graph quality**
- `d4cac5a` collapse reversed `co_participant` edges — one canonical row per unordered pair.
- `83095bd` honor depth in `get_related` and stop noisy person-domain `co_participant` edges.
- `d616194` stop emitting junk 'Organization reference' entity that polluted the graph.
- `6cc4b58` link manual/session/consolidated memories into the knowledge graph.

**Thin-client chat correctness (memory-less bug)**
- `93554c6` route chat through the box in thin-client mode so it isn't memory-less.
- `e3b1bf3` resolve `run_turn` NameError in local chat path.
- `0c40dc1` `/sources` now matches `role==tool` so it shows which tools ran.
- `f7d5378` tiered models — auto-route by complexity (fast/medium/big) + console `/model` override; `JARVIS_CHAT_MODEL_{FAST,BIG}`.
- `c030656` configurable `JARVIS_CHAT_MODEL` (default 7B, set box to llama3.2:1b for snappy ask/console/chat); resilient console + longer query timeout.
- `ef16e9c` sources only on explicit `/sources on`; recall questions escalate to medium (never fast toy model).
- `4b03dda` hide sources by default for natural chat; `/sources` toggle + auto-show on recall questions.
- `6d5be46` brain synthesizes retrieval query from chat history so follow-ups get vector signal.

**Notify / triggers robustness**
- `772da65` escape quotes/newlines in notification bodies so popups don't silently fail.
- `34a9b73` short-circuit notification backends — one popup, not two.
- `f73cdee` record real delivery channel and friendly time-ago.
- `c9a8a52` render template variables in notify action bodies.
- `6803dbc` reject `*/0` at parse time and skip per-tick TaskQueue when unused.
- `e17c5a3` upcoming-events-poll queries calendar and only notifies on real events.

**Iron-Man UX features**
- `a4ee3a8` suggest proactive follow-ups from grounded entities (J.A.R.V.I.S. touch).
- `da2ca2a` dynamic context-aware greeting banner in the console.
- `e17c5a3` real calendar poll for upcoming-events notifications.

**API hardening**
- `ab465f2` return 400 on malformed history instead of silently dropping it.
- `c38a2b2` `classify --dry-run` now truly skips applying the envelope (no store writes) — forwards `dry_run` through both `classify` and `classify_recent`.
- `fd8dadd` exercise `/model` override and `/api` model+history params.

**RAG store reuse / resource hygiene**
- `f7c74e3` reuse the caller's store for RAG injection (one Chroma handle per turn).
- `5ca30ad` `summarize_cluster` no longer opens a second Store/Chroma handle.
- `4329bc7` `promote_old` closes the Store it opens (owned flag).
- `434aecb` throttle `system_profiler`/`log-show` probes to once per hour.
- `56ac597` close night-mode subprocess output handle to avoid fd leak.
- `66ad90c` stop the watchdog observer after `run_sync` and drop unused imports.

**Collector reliability**
- `3df7021` cap scan file size so huge files don't OOM or bloat the outbox.
- `c32bca8` run OCR independently of exiftool availability.
- `7974859` correct Chrome/Safari days-back window (epoch mismatch).
- `0955563` one bad path no longer aborts the whole scan; staged enqueues still commit.
- `5e41756` `/no-save` now truly disables saving (was inverted).
- `554c63f` timestamp the unified-log snapshot at window start (not 1970).

## Verification (this session)
- **pytest**: `614 passed, 1 skipped` (hermetic, `-p no:cacheprovider`) — matches the circle claim.
- **ruff**: `jarvis/ tests/` → **185 errors** (down from 219; remaining are intentional).
- **doctor**: `jarvis doctor` → 5 PASS / 1 WARN (git WARN only: `HEAD=c38a2b2`, `origin/bot=ef16e9c` — origin is behind only because we do not push; expected).
- **server --check**: `OK app=loaded memories=3951 port=8766`.

## What's NOT done / caveats
- **The box needs activation**: `ssh despo@100.102.0.99 'git -C C:/Users/despo/jarvis pull'` then restart the `JarvisServer` scheduled task (or re-run `C:\data\jarvis\server-start.bat`). Every change is backward-compatible; token stays enforced.
- Optional Tailscale admin-console ACL to scope `:8766`.
- Remaining ruff debt is **intentional**: `BLE001` (catch-all `except Exception` guards) and `S110` (empty `except`), plus a few style items — not worth churning.
- **Retired Mac-local daemons have 0 coverage by design** — they run on the Mac side and are not in the pytest suite.

## Session state
- Repo: branches `bot` == `main` == `origin/bot` == `origin/main`, HEAD `4f67fcd`, working tree clean.
- **PUSHED + ACTIVATED (2026-08-08):** `git push origin bot` + `main` completed; the box pulled to `4f67fcd` and `JarvisServer` was restarted (stale PID force-killed to free port 8766). Verified live: `/api/health` uptime reset (fresh boot), memories=4132 intact, thin-client `status` OK over the box.
- Suite 616 passed / 1 skipped; ruff 185 (no new F-codes); doctor PASS (box reachable, 4132 memories, ingester drained, idle).
- Box: healthy, 4,132 memories, ingester idle/drained, on the new commit.