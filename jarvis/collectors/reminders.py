import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

REMINDERS_PATHS = [
    Path.home() / "Library" / "Calendars",
    Path.home() / "Library" / "Reminders",
]


def sync_reminders(store):
    count = 0
    for base_dir in REMINDERS_PATHS:
        if not base_dir.exists():
            continue
        for rem_db in base_dir.rglob("*.sqlite"):
            if "Reminders" not in rem_db.name and "reminders" not in rem_db.name:
                continue
            try:
                conn = sqlite3.connect(f"file:{rem_db}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                cur = conn.execute("""
                    SELECT
                        ZTITLE as title,
                        ZNOTES as notes,
                        ZDUEDATE as due_date,
                        ZCOMPLETED as completed,
                        Z_CREATIONDATE as creation_date
                    FROM ZREMINDER
                    WHERE ZTITLE IS NOT NULL
                    ORDER BY Z_CREATIONDATE DESC
                    LIMIT 2000
                """)
                rows = cur.fetchall()
                conn.close()
                for row in rows:
                    title = row["title"] or ""
                    notes = row["notes"] or ""
                    due = row["due_date"]
                    completed = bool(row["completed"])
                    creation = row["creation_date"]
                    if creation:
                        try:
                            ts = datetime.fromtimestamp(creation + 978307200).isoformat()
                        except Exception:
                            ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    else:
                        ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    status = "completed" if completed else "open"
                    due_str = ""
                    if due:
                        try:
                            due_str = datetime.fromtimestamp(due + 978307200).isoformat()
                        except Exception:
                            due_str = str(due)
                    text = f"[{status}] {title}\nDue: {due_str}\n{notes}"
                    if len(text.strip()) < 3:
                        continue
                    source = "reminders"
                    source_id = f"reminder:{title}:{ts}"
                    fid = fingerprint(source, source_id, text, ts)
                    if store.exists(fid):
                        continue
                    emb = get_embedding(text[:4000])
                    chunks = chunk_document(text, metadata={"due": due_str, "completed": completed, "db": str(rem_db)})
                    for i, chunk in enumerate(chunks):
                        cid = f"{fid}-{i}"
                        store.add(cid, source, source_id, ts, chunk["text"], ["reminders"], {"due": due_str, "completed": completed, "db": str(rem_db)}, emb)
                        count += 1
            except Exception as e:
                print(f"reminders error: {e}")
    return count

