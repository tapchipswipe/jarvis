"""jarvis/collectors/thin.py — thin-client ambient collector (files -> outbox -> server).

In FULL-THIN the Mac is a client: it must *never* write a local brain. Collectors
enqueue raw text into the disposable outbox (``~/.cache/jarvis/cache.db``), and
a ``flush`` pushes the backlog to the Lightspeed ``jarvis server`` over
``/api/remember``. The server is the single writer; store.add() is content-hash
idempotent, so re-scanning a file that already exists on the box is a no-op there.

Why bounded & throttled-safe:
  * Reads eligible text files in the (small) user-authored roots below.
  * An unchanged-file fingerprint (mtime|size) is remembered in the outbox kv, so
    re-scans skip files cheaply instead of re-reading them.
  * ``cache.enqueue`` is content-hash idempotent, so duplicates never bloat the
    backlog even if the fingerprint is lost.
This module never opens a Store/Chroma handle — client only.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

COLLECT_DIRS = [
    Path.home() / "Documents",
    Path.home() / "notes",
    Path.home() / "obsidian",
]
COLLECT_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".xml", ".html", ".log",
                      ".yaml", ".yml", ".toml", ".rst", ".org"}
COLLECT_EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                        "Cache", "Caches", "tmp", "VirtualBox VMs", ".Trash"}


def _should_exclude(path: Path) -> bool:
    parts = path.parts
    return any(excl in parts for excl in COLLECT_EXCLUDE_DIRS)


def _fingerprint(path: Path) -> str:
    st = path.stat()
    return hashlib.sha256(f"{path}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()


_WALK_MAX_ERRORS = 1024


def _walk(roots, extensions, max_files):
    # A single bad path (broken symlink, permission-bounced dir, a file that
    # disappears mid-walk) must NOT abort the whole scan. Guard both the per-path
    # checks and the rglob iteration itself, skipping offenders with a bounded
    # error counter so a persistently-unreadable subtree can't spin forever.
    seen_any = set()
    errors = 0
    for base in roots:
        if not base.exists():
            continue
        try:
            for path in base.rglob("*"):
                if errors >= _WALK_MAX_ERRORS:
                    return
                try:
                    if len(seen_any) >= max_files:
                        return
                    if path.is_dir() or _should_exclude(path):
                        continue
                    if path.suffix.lower() not in extensions:
                        continue
                    seen_any.add(str(path))
                    yield path
                except OSError:
                    # per-file failure (e.g. broken symlink / unreadable path)
                    errors += 1
                    continue
        except OSError:
            # rglob itself bailed on an unreadable subdir; move to next root
            errors += 1
            continue


def scan_once(roots=None, extensions=None, max_files: int = 2000, marker_prefix: str = "seen") -> dict:
    """Walk eligible files and enqueue their text into the outbox (client mode).

    Returns stats: {files, enqueued, dups, blank, skipped_seen, errors}. Idempotent:
    unchanged files are skipped via the fingerprint marker, and equal content is not
    re-enqueued. Opens the disposable cache only — never a Store.
    """
    from jarvis.cache import Cache

    roots = [Path(r) for r in roots] if roots else COLLECT_DIRS
    extensions = set(extensions) if extensions else COLLECT_EXTENSIONS
    stats = {"files": 0, "enqueued": 0, "dups": 0, "blank": 0,
             "skipped_seen": 0, "errors": 0}
    cache = Cache()
    try:
        for path in _walk(roots, extensions, max_files):
            stats["files"] += 1
            try:
                fp_key = f"{marker_prefix}:{_fingerprint(path)}"
                if cache.get_kv(fp_key):
                    stats["skipped_seen"] += 1
                    continue
                text = path.read_text(errors="ignore")
                if not text.strip():
                    stats["blank"] += 1
                else:
                    ok = cache.enqueue(text, source="file", tags=["file"],
                                       metadata={"path": str(path)})
                    stats["enqueued" if ok else "dups"] += 1
                cache.put_kv(fp_key, "1")
            except Exception:  # noqa: BLE001 - one bad file must not kill the scan
                stats["errors"] += 1
        cache.conn.commit()
        return stats
    finally:
        cache.close()


def flush_once(limit: int = 200) -> dict:
    """Push pending outbox items to the server. Returns {pushed, failed, offline}."""
    from jarvis.cache import Cache, flush_outbox

    cache = Cache()
    try:
        return flush_outbox(cache, limit=limit)
    finally:
        cache.close()


def run(max_files: int = 2000, flush: bool = True, limit: int = 200) -> dict:
    """scan_once + optional flush. Thin-client only (no Store/Chroma handles)."""
    results = scan_once(max_files=max_files)
    if flush:
        results["flush"] = flush_once(limit=limit)
    return results
