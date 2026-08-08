import datetime as _dt
import json
import os
import subprocess

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.paths import data_dir, ensure_private_dir
from jarvis.store import fingerprint

# Stable timestamp for the whole system snapshot batch. Using a fixed value (not
# datetime.now()) keeps the fingerprint unchanged between runs, so store.exists()
# fires and the (expensive) system_profiler/log re-embed is skipped when the
# snapshot has not changed. The apps list is effectively static between boots.
_SNAPSHOT_TS = "1970-01-01T00:00:00"

# Rolling window (hours) for the unified-log snapshot. The window changes every
# run, so its memory must be timestamped at the window start (now - window) rather
# than the fixed _SNAPSHOT_TS: a 1970 timestamp makes Jarvis treat error-log
# snapshots as ancient/non-recent. The content-based fingerprint stays stable so
# re-runs still dedup.
_LOG_WINDOW_HOURS = 24

# Throttle for the expensive system probes (system_profiler + `log show`). Those
# subprocesses are slow (up to ~120s) and the log output changes every cycle, so
# the stable-ts dedup only saves the *post-process* work: on an idle Mac every
# sync still pays the full subprocess cost. We gate both probes behind a stored
# last-run timestamp so the probes run at most once per interval (default 1h),
# not once per sync. First-run behavior is unchanged (no marker yet => run).
_THROTTLE_DEFAULT_HOURS = 1.0
_THROTTLE_ENV = "JARVIS_SYSTEM_THROTTLE_HOURS"
# Marker file name lives under data_dir("data", "state") so it is per-profile and
# persists across syncs. Per-source: both probes share the "system" source marker.
_MARKER_REL = ("data", "state", "system_probed_at.json")


def _throttle_hours() -> float:
    """Configured throttle window in hours (env-overridable, default 1h)."""
    try:
        return float(os.environ.get(_THROTTLE_ENV, _THROTTLE_DEFAULT_HOURS))
    except (TypeError, ValueError):
        return _THROTTLE_DEFAULT_HOURS


def _marker_path():
    return data_dir(*_MARKER_REL)


def _last_probe_ts() -> float | None:
    """Epoch seconds of the last successful throttle-marking, or None if never."""
    try:
        return float(_marker_path().read_text().strip())
    except (OSError, ValueError):
        return None


def _within_throttle(now: float | None = None) -> bool:
    """True if the last probe ran within the configured interval (skip this sync)."""
    if _throttle_hours() <= 0:
        return False  # disabled
    last = _last_probe_ts()
    if last is None:
        return False  # first run => not throttled
    now = _dt.datetime.now(_dt.timezone.utc).timestamp() if now is None else now
    return (now - last) < _throttle_hours() * 3600.0


def _mark_probed() -> None:
    """Persist 'now' as the last probe time so the next sync within the window skips."""
    path = _marker_path()
    ensure_private_dir(path.parent)
    path.write_text(str(_dt.datetime.now(_dt.timezone.utc).timestamp()))


def _log_window_start() -> str:
    """ISO timestamp marking the start of the current unified-log window (now - 24h)."""
    start = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=_LOG_WINDOW_HOURS)
    return start.strftime("%Y-%m-%dT%H:%M:%S")


def sync_system(store):
    # Throttle before any subprocess: if we probed within the interval, skip the
    # expensive system_profiler/log-show entirely and emit no new memories.
    if _within_throttle():
        return 0

    count = 0
    try:
        result = subprocess.run(["system_profiler", "SPApplicationsDataType", "-json"], capture_output=True, text=True, timeout=60, check=False)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            apps = data.get("_items", [])
            app_summary = "\n".join(f"{a.get('_name', '')} | {a.get('version', '')} | {a.get('path', '')}" for a in apps[:200])
            text = f"Installed Applications:\n{app_summary}"
            source = "system"
            source_id = "installed-apps"
            ts = _SNAPSHOT_TS
            fid = fingerprint(source, source_id, text, ts)
            if not store.exists(fid):
                emb = get_embedding(text[:4000])
                chunks = chunk_document(text, metadata={"type": "system_snapshot"})
                for i, chunk in enumerate(chunks):
                    # First chunk keeps the base fid so store.exists(fid) matches
                    # and the whole (stable-ts) snapshot is skipped on re-sync.
                    cid = fid if i == 0 else f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["system"], {"type": "installed_apps"}, emb)
                    count += 1
    except Exception as e:
        print(f"system error: {e}")
    try:
        log_result = subprocess.run(
            ["log", "show", "--predicate", "eventMessage contains 'error' OR eventMessage contains 'fault'", "--last", "24h", "--style", "compact"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if log_result.returncode == 0 and log_result.stdout.strip():
            log_text = log_result.stdout[:40000]
            source = "system"
            source_id = "unified-log-24h"
            # Timestamp the rolling window at its start (now - 24h) so Jarvis sees
            # the error-log snapshot as recent. The fingerprint still uses the
            # stable _SNAPSHOT_TS so identical window content dedups on re-run.
            ts = _log_window_start()
            fid = fingerprint(source, source_id, log_text, _SNAPSHOT_TS)
            if not store.exists(fid):
                emb = get_embedding(log_text[:4000])
                chunks = chunk_document(log_text, metadata={"type": "unified_log", "period": "24h"})
                for i, chunk in enumerate(chunks):
                    # First chunk keeps the base fid so store.exists(fid) matches
                    # and the whole (content-stable) log batch is skipped on re-sync.
                    cid = fid if i == 0 else f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["system", "logs"], {"type": "unified_log", "period": "24h"}, emb)
                    count += 1
    except Exception as e:
        print(f"system log error: {e}")
    # The probes ran (or attempted); record the time so the next sync within the
    # throttle window skips them. Marking even on a failed probe avoids a retry
    # storm hammering the expensive subprocesses every sync cycle.
    _mark_probed()
    return count

