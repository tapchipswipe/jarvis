from pathlib import Path
from datetime import datetime
HISTORY_PATH = Path.home() / ".zsh_history"


def tail_history(store, max_lines=2000):
    if not HISTORY_PATH.exists():
        return
    lines = HISTORY_PATH.read_text(errors="ignore").splitlines()
    for line in lines[-max_lines:]:
        try:
            parts = line.split(";", 1)
            cmd = parts[-1].strip() if len(parts) > 1 else parts[0].strip()
            if not cmd:
                continue
            source = "shell"
            source_id = cmd[:64]
            ts = datetime.utcnow().isoformat()
            from brain.store import fingerprint
            from brain.embed import get_embedding
            from brain.ingest import chunk_document
            from brain.extract import extract_metadata
            fid = fingerprint(source, source_id, cmd, ts)
            if store.exists(fid):
                continue
            extraction = extract_metadata(cmd)
            base_tags = ["shell"] + extraction.get("tags", [])[:5]
            chunks = chunk_document(cmd, metadata={"shell": cmd[:100], "entities": extraction.get("entities", [])})
            emb = get_embedding(cmd[:4000])
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"command": cmd[:200], "entities": extraction.get("entities", [])}, emb)
        except Exception:
            pass
