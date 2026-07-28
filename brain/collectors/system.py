import subprocess
from pathlib import Path
from datetime import datetime
from brain.store import fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document


def sync_system(store):
    count = 0
    try:
        result = subprocess.run(["system_profiler", "SPApplicationsDataType", "-json"], capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            apps = data.get("_items", [])
            app_summary = "\n".join(f"{a.get('_name', '')} | {a.get('version', '')} | {a.get('path', '')}" for a in apps[:200])
            text = f"Installed Applications:\n{app_summary}"
            source = "system"
            source_id = "installed-apps"
            ts = datetime.utcnow().isoformat()
            fid = fingerprint(source, source_id, text, ts)
            if not store.exists(fid):
                emb = get_embedding(text[:4000])
                chunks = chunk_document(text, metadata={"type": "system_snapshot"})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["system"], {"type": "installed_apps"}, emb)
                    count += 1
    except Exception:
        pass
    return count
