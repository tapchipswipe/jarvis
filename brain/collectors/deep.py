from pathlib import Path
from datetime import datetime
from brain.store import fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document

DEEP_DIRS = [
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Desktop",
]
DEEP_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".xml", ".html", ".log", ".yaml", ".yml", ".toml", ".rst", ".org"}


def sync_deep(store, max_files=1000):
    count = 0
    processed = 0
    for base_dir in DEEP_DIRS:
        if not base_dir.exists():
            continue
        for path in base_dir.rglob("*"):
            if processed >= max_files:
                break
            if path.is_dir() or path.suffix.lower() not in DEEP_EXTENSIONS:
                continue
            try:
                text = path.read_text(errors="ignore")
                if len(text.strip()) < 50:
                    continue
                source = "deep"
                source_id = str(path)
                ts = datetime.utcfromtimestamp(path.stat().st_mtime).isoformat()
                fid = fingerprint(source, source_id, text, ts)
                if store.exists(fid):
                    continue
                emb = get_embedding(text[:4000])
                chunks = chunk_document(text, metadata={"path": str(path)})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["deep"], {"path": str(path)}, emb)
                    count += 1
                processed += 1
            except Exception:
                pass
    return count
