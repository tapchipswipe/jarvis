import json
import os
import platform
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint


def _get_inbox_root() -> Path:
    """Return the inbox root path for the current platform.

    JARVIS_INBOX wins (per-profile / server override). Otherwise the platform
    default is used (Windows server: C:/data; POSIX server: /data).
    """
    env = os.environ.get("JARVIS_INBOX")
    if env:
        return Path(env)
    if platform.system() == "Windows":
        return Path("C:/data/jarvis/inbox")
    return Path("/data/jarvis/inbox")


def _get_processed_path() -> Path:
    """Path of the processed.json ledger, next to the inbox root."""
    env = os.environ.get("JARVIS_PROCESSED_PATH")
    if env:
        return Path(env)
    if platform.system() == "Windows":
        return Path("C:/data/jarvis/processed.json")
    return _get_inbox_root().parent / "processed.json"


LIGHTSPEED_INBOX = _get_inbox_root()
PROCESSED_PATH = _get_processed_path()


class InboxHandler(FileSystemEventHandler):
    def __init__(self, store):
        self.store = store
        self.seen = set()
        if PROCESSED_PATH.exists():
            self.seen = set(json.loads(PROCESSED_PATH.read_text()))

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() == ".json":
            return  # JSON sidecars are loaded lazily by _try_load_sidecar, never ingested as content
        if path.suffix.lower() not in {".md", ".txt", ".csv"}:
            return
        key = f"{path}:{path.stat().st_mtime}"
        if key in self.seen:
            return
        try:
            text = path.read_text(errors="ignore")
            sidecar = self._try_load_sidecar(path)
            route = sidecar.get("route", "unclassified") if isinstance(sidecar, dict) else "unclassified"
            tags = sidecar.get("tag_seeds", []) if isinstance(sidecar, dict) else []
            from jarvis.extract import extract_metadata
            extraction = extract_metadata(text)
            device_id = path.parent.name
            source_id = str(path)
            ts = Path(path).stat().st_mtime_ns
            import datetime
            ts_iso = datetime.datetime.fromtimestamp(ts / 1e9, datetime.timezone.utc).replace(tzinfo=None).isoformat()
            fid = fingerprint("device", source_id, text, ts_iso)
            if self.store.exists(fid):
                self.seen.add(key)
                self._save_seen()
                return
            base_tags = ["device", device_id] + tags + extraction.get("tags", [])[:5]
            meta = {"device": device_id, "path": str(path), "entities": extraction.get("entities", [])}
            if isinstance(sidecar, dict):
                meta["sidecar"] = sidecar
            chunks = chunk_document(text, metadata=meta)
            emb = get_embedding(text[:4000])
            added = 0
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                self.store.add(cid, "device", source_id, ts_iso, chunk["text"], base_tags, meta, emb, route=route)
                added += 1
            self.seen.add(key)
            self._save_seen()
            if added:
                print(f"Inbox: processed {added} chunk(s) from {path} [route={route}]")
        except Exception as e:
            print(f"Inbox: failed {path}: {e}")

    def _try_load_sidecar(self, path: Path) -> dict | None:
        sidecar_path = path.with_suffix(".json")
        if sidecar_path.exists() and sidecar_path.suffix == ".json":
            try:
                return json.loads(sidecar_path.read_text(errors="ignore"))
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def _save_seen(self):
        PROCESSED_PATH.write_text(json.dumps(list(self.seen)))


def start_inbox_watcher(store):
    LIGHTSPEED_INBOX.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    handler = InboxHandler(store)
    observer.schedule(handler, str(LIGHTSPEED_INBOX), recursive=True)
    observer.start()
    return observer
