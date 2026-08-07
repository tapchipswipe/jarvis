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
import time
from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import Store, fingerprint

logger = logging.getLogger("jarvis.inbox_ingest")

# Box default (Windows) inbox; override with JARVIS_INBOX.
DEFAULT_INBOX = Path(os.environ.get("JARVIS_INBOX", "C:/data/jarvis/inbox"))
_SUFFIXES = {".md", ".txt", ".csv"}


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

    fid = fingerprint("device", source_id, text, _iso())
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


def process_batch(inbox_dir: Path | None = None, batch: int = 50,
                  cooldown: float = 0.2) -> dict:
    """Process up to *batch* inbox files through one in-process Store.

    Returns {processed, added, remaining, done}.
    """
    files = inbox_files(inbox_dir)
    if not files:
        return {"processed": 0, "added": 0, "remaining": 0, "done": True}
    store = Store()
    processed = 0
    added = 0
    try:
        for path in files[:batch]:
            try:
                added += ingest_inbox_file(store, path)
            except Exception:
                logger.warning("inbox ingest failed for %s", path, exc_info=True)
            processed += 1
            if added and cooldown:
                time.sleep(cooldown)
    finally:
        store.close()
    remaining = len(files) - processed
    return {"processed": processed, "added": added,
            "remaining": remaining, "done": remaining <= 0}


def start_background_ingester() -> None:
    """Start a daemon thread that drains the inbox backlog in throttled batches.

    Called from ``run_dashboard``. Env controls:
      JARVIS_INBOX_DISABLE=1   -> do not start
      JARVIS_INBOX             -> inbox dir (default C:/data/jarvis/inbox)
      JARVIS_INBOX_BATCH       -> files per cycle (default 50)
      JARVIS_INBOX_COOLDOWN    -> pause s per ingested file (default 0.2)
      JARVIS_INBOX_CYCLE       -> s between cycles (default 15)
    """
    import threading

    if os.environ.get("JARVIS_INBOX_DISABLE") == "1":
        logger.info("Inbox ingester disabled via JARVIS_INBOX_DISABLE=1")
        return
    batch = int(os.environ.get("JARVIS_INBOX_BATCH", "50"))
    cooldown = float(os.environ.get("JARVIS_INBOX_COOLDOWN", "0.2"))
    cycle = float(os.environ.get("JARVIS_INBOX_CYCLE", "15"))
    inbox_dir = Path(os.environ.get("JARVIS_INBOX", "C:/data/jarvis/inbox"))

    def _loop() -> None:
        logger.info("Inbox backlog ingester started on %s (batch=%d)", inbox_dir, batch)
        while True:
            try:
                res = process_batch(inbox_dir, batch=batch, cooldown=cooldown)
                if res.get("done"):
                    logger.info("Inbox backlog drained: %s", res)
                    time.sleep(30.0)  # idle-poll for new arrivals
                elif res.get("remaining", 0) > 0:
                    logger.info("Inbox ingester progress: %s", res)
            except Exception:
                logger.exception("inbox ingester cycle error")
            time.sleep(cycle)

    t = threading.Thread(target=_loop, name="jarvis-inbox-ingester", daemon=True)
    t.start()

