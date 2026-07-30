import subprocess
import json
from pathlib import Path
from datetime import datetime
from jarvis.store import fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document


def sync_system(store):
    count = 0
    try:
        result = subprocess.run(["system_profiler", "SPApplicationsDataType", "-json"], capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
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
    except Exception as e:
        print(f"system error: {e}")
    try:
        log_result = subprocess.run(
            ["log", "show", "--predicate", "eventMessage contains 'error' OR eventMessage contains 'fault'", "--last", "24h", "--style", "compact"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if log_result.returncode == 0 and log_result.stdout.strip():
            log_text = log_result.stdout[:40000]
            source = "system"
            source_id = "unified-log-24h"
            ts = datetime.utcnow().isoformat()
            fid = fingerprint(source, source_id, log_text, ts)
            if not store.exists(fid):
                emb = get_embedding(log_text[:4000])
                chunks = chunk_document(log_text, metadata={"type": "unified_log", "period": "24h"})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["system", "logs"], {"type": "unified_log", "period": "24h"}, emb)
                    count += 1
    except Exception as e:
        print(f"system log error: {e}")
    return count
