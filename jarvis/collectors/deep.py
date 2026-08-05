from pathlib import Path
from datetime import datetime
from jarvis.store import fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document

# Scaled-back deep-scan roots. The mac Library dirs contain tens of thousands of
# app-sandbox files and repeatedly hit Errno 11 (EAGAIN) during a live walk, so
# they are excluded here — Documents/Downloads/Desktop cover user-authored text.
DEEP_DIRS = [
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Desktop",
]
DEEP_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".xml", ".html", ".log", ".yaml", ".yml", ".toml", ".rst", ".org"}
DEEP_EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "Cache", "Caches", "tmp", "VirtualBox VMs"}


def _should_exclude(path: Path) -> bool:
    parts = path.parts
    for excl in DEEP_EXCLUDE_DIRS:
        if excl in parts:
            return True
    return False


def sync_deep(store, max_files=50000):
    count = 0
    processed = 0
    errs = 0
    for base_dir in DEEP_DIRS:
        if not base_dir.exists():
            continue
        for path in base_dir.rglob("*"):
            if processed >= max_files:
                break
            if path.is_dir():
                continue
            if _should_exclude(path):
                continue
            if path.suffix.lower() not in DEEP_EXTENSIONS:
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
                chunks = chunk_document(text, metadata={"path": str(path)})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    emb = get_embedding(chunk["text"])
                    store.add(cid, source, source_id, ts, chunk["text"], ["deep"], {"path": str(path)}, emb)
                    count += 1
                processed += 1
            except OSError as e:
                # EAGAIN / EMFILE during a live filesystem walk are common;
                # report each unique errno at most once instead of per-file.
                errs += 1
                if errs <= 5:
                    print(f"deep error: {e}")
            except Exception as e:
                errs += 1
                if errs <= 5:
                    print(f"deep error: {e}")
    return count
