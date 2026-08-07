import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

MAIL_PATHS = [
    Path.home() / "Library" / "Mail",
]


def sync_email(store):
    count = 0
    for mail_dir in MAIL_PATHS:
        if not mail_dir.exists():
            continue
        for mail_db in mail_dir.rglob("Mail*.sqlite"):
            try:
                conn = sqlite3.connect(f"file:{mail_db}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT subject, sender, date_received, body FROM messages WHERE date_received IS NOT NULL ORDER BY date_received DESC LIMIT 200")
                rows = cur.fetchall()
                conn.close()
                for row in rows:
                    text = f"Subject: {row['subject'] or ''}\nFrom: {row['sender'] or ''}\nDate: {row['date_received'] or ''}\n\n{row['body'] or ''}"
                    source = "email"
                    source_id = f"{mail_db}:{row['subject']}"
                    ts = row["date_received"] or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    fid = fingerprint(source, source_id, text, ts)
                    if store.exists(fid):
                        continue
                    emb = get_embedding(text[:4000])
                    chunks = chunk_document(text, metadata={"mail_db": str(mail_db)})
                    for i, chunk in enumerate(chunks):
                        cid = f"{fid}-{i}"
                        store.add(cid, source, source_id, ts, chunk["text"], ["email"], {"path": str(mail_db)}, emb)
                        count += 1
            except Exception:
                pass
    return count

