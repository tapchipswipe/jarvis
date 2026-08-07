import sqlite3
import shutil
from pathlib import Path
from datetime import datetime, timezone
from jarvis.store import fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"


def _copy_locked_db(db_path: Path) -> Path | None:
    tmp = Path("/tmp") / f"chat_{db_path.stat().st_mtime}_{db_path.name}"
    try:
        shutil.copy2(db_path, tmp)
        return tmp
    except Exception:
        return None


def sync_messages(store):
    count = 0
    if not CHAT_DB.exists():
        return 0
    tmp_path = _copy_locked_db(CHAT_DB)
    if not tmp_path:
        return 0
    try:
        conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("""
            SELECT
                m.Z_PK as pk,
                m.ZTEXT as text,
                m.ZDATE as date,
                m.ZFROMJID as from_jid,
                c.ZDISPLAYNAME as chat_name,
                c.Z_PK as chat_id
            FROM message m
            LEFT JOIN chat_message_join j ON m.Z_PK = j.Z_MESSAGE
            LEFT JOIN chat c ON j.Z_CHAT = c.Z_PK
            WHERE m.ZTEXT IS NOT NULL
            ORDER BY m.ZDATE DESC
            LIMIT 5000
        """)
        rows = cur.fetchall()
        conn.close()
        for row in rows:
            text = row["text"] or ""
            sender = row["from_jid"] or ""
            chat_name = row["chat_name"] or f"chat:{row['chat_id']}"
            date_val = row["date"]
            if date_val:
                try:
                    ts = datetime.fromtimestamp(date_val + 978307200).isoformat()
                except Exception:
                    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            else:
                ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            full_text = f"[{chat_name}] {sender}: {text}"
            if len(full_text.strip()) < 3:
                continue
            source = "messages"
            source_id = f"msg:{row['pk']}"
            fid = fingerprint(source, source_id, full_text, ts)
            if store.exists(fid):
                continue
            emb = get_embedding(full_text[:4000])
            chunks = chunk_document(full_text, metadata={"chat": chat_name, "sender": sender, "db": str(CHAT_DB)})
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                store.add(cid, source, source_id, ts, chunk["text"], ["messages"], {"chat": chat_name, "sender": sender, "db": str(CHAT_DB)}, emb)
                count += 1
    except Exception as e:
        print(f"messages error: {e}")
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
    return count

