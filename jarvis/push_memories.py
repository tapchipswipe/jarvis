"""push_memories.py — push local memories to Lightspeed with a durable queue.

Replaces the old fire-and-forget push:

  * Every run enqueues new memories (idempotent on content hash). Completed
    items are removed; failures stay in the queue and are retried with
    exponential backoff (push_backoff) instead of being dropped.
  * When Lightspeed is unreachable, nothing is transmitted — items simply
    remain queued for the next run.
  * Large batches are staged into a single tar.gz and extracted remotely
    (one SCP instead of thousands); any failure falls back to per-file SCP.
  * Sync results are recorded in the store's sync_log table.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis.device_id import get_device_id
from jarvis.store import Store
from jarvis.sync.push import (
    BATCH_MIN_FILES,
    LIGHTSPEED_INBOX,
    ensure_remote_dir,
    lightspeed_reachable,
    push_backoff,
    push_bundle,
    scp_put,
    stage_bundle,
)


def _cell(row, key: str, default=""):
    """Safe column access that works on both sqlite3.Row and dict."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def build_sidecar(row, device_id: str) -> dict:
    """Build the v2 sidecar dict for a memory row."""
    content = row["content"]
    tags = json.loads(_cell(row, "tags") or "[]")
    metadata = json.loads(_cell(row, "metadata") or "{}")
    sidecar = {
        "version": 2,
        "source_device": device_id,
        "pushed_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "source": row["source"],
        "source_id": _cell(row, "source_id"),
        "timestamp": _cell(row, "timestamp"),
        "tier": _cell(row, "tier", "raw"),
        "route": _cell(row, "route") or "unclassified",
        "confidence": None,
        "tag_seeds": tags,
        "tags": tags,
        "metadata": metadata,
        "action_atom": None,
        "target_list": None,
        "escalate_reason": None,
    }
    if isinstance(metadata, dict):
        envelope = metadata.get("envelope") or metadata.get("sidecar") or {}
        sidecar["confidence"] = envelope.get("confidence")
        sidecar["action_atom"] = envelope.get("action_atom")
        sidecar["target_list"] = envelope.get("target_list")
        sidecar["escalate_reason"] = envelope.get("escalate_reason")
        sidecar["tag_seeds"] = envelope.get("tag_seeds", tags)
    return sidecar


def _enqueue_new_memories(store: Store, device_id: str) -> int:
    """Enqueue every active memory not already in the queue. Returns new count."""
    rows = store.conn.execute(
        "SELECT * FROM memories WHERE superseded = 0"
    ).fetchall()
    before = store.push_queue_stats()["total"]
    for r in rows:
        content = r["content"]
        push_key = hashlib.sha256(content.encode()).hexdigest()
        store.enqueue_push(push_key, content, build_sidecar(r, device_id))
    return store.push_queue_stats()["total"] - before


def _push_entry(store: Store, entry: dict, device_id: str) -> bool:
    """Per-file SCP push of one queue entry."""
    content = entry["content"]
    sidecar = json.loads(entry["sidecar"] or "{}")
    cid = hashlib.sha256(content.encode()).hexdigest()[:16]
    base_name = f"{cid}_{sidecar.get('tier', 'raw')}_{sidecar.get('source', 'device')}"
    txt_name = f"{base_name}.txt"
    json_name = f"{base_name}.json"
    txt_tmp = Path("/tmp") / txt_name
    json_tmp = Path("/tmp") / json_name
    txt_tmp.write_text(content, encoding="utf-8")
    json_tmp.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    try:
        scp_put(txt_tmp, f"{LIGHTSPEED_INBOX}/{device_id}/{txt_name}")
        scp_put(json_tmp, f"{LIGHTSPEED_INBOX}/{device_id}/{json_name}")
        return True
    finally:
        txt_tmp.unlink(missing_ok=True)
        json_tmp.unlink(missing_ok=True)


def _push_batch(store: Store, entries: list[dict], device_id: str) -> bool:
    """Stage a batch, SCP it once, and extract remotely. False on any failure."""
    import subprocess as _sp
    try:
        bundle = stage_bundle(entries, device_id)
        try:
            ok = push_bundle(bundle)
        finally:
            bundle.unlink(missing_ok=True)
        return ok
    except (_sp.CalledProcessError, OSError):
        return False


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    store = Store()
    try:
        device_id = get_device_id()
        enqueued = _enqueue_new_memories(store, device_id)
        queue = store.push_queue_stats()
        print(f"Queue: {queue['total']} item(s) (enqueued {enqueued} new).")

        if not lightspeed_reachable():
            store.log_sync("device_push", started, datetime.now(timezone.utc).isoformat(),
                           0, queue["total"])
            print("Lightspeed unreachable - items remain queued, nothing dropped.")
            return 0

        ensure_remote_dir()
        due = store.push_queue_due(limit=5000)
        if not due:
            store.log_sync("device_push", started, datetime.now(timezone.utc).isoformat(),
                           0, queue["total"])
            print("No queued items to push.")
            return 0

        pushed = 0
        failed = 0
        now = datetime.now(timezone.utc)
        use_batch = os.environ.get("JARVIS_PUSH_BATCH", "1") != "0" and len(due) >= BATCH_MIN_FILES

        if use_batch:
            print(f"Pushing {len(due)} item(s) as a single bundle...")
            if _push_batch(store, due, device_id):
                count = len(due)
                for e in due:
                    store.push_queue_success(e["id"])
                pushed = count
                print(f"Pushed {pushed} memories to lightspeed (batch).")
            else:
                print("Batch push failed - falling back to per-file SCP.")
                use_batch = False

        if not use_batch:
            for e in due:
                try:
                    if _push_entry(store, e, device_id):
                        store.push_queue_success(e["id"])
                        pushed += 1
                    else:
                        raise RuntimeError("scp returned failure")
                except Exception as exc:  # noqa: BLE001 - never abort the loop
                    attempts = e.get("attempts", 0) + 1
                    next_at = (now + timedelta(seconds=push_backoff(attempts))).isoformat()
                    store.push_queue_fail(e["id"], str(exc), attempts, next_at)
                    failed += 1
                    print(f"Failed to push item {e['id']}: {exc} (retry in {push_backoff(attempts)}s)")

        store.log_sync("device_push", started, datetime.now(timezone.utc).isoformat(),
                       pushed, failed)
        print(f"Pushed {pushed} memories to lightspeed ({failed} failed, kept queued).")
        return 0 if failed == 0 else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
