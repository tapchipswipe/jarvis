"""jarvis/backup.py — crash-consistent snapshot of a Jarvis store.

Storage model (single-brain box):
  <data_root>/data/meta.db             SQLite — canonical memories/points
  <data_root>/data/embed_cache.db      SQLite — embedding cache (optional)
  <data_root>/data/chroma/chroma.sqlite3 + <collection>/HNSW bins   — vector index

All SQLite files are snapshotted with the SQLite *online-backup* API, which is
guaranteed internally consistent even while the live server is writing — no
server pause required for those. Chroma's HNSW index binaries (data_level0.bin
etc.) are plain files written by Chroma and cannot be captured point-in-time
while writes are active; snapshot_store() copies them best-effort. For a fully
strict snapshot (consistent HNSW too) pause the server during the maintenance
window — the shell wrapper offers a --strict mode that briefly stops the
scheduled task, snapshots, then restarts it so a torn HNSW is impossible.
"""
from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

# (relative-to-data-root SQLite file) -- all crash-consistent via online backup
_SQLITE_FILES = ("meta.db", "embed_cache.db", "chroma/chroma.sqlite3")
# Chroma HNSW index extension dirs to carry over (best-effort unless strict)
_CHROMA_HNSW_EXTS = {".bin", ".pickle"}


def _online_backup(src: Path, dst: Path) -> int:
    """Atomic, crash-consistent copy of a live SQLite DB via the backup API."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_con = sqlite3.connect(str(dst))
    try:
        src_con.backup(dst_con)
        dst_con.commit()
    finally:
        dst_con.close()
        src_con.close()
    return dst.stat().st_size


def snapshot_store(data_root: Path, dst: Path, strict: bool = False) -> dict:
    """Snapshot the Jarvis store under *data_root* into *dst*.

    Returns a summary dict {files, bytes, sqlite_backed, hnsv_copied, strict,
    duration_sec}. Never touches/opens the live Chroma: HNSW binaries are file-
    copied only. *strict* is advisory here (recorded) — the actual pause/restart
    is orchestrated by the shell wrapper so a strictly-torn HNSW is impossible.
    """
    data_root = Path(data_root)
    dst = Path(dst)
    t0 = time.time()
    dst.mkdir(parents=True, exist_ok=True)

    backed: list[str] = []
    hnsv_copied: list[str] = []

    # 1. Chroma dir first (raw copy of HNSW bins + any sqlite).
    chroma_src = data_root / "chroma"
    if chroma_src.is_dir():
        _copy_chroma_tree(chroma_src, dst / "chroma")
        hnsv_copied = [
            str(p.relative_to(chroma_src))
            for p in chroma_src.rglob("*")
            if p.is_file() and p.suffix in _CHROMA_HNSW_EXTS
        ]

    # 2. SQLite files via online backup (overwrites any raw chroma.sqlite3 with
    #    the consistent copy).
    for rel in _SQLITE_FILES:
        src = data_root / rel
        if src.is_file():
            _online_backup(src, dst / rel)
            backed.append(rel)

    # 3. Any other non-sqlite files at the data root (e.g. misc config/json).
    for p in data_root.iterdir():
        if p.is_file() and not p.name.endswith((".db", ".sqlite3", ".db-wal",
                                                ".db-shm")):
            try:
                shutil.copy2(p, dst / p.name)
            except OSError:
                pass

    total_bytes = sum(
        p.stat().st_size for p in dst.rglob("*") if p.is_file())
    return {
        "files": len(backed) + len(hnsv_copied),
        "bytes": total_bytes,
        "sqlite_backed": backed,
        "hnsv_copied": len(hnsv_copied),
        "strict": bool(strict),
        "duration_sec": round(time.time() - t0, 2),
    }


def _copy_chroma_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.db-wal", "*.db-shm"))
