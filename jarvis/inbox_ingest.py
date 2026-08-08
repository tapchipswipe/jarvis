"""jarvis/inbox_ingest.py — lightweight, server-side inbox backlog ingester.

Runs INSIDE the ``jarvis server`` process (same Store/Chroma handle), so a
separate process never has to open a second Chroma lock on the live brain.

Why this is "lightweight" (deliberate):
  * The inbox backlog carries v2 sidecars (x.json) that already include the
    memory's source, source_id, timestamp, tier, route, and tags. We trust the
    sidecar and do NOT call extract_metadata()/classify() — both default to the
    7B model (qwen2.5:7b) and would thrash a 16 GB box across thousands of
    small raw files.
  * The only model work is embedding each chunk with the small embed model
    (nomic-embed-text) so the content becomes searchable.
  * Files are processed in small throttled batches so the server stays
    responsive to normal client requests.

Idempotent: dedupes on content-hash (and memory id), so re-runs are safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.paths import data_dir as _data_dir
from jarvis.store import Store, fingerprint

logger = logging.getLogger("jarvis.inbox_ingest")

# Box default (Windows) inbox; override with JARVIS_INBOX.
DEFAULT_INBOX = Path(os.environ.get("JARVIS_INBOX", "C:/data/jarvis/inbox"))
_SUFFIXES = {".md", ".txt", ".csv"}

# Thread-safe progress registry for /api/ingest/status observability.
_status_lock = threading.Lock()
_status: dict = {"active": False, "enabled": False, "inbox": str(DEFAULT_INBOX),
                 "errors": 0, "total": 0, "idle": False}


def ingest_status() -> dict:
    """Return an immutable snapshot of ingester progress (fine to read across threads)."""
    with _status_lock:
        return dict(_status)


def _set_status(active: bool, **updates) -> None:
    with _status_lock:
        _status["active"] = bool(active)
        _status.update(updates)
        _status["ts"] = _iso()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_sidecar(path: Path) -> dict:
    sc = path.with_suffix(".json")
    if sc.exists():
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def ingest_inbox_file(store: Store, path: Path) -> int:
    """Ingest one inbox file into *store*, using its v2 sidecar metadata.

    Returns the number of chunks added (0 if duplicated/blank).
    """
    text = (path.read_text(errors="ignore") or "").strip()
    if not text:
        return 0
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    if store.exists_by_content(content_hash):
        return 0

    sidecar = _load_sidecar(path)
    device_id = path.parent.name or "device"
    source = sidecar.get("source") or "device"
    source_id = sidecar.get("source_id") or str(path)
    ts = sidecar.get("timestamp") or _iso()
    tier = sidecar.get("tier") or "raw"
    route = sidecar.get("route") or "unclassified"
    tags = list(sidecar.get("tags") or sidecar.get("tag_seeds") or [])
    if device_id not in tags:
        tags.append(device_id)

    # Derive the memory id from the *resolved* sidecar timestamp (ts), not a
    # fresh _iso() per run: _iso() changed every call, so store.exists(fid)
    # never matched on a re-run and the id was nondeterministic. Anchoring on
    # the record's own ts keeps the id (and the derived chunk ids) stable
    # across runs for the same record, so marker/dedup and the cursor still work.
    fid = fingerprint("device", source_id, text, ts)
    if store.exists(fid):
        return 0

    meta = {"device": device_id, "path": str(path)}
    if sidecar:
        meta["sidecar"] = sidecar

    chunks = chunk_document(text, metadata=meta)
    added = 0
    for i, chunk in enumerate(chunks):
        cid = f"{fid}-{i}"
        emb = get_embedding(chunk["text"])
        store.add(cid, source, source_id, ts, chunk["text"], tags, meta, emb,
                  tier=tier, route=route)
        added += 1
    return added
def inbox_files(inbox_dir: Path | None = None) -> list[Path]:
    root = inbox_dir or DEFAULT_INBOX
    if not root.exists():
        return []
    return [p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in _SUFFIXES]


def _inbox_marker(root: Path) -> tuple[int, int] | None:
    """Cheap fingerprint (file count, max mtime_ns) of the inbox tree.

    Lets an unchanged, drained inbox *idle* without re-running the full
    scan+dedupe pass on every cycle: new or modified files bump max mtime,
    deletions change the count. Returns None when *root* is absent.
    """
    if not root.is_dir():
        return None
    count = 0
    max_mtime = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if Path(fn).suffix.lower() not in _SUFFIXES:
                    continue
                try:
                    st = os.lstat(os.path.join(dirpath, fn))
                except OSError:
                    continue
                count += 1
                max_mtime = max(max_mtime, st.st_mtime_ns)
    except OSError:
        return None
    return (count, max_mtime)


def process_batch(inbox_dir: Path | None = None, batch: int = 50,
                  cooldown: float = 0.2,
                  cursor_path: Path | None = None) -> dict:
    """Process up to *batch* inbox files through one in-process Store.

    Advances past previously-seen files via a persisted *cursor_path* (the last
    fully-processed file), so repeated calls drain the *whole* backlog instead
    of always re-processing the same first batch. Returns
    {processed, added, remaining, done, cursor}.
    """
    files = sorted(inbox_files(inbox_dir), key=lambda p: str(p))
    total = len(files)
    if not files:
        _set_status(True, inbox=str(inbox_dir or DEFAULT_INBOX), total=0,
                    remaining=0, processed=0, added=0, errors=0, done=True, cursor=None)
        return {"processed": 0, "added": 0, "errors": 0, "remaining": 0,
                "total": 0, "done": True, "cursor": None}

    start_idx = 0
    if cursor_path is not None:
        try:
            last = (cursor_path.read_text(encoding="utf-8") or "").strip()
        except OSError:
            last = ""
        if last:
            for i, p in enumerate(files):
                if str(p) == last:
                    start_idx = i + 1
                    break

    todo = files[start_idx:start_idx + batch]
    if not todo:
        _set_status(True, inbox=str(inbox_dir or DEFAULT_INBOX), total=total,
                    remaining=0, processed=0, added=0, errors=0, done=True, cursor=str(files[-1]))
        return {"processed": 0, "added": 0, "errors": 0, "remaining": 0,
                "total": total, "done": True, "cursor": str(files[-1])}

    store = Store()
    processed = 0
    added = 0
    errors = 0
    cursor_file = None  # last consecutively-successful file; stays put on failure
    try:
        advancing = True
        for path in todo:
            try:
                added += ingest_inbox_file(store, path)
                if advancing:
                    cursor_file = str(path)
            except Exception:
                # A failed file must NOT be permanently skipped: stop advancing
                # the cursor here so this file (and anything after it) is retried
                # next cycle. Later files in this batch are still processed now;
                # on the retry they're idempotent (dedup on content-hash).
                errors += 1
                advancing = False
                logger.warning("inbox ingest failed for %s", path, exc_info=True)
            processed += 1
            if added and cooldown:
                time.sleep(cooldown)
    finally:
        store.close()
    remaining = len(files) - (start_idx + processed)
    _set_status(True, inbox=str(inbox_dir or DEFAULT_INBOX), total=total,
                processed=processed, added=added, errors=errors, remaining=remaining,
                done=remaining <= 0, cursor=cursor_file)
    return {"processed": processed, "added": added, "errors": errors,
            "total": total, "remaining": remaining, "done": remaining <= 0,
            "cursor": cursor_file}



def start_background_ingester() -> None:
    """Start a daemon thread that drains the inbox backlog in throttled batches.

    Called from ``run_dashboard``. Env controls:
      JARVIS_INBOX_DISABLE=1   -> do not start
      JARVIS_INBOX             -> inbox dir (default C:/data/jarvis/inbox)
      JARVIS_INBOX_BATCH       -> files per cycle (default 50)
      JARVIS_INBOX_COOLDOWN    -> pause s per ingested file (default 0.2)
      JARVIS_INBOX_CYCLE       -> s between cycles (default 15)
    """
    if os.environ.get("JARVIS_INBOX_DISABLE") == "1":
        logger.info("Inbox ingester disabled via JARVIS_INBOX_DISABLE=1")
        _set_status(False, enabled=False)
        return
    _set_status(True, enabled=True)
    batch = int(os.environ.get("JARVIS_INBOX_BATCH", "50"))
    cooldown = float(os.environ.get("JARVIS_INBOX_COOLDOWN", "0.2"))
    cycle = float(os.environ.get("JARVIS_INBOX_CYCLE", "15"))
    inbox_dir = Path(os.environ.get("JARVIS_INBOX", "C:/data/jarvis/inbox"))
    cursor_path = Path(os.environ.get("JARVIS_INBOX_CURSOR") or
                       str(_data_dir() / "inbox_ingest_cursor.txt"))
    marker_path = Path(os.environ.get("JARVIS_INBOX_MARKER_FILE") or
                       str(_data_dir() / "inbox_ingest_drained_marker.txt"))

    def _load_persisted_marker() -> tuple[int, int] | None:
        try:
            txt = (marker_path.read_text(encoding="utf-8") or "").strip()
            if txt:
                count_s, mtime_s = txt.split(":", 1)
                return (int(count_s), int(mtime_s))
        except (OSError, ValueError):
            pass
        return None

    def _save_persisted_marker(m: tuple[int, int]) -> None:
        try:
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(f"{m[0]}:{m[1]}", encoding="utf-8")
        except OSError:
            pass

    def _loop() -> None:
        logger.info("Inbox backlog ingester started on %s (batch=%d)", inbox_dir, batch)
        idle = float(os.environ.get("JARVIS_INBOX_IDLE", "30"))
        last_marker = _load_persisted_marker()
        idle_state = False
        # Bootstrap: if the inbox is unchanged since the last drain, start
        # already-idle so a server restart does NOT trigger a wasteful full
        # re-drain (and its Chroma lock contention that stalls concurrent queries).
        current = _inbox_marker(inbox_dir)
        if current is not None and current == last_marker:
            idle_state = True
            last_marker = current
            logger.info("Inbox unchanged since last drain — starting idle (no re-drain)")
        while True:
            try:
                marker = _inbox_marker(inbox_dir)
                if marker is not None and marker == last_marker and idle_state:
                    # Inbox unchanged since it finished draining -> idle-poll on
                    # the cheap fingerprint instead of re-scanning the tree (a
                    # new/modified file bumps the marker and drops us out here).
                    _set_status(True, inbox=str(inbox_dir), total=marker[0],
                                remaining=0, processed=0, added=0, errors=0,
                                done=True, cursor=None, idle=True)
                    time.sleep(idle)
                    continue

                res = process_batch(inbox_dir, batch=batch, cooldown=cooldown,
                                    cursor_path=cursor_path)
                last_marker = marker
                if res.get("done"):
                    logger.info("Inbox backlog drained: %s", res)
                    idle_state = True
                    if marker is not None:
                        _save_persisted_marker(marker)
                    try:  # reset cursor so a fresh scan picks up new files anywhere
                        cursor_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    time.sleep(idle)
                else:
                    idle_state = False
                    if res.get("cursor"):
                        try:
                            cursor_path.parent.mkdir(parents=True, exist_ok=True)
                            cursor_path.write_text(res["cursor"], encoding="utf-8")
                        except OSError:
                            pass
                    logger.info("Inbox ingester progress: %s", res)
                    time.sleep(cycle)
            except Exception:
                logger.exception("inbox ingester cycle error")
                time.sleep(cycle)

    t = threading.Thread(target=_loop, name="jarvis-inbox-ingester", daemon=True)
    t.start()

