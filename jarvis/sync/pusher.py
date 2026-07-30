import warnings
import logging

warnings.warn(
    "jarvis.sync.pusher is deprecated; use jarvis.sync.daemon for ingestion.",
    DeprecationWarning,
    stacklevel=2,
)
logging.getLogger(__name__).warning("pusher module is deprecated; use the sync daemon instead")

from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from jarvis.sync.push import push_file, ensure_remote_dir

WATCH_DIRS = [
    Path.home() / "Documents",
    Path.home() / "notes",
    Path.home() / "obsidian",
]

PUSH_TRACKER = Path.home() / ".config" / "jarvis" / "pushed.json"


class PushHandler(FileSystemEventHandler):
    def __init__(self, extensions=None):
        self.extensions = extensions or {".md", ".txt", ".json", ".csv"}
        self.pushed = set()
        if PUSH_TRACKER.exists():
            import json
            self.pushed = set(json.loads(PUSH_TRACKER.read_text()))

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in self.extensions:
            return
        key = f"{path}:{path.stat().st_mtime}"
        if key in self.pushed:
            return
        try:
            ensure_remote_dir()
            remote = push_file(path)
            if remote:
                self.pushed.add(key)
                PUSH_TRACKER.parent.mkdir(parents=True, exist_ok=True)
                PUSH_TRACKER.write_text(json.dumps(list(self.pushed)))
                print(f"Pushed {path} -> {remote}")
        except Exception as e:
            print(f"Push failed for {path}: {e}")


def start_pusher():
    observer = Observer()
    handler = PushHandler()
    for d in WATCH_DIRS:
        if d.exists():
            observer.schedule(handler, str(d), recursive=True)
    observer.start()
    return observer
