import subprocess
import sqlite3
from pathlib import Path
from datetime import datetime
from brain.store import fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document

GIT_DIRS = [
    Path.home() / "code",
    Path.home() / "Projects",
    Path.home() / "projects",
    Path.home() / "repos",
]


def sync_git(store):
    count = 0
    for git_dir in GIT_DIRS:
        if not git_dir.exists():
            continue
        for repo in git_dir.rglob(".git"):
            if not repo.is_dir():
                continue
            git_dir_path = repo.parent
            try:
                result = subprocess.run(["git", "-C", str(git_dir_path), "log", "--format=%H|%ai|%an|%s", "--all", "-n", "200"], capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    continue
                for line in result.stdout.splitlines():
                    parts = line.split("|", 3)
                    if len(parts) < 4:
                        continue
                    commit_hash, date, author, subject = parts
                    text = f"Commit: {subject}\nAuthor: {author}\nDate: {date}\nHash: {commit_hash}"
                    source = "git"
                    source_id = f"{git_dir_path}:{commit_hash}"
                    ts = date
                    fid = fingerprint(source, source_id, text, ts)
                    if store.exists(fid):
                        continue
                    emb = get_embedding(text[:4000])
                    chunks = chunk_document(text, metadata={"repo": str(git_dir_path)})
                    for i, chunk in enumerate(chunks):
                        cid = f"{fid}-{i}"
                        store.add(cid, source, source_id, ts, chunk["text"], ["git"], {"repo": str(git_dir_path)}, emb)
                        count += 1
            except Exception:
                pass
    return count
