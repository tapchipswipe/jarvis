from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

KILO_SESSION_DIR = Path.home() / ".config" / "kilo" / "sessions"


def _file_mtime_ts(path: Path) -> str:
    """Stable ISO timestamp for a session file based on its mtime.

    Using the file's mtime (not datetime.now()) keeps the batch fingerprint
    unchanged between runs, so store.exists() fires and unchanged sessions are
    not re-embedded on every sync.
    """
    mtime = path.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).replace(tzinfo=None).isoformat()


def ingest_kilo_sessions(store):
    if not KILO_SESSION_DIR.exists():
        return 0
    count = 0
    for f in KILO_SESSION_DIR.rglob("*.json"):
        try:
            data = f.read_text(errors="ignore")
            source = "ai_kilo"
            source_id = str(f)
            # Stable batch ts from the file's mtime so the fingerprint is
            # unchanged when the session hasn't changed. A per-file now() made
            # the fingerprint differ every run and the skip (store.exists)
            # never fired, re-embedding every session on each sync.
            ts = _file_mtime_ts(f)
            fid = fingerprint(source, source_id, data, ts)
            if store.exists(fid):
                continue
            from jarvis.extract import extract_metadata
            extraction = extract_metadata(data)
            base_tags = ["ai", "kilo"] + extraction.get("tags", [])[:5]
            emb = get_embedding(data[:4000])
            chunks = chunk_document(data, metadata={"path": str(f), "entities": extraction.get("entities", [])})
            for i, chunk in enumerate(chunks):
                # First chunk keeps the base fid so store.exists(fid) matches
                # and the whole (stable-ts) session is skipped on re-sync.
                cid = fid if i == 0 else f"{fid}-{i}"
                store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"path": str(f), "entities": extraction.get("entities", [])}, emb)
                count += 1
        except Exception:
            pass
    return count

