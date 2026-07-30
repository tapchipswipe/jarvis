from pathlib import Path
from datetime import datetime
from jarvis.store import fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document

RSS_DIRS = [
    Path.home() / "Documents" / "feeds",
    Path.home() / "Documents" / "rss",
    Path.home() / "Downloads",
]


def sync_rss(store):
    count = 0
    for rss_dir in RSS_DIRS:
        if not rss_dir.exists():
            continue
        for rss_file in rss_dir.rglob("*"):
            if rss_file.is_dir() or rss_file.suffix.lower() not in {".xml", ".rss", ".atom", ".opml", ".txt"}:
                continue
            try:
                text = rss_file.read_text(errors="ignore")
                source = "rss"
                source_id = str(rss_file)
                ts = datetime.utcfromtimestamp(rss_file.stat().st_mtime).isoformat()
                fid = fingerprint(source, source_id, text, ts)
                if store.exists(fid):
                    continue
                emb = get_embedding(text[:4000])
                chunks = chunk_document(text, metadata={"path": str(rss_file)})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["rss"], {"path": str(rss_file)}, emb)
                    count += 1
            except Exception:
                pass
    return count
