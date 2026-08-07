import json
import subprocess

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

# Stable timestamp for the whole system snapshot batch. Using a fixed value (not
# datetime.now()) keeps the fingerprint unchanged between runs, so store.exists()
# fires and the (expensive) system_profiler/log re-embed is skipped when the
# snapshot has not changed. The apps list is effectively static between boots.
_SNAPSHOT_TS = "1970-01-01T00:00:00"


def sync_system(store):
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
            ts = _SNAPSHOT_TS
            fid = fingerprint(source, source_id, log_text, ts)
            if not store.exists(fid):
                emb = get_embedding(log_text[:4000])
                chunks = chunk_document(log_text, metadata={"type": "unified_log", "period": "24h"})
                for i, chunk in enumerate(chunks):
                    # First chunk keeps the base fid so store.exists(fid) matches
                    # and the whole (stable-ts) log batch is skipped on re-sync.
                    cid = fid if i == 0 else f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["system", "logs"], {"type": "unified_log", "period": "24h"}, emb)
                    count += 1
    except Exception as e:
        print(f"system log error: {e}")
    return count

