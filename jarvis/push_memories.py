import sys
from pathlib import Path
import json
import hashlib
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis.device_id import get_device_id
from jarvis.store import Store
from jarvis.sync.push import scp_put, ensure_remote_dir, LIGHTSPEED_INBOX


def main():
    ensure_remote_dir()
    store = Store()
    rows = store.conn.execute("SELECT * FROM memories WHERE superseded = 0 ORDER BY timestamp DESC").fetchall()
    if not rows:
        print("No memories to push.")
        store.close()
        return
    device_id = get_device_id()
    pushed = 0
    for r in rows:
        content = r["content"]
        cid = hashlib.sha256(content.encode()).hexdigest()[:16]
        base_name = f"{cid}_{r['tier']}_{r['source']}"
        txt_name = f"{base_name}.txt"
        json_name = f"{base_name}.json"
        txt_tmp = Path("/tmp") / txt_name
        txt_tmp.write_text(content, encoding="utf-8")
        sidecar = {
            "version": 2,
            "source_device": device_id,
            "pushed_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "source": r["source"],
            "source_id": r["source_id"],
            "timestamp": r["timestamp"],
            "tier": r["tier"],
            "route": r["route"] or "unclassified",
            "confidence": None,
            "tag_seeds": json.loads(r["tags"] or "[]"),
            "tags": json.loads(r["tags"] or "[]"),
            "metadata": json.loads(r["metadata"] or "{}"),
            "action_atom": None,
            "target_list": None,
            "escalate_reason": None,
        }
        try:
            meta = json.loads(r["metadata"] or "{}")
            if isinstance(meta, dict):
                envelope = meta.get("envelope") or meta.get("sidecar") or {}
                sidecar["confidence"] = envelope.get("confidence")
                sidecar["action_atom"] = envelope.get("action_atom")
                sidecar["target_list"] = envelope.get("target_list")
                sidecar["escalate_reason"] = envelope.get("escalate_reason")
                sidecar["tag_seeds"] = envelope.get("tag_seeds", sidecar["tag_seeds"])
        except Exception:
            pass
        json_tmp = Path("/tmp") / json_name
        json_tmp.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
        try:
            scp_put(txt_tmp, f"{LIGHTSPEED_INBOX}/{device_id}/{txt_name}")
            scp_put(json_tmp, f"{LIGHTSPEED_INBOX}/{device_id}/{json_name}")
            pushed += 1
        except Exception as e:
            print(f"Failed to push {base_name}: {e}")
        txt_tmp.unlink(missing_ok=True)
        json_tmp.unlink(missing_ok=True)
    store.close()
    print(f"Pushed {pushed} memories to lightspeed.")


if __name__ == "__main__":
    main()
