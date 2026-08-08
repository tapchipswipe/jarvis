from datetime import datetime, timezone
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

WATCH_DIRS = [
    Path.home() / "Documents",
    Path.home() / "notes",
    Path.home() / "obsidian",
]


class FileIngestionHandler(FileSystemEventHandler):
    def __init__(self, store, extensions=None):
        self.store = store
        self.extensions = extensions or {".md", ".txt", ".pdf", ".json", ".csv"}

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in self.extensions:
            return
        self._ingest(path)

    def _ingest(self, path: Path):
        try:
            text = path.read_text(errors="ignore")
            from jarvis.collectors import capture as _capture
            _queued = _capture(text, source="file", tags=["file"], metadata={"path": str(path)})
            if _queued is not None:
                if _queued:
                    print(f"Captured {path} (queued for server).")
                return
            from jarvis.extract import extract_metadata
            extraction = extract_metadata(text)
            base_tags = ["file"] + extraction.get("tags", [])[:5]
            chunks = chunk_document(text, metadata={"path": str(path), "entities": extraction.get("entities", [])})
            emb = get_embedding(text[:4000])
            source = "file"
            source_id = str(path)
            ts = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(tzinfo=None).isoformat()
            fid = fingerprint(source, source_id, text, ts)
            added = 0
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                self.store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"path": str(path), "entities": extraction.get("entities", [])}, emb)
                added += 1
            if added:
                print(f"Ingested {added} chunk(s) from {path}")
        except Exception as e:
            print(f"Failed to ingest {path}: {e}")


def start_watcher(store):
    observer = Observer()
    handler = FileIngestionHandler(store)
    for d in WATCH_DIRS:
        if d.exists():
            observer.schedule(handler, str(d), recursive=True)
    observer.start()
    return observer


if __name__ == "__main__":
    from jarvis.store import Store
    store = Store()
    observer = start_watcher(store)
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    store.close()
