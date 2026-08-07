import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

NOTES_PATHS = [
    Path.home() / "Library" / "Group Containers" / "group.com.apple.notes" / "NoteStorage.sqlite",
]


def _copy_locked_db(db_path: Path) -> Path | None:
    tmp = Path("/tmp") / f"notestore_{db_path.stat().st_mtime}_{db_path.name}"
    try:
        shutil.copy2(db_path, tmp)
        return tmp
    except Exception:
        return None


def sync_notes(store):
    count = 0
    for db_path in NOTES_PATHS:
        if not db_path.exists():
            continue
        tmp_path = _copy_locked_db(db_path)
        if not tmp_path:
            continue
        try:
            conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.execute("""
                SELECT
                    n.Z_PK as pk,
                    n.ZTITLE1 as title,
                    n.ZSNIPPET as snippet,
                    n.ZMODIFICATIONDATE1 as mod_date,
                    n.ZICCLOUDSYNCINGOBJECT as folder_id,
                    f.ZTITLE2 as folder,
                    n.ZTEXT1 as body
                FROM ZICCLOUDSYNCINGOBJECT n
                LEFT JOIN ZICCLOUDSYNCINGOBJECT f ON n.ZFOLDER = f.Z_PK
                WHERE n.ZTITLE1 IS NOT NULL OR n.ZSNIPPET IS NOT NULL OR n.ZTEXT1 IS NOT NULL
                ORDER BY n.ZMODIFICATIONDATE1 DESC
                LIMIT 2000
            """)
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                title = row["title"] or ""
                snippet = row["snippet"] or ""
                body = row["body"] or ""
                folder = row["folder"] or ""
                mod_date = row["mod_date"]
                if mod_date:
                    try:
                        ts = datetime.fromtimestamp(mod_date + 978307200).isoformat()
                    except Exception:
                        ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                else:
                    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                text = f"{'[' + folder + '] ' if folder else ''}{title}\n{snippet}\n{body}"
                if len(text.strip()) < 10:
                    continue
                source = "notes"
                source_id = f"note:{row['pk']}"
                fid = fingerprint(source, source_id, text, ts)
                if store.exists(fid):
                    continue
                emb = get_embedding(text[:4000])
                chunks = chunk_document(text, metadata={"folder": folder, "db": str(db_path)})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["notes"], {"folder": folder, "db": str(db_path)}, emb)
                    count += 1
        except Exception as e:
            print(f"notes error: {e}")
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
    return count

