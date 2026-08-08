# Round 11 handoff — Disk triage, thin-client collector/remote hardening, ingest-speed (2026-08-08)

## TL;DR
Autonomous find→fix→repeat session. Freed ~5 GB of full-disk (~2.2 GB free → ~7 GB) and restored
the backup chain that had failed overnight on "No space left on device". Then fixed real bugs in the
thin-client pipeline that surfaced as hangs, silent dropouts and outbox bloat, and **dramatically
sped up ingestion** (batched embeddings + batched Chroma adds → ~56×). All fixes are on `bot`==`main`
(HEAD `aee3f1d`), pushed to origin, **and deployed on the box** (server restarted onto the new code;
box working tree now at `aee3f1d`). Suite **632 passed / 1 skipped** (hermetic). Box healthy with
**~4,589 memories** (was ~4,137 at session start).

## What was done (grouped)

### Disk triage (first priority — the box/off-box backup chain was failing)
- Truncated **1.1 GB** of runaway logs: `~/.cline/data/logs/cline.log` (799 MB) + `hub-daemon.log` (364 MB).
- Cleared Homebrew / npm / uv / WebKit / HTTPStorages caches; removed redundant unencrypted
  `~/jarvis/backups/store-*` dirs (kept the `.age` encrypted archives); GoogleUpdater (744 MB),
  wallpaper aerials (596 MB), zoom + Cursor Electron caches, Steam appcache/depotcache/logs.
- Deleted installers for already-installed apps (`Antigravity IDE`, `Plaud`); left `ChatGPT.dmg` /
  `BitTorrent Neo.dmg` (not installed).
- **Re-ran `scripts/jarvis-backup.sh`** → new off-box `store-20260808.tar.gz.age` (58 MB). 3-2-1 chain restored.

### Performance (the headline — ingestion ~56× faster)
- `embed.py` `_ollama_embed` now sends all texts in **one** request to Ollama `/api/embed`
  (returns `embeddings:[...]`), with fallback to single-input `/api/embeddings`. Measured on box:
  **20 embeds in 0.35 s** (vs ~100 s serial). Deployed.
- New `Store.add_many` + `Brain.remember_many` — one SQLite commit + **one batched Chroma
  `collection.add`** per request instead of a per-item HNSW add (~4 s/item at scale). Wired into
  `/api/remember`. Combined with batched embed: `remember_batch(20)` = **1.77 s** (was ~100 s). Deployed.
- `remote.remember_batch` scales its timeout to batch size (`max(600, 10*n)`) — the old 60 s default
  was aborting multi-minute embeds (root cause of interminable `jarvis flush` hangs).

### Real collector / flush bugs
- `thin.py` `_should_exclude` now skips Python/tool caches (`.mypy_cache`, `.pytest_cache`,
  `.ruff_cache`, `.tox`, `.hypothesis`, `.nox`, `.ipynb_checkpoints`, `.egg-info`, …). The collector
  was walking into these and reading serialized cache blobs → **1500 files / ~1400 errors / intermittent
  hangs** → now **91 files / 0 errors / ~0.06 s**.
- `thin.py` exact-filename skip for boilerplate manifests/lockfiles (`package-lock.json`,
  `tsconfig.json`, `Cargo.lock`, `poetry.lock`, `pyproject.toml`, …) that padded the outbox with
  near-identical noise.
- `cache.enqueue` now rejects content > 20 KB (oversized collected blobs up to 796 KB were bloating
  the outbox and forcing the box to embed megabytes of junk per flush).
- `flush_outbox` now drains in **25-item chunks** (was 200) and marks-synced per chunk — the box's
  single uvicorn worker would block for minutes on one huge `/api/remember`, stalling every other
  request.

### Scripts / correctness
- `scripts/jarvis-collect.sh` + `scripts/jarvis-health-check.sh` pointed at `http://…:8766` after the
  Round 9b TLS move → the collect daemon's flush always reported `offline=True` and the outbox grew
  unboundedly. Now `https://` + pinned `JARVIS_TLS_FINGERPRINT`.
- Replaced all `datetime.utcfromtimestamp()` (5 files, removed in Python 3.15) with
  `fromtimestamp(ts, timezone.utc).replace(tzinfo=None)` — string-identical naive-UTC, so **fingerprints
  unchanged** and nothing re-ingests.

## Verification (this session)
- `pytest`: **632 passed, 1 skipped** (added 5 tests: cache-dir exclusion ×2, boilerplate exclusion,
  `add_many` batch + dedup).
- Box restarted twice (first for batched embed, then for batched add) — **memories preserved** across
  both (4,137 → ~4,589). Server healthy, `/api/health(+deep)` OK.
- Outbox drained from **~900+ junk items → ~27 legitimate** (daemon finishing the rest); the oversized
  mypy-cache junk was purged from the disposable outbox.

## State / how to resume
- Repo: `bot`==`main`==`origin/bot`==`origin/main`=`aee3f1d`, working tree clean. Box working tree at
  `aee3f1d`; running `JarvisServer` already loaded the core perf fixes. Post-quantum SSH warning is
  benign (box runs an older OpenSSH).
- **Docs updated:** `UPGRADES.md` (Round 11), `docs/STATUS.md` (Round 11, 632 tests, deployed),
  and this handoff.
- Environment/os notes for next session:
  - `search_codebase`/semantic tools time out on `~/Library/CloudStorage/OneDrive-SaintAnselmCollege/.Trash`
    — sweep that Trash to keep code search fast.
  - The box embeds new content through a single uvicorn worker (HNSW re-index); small-chunk flush keeps it
    responsive. Large first-time ingests are steady but not instant.
  - `timeout` (GNU coreutils) is not available on macOS; use Python `signal.alarm` or background-launch for
    time-bounded diagnostics.
  - The 30-second tool cap on shell commands means long ops (flush, backup) must be launched detached
    (`nohup … </dev/null >log 2>&1 &`) and polled.