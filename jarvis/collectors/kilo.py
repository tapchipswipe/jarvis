from pathlib import Path
from datetime import datetime
from jarvis.store import fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document

KILO_SESSION_DIR = Path.home() / ".config" / "kilo" / "sessions"


def ingest_kilo_sessions(store):
    if not KILO_SESSION_DIR.exists():
        return 0
    count = 0
    for f in KILO_SESSION_DIR.rglob("*.json"):
        try:
            data = f.read_text(errors="ignore")
            source = "ai_kilo"
            source_id = str(f)
            ts = datetime.utcnow().isoformat()
            fid = fingerprint(source, source_id, data, ts)
            if store.exists(fid):
                continue
            from jarvis.extract import extract_metadata
            extraction = extract_metadata(data)
            base_tags = ["ai", "kilo"] + extraction.get("tags", [])[:5]
            emb = get_embedding(data[:4000])
            chunks = chunk_document(data, metadata={"path": str(f), "entities": extraction.get("entities", [])})
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"path": str(f), "entities": extraction.get("entities", [])}, emb)
                count += 1
        except Exception:
            pass
    return count
