import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from jarvis.store import fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document

CALENDAR_PATHS = [
    Path.home() / "Library" / "Calendars",
]


def sync_calendar(store):
    count = 0
    for cal_dir in CALENDAR_PATHS:
        if not cal_dir.exists():
            continue
        for cal_db in cal_dir.rglob("*.caldav/calendar-data/*.sqlite"):
            try:
                conn = sqlite3.connect(f"file:{cal_db}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT summary, start_date, end_date, location FROM events WHERE start_date IS NOT NULL ORDER BY start_date DESC LIMIT 200")
                rows = cur.fetchall()
                conn.close()
                for row in rows:
                    text = f"{row['summary'] or ''}\n{row['start_date'] or ''} → {row['end_date'] or ''}\n{row['location'] or ''}"
                    source = "calendar"
                    source_id = f"{cal_db}:{row['summary']}"
                    ts = row["start_date"] or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    fid = fingerprint(source, source_id, text, ts)
                    if store.exists(fid):
                        continue
                    emb = get_embedding(text[:4000])
                    chunks = chunk_document(text, metadata={"calendar": str(cal_db)})
                    for i, chunk in enumerate(chunks):
                        cid = f"{fid}-{i}"
                        store.add(cid, source, source_id, ts, chunk["text"], ["calendar"], {"path": str(cal_db)}, emb)
                        count += 1
            except Exception:
                pass
    return count

