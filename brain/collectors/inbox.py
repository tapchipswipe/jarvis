import json
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from brain.store import Store, fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document

LIGHTSPEED_INBOX = Path("/data/second-brain/inbox")


class InboxHandler(FileSystemEventHandler):
    def __init__(self, store):
        self.store = store
        self.seen = set()
        if Path("/data/second-brain/processed.json").exists():
            self.seen = set(json.loads(Path("/data/second-brain/processed.json").read_text()))

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in {".md", ".txt", ".json", ".csv"}:
            return
        key = f"{path}:{path.stat().st_mtime}"
        if key in self.seen:
            return
        try:
            text = path.read_text(errors="ignore")
            from brain.extract import extract_metadata
            extraction = extract_metadata(text)
            device_id = path.parent.name
            source_id = str(path)
            ts = Path(path).stat().st_mtime_ns
            import datetime
            ts_iso = datetime.datetime.utcfromtimestamp(ts / 1e9).isoformat()
            fid = fingerprint("device", source_id, text, ts_iso)
            if self.store.exists(fid):
                self.seen.add(key)
                self._save_seen()
                return
            base_tags = ["device", device_id] + extraction.get("tags", [])[:5]
            chunks = chunk_document(text, metadata={"device": device_id, "path": str(path), "entities": extraction.get("entities", [])})
            emb = get_embedding(text[:4000])
            added = 0
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                self.store.add(cid, "device", source_id, ts_iso, chunk["text"], base_tags, {"device": device_id, "path": str(path), "entities": extraction.get("entities", [])}, emb)
                added += 1
            self.seen.add(key)
            self._save_seen()
            if added:
                print(f"Inbox: processed {added} chunk(s) from {path}")
        except Exception as e:
            print(f"Inbox: failed {path}: {e}")

    def _save_seen(self):
        Path("/data/second-brain/processed.json").write_text(json.dumps(list(self.seen)))


def start_inbox_watcher(store):
    LIGHTSPEED_INBOX.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    handler = InboxHandler(store)
    observer.schedule(handler, str(LIGHTSPEED_INBOX), recursive=True)
    observer.start()
    return observer
