import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

CONTACTS_PATHS = [
    Path.home() / "Library" / "Application Support" / "AddressBook",
    Path.home() / "Library" / "Contacts",
]


def sync_contacts(store):
    count = 0
    for base_dir in CONTACTS_PATHS:
        if not base_dir.exists():
            continue
        for contact_db in base_dir.rglob("*.sqlite"):
            if "AddressBook" not in contact_db.name and "Contacts" not in contact_db.name:
                continue
            try:
                conn = sqlite3.connect(f"file:{contact_db}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                cur = conn.execute("""
                    SELECT
                        p.ZFIRSTNAME as first_name,
                        p.ZLASTNAME as last_name,
                        p.ZORGANIZATION as organization,
                        p.ZJOBTITLE as job_title,
                        p.ZCREATIONDATE as creation_date
                    FROM ZCONTACTPERSON p
                    ORDER BY p.ZCREATIONDATE DESC
                    LIMIT 2000
                """)
                rows = cur.fetchall()
                conn.close()
                for row in rows:
                    first = row["first_name"] or ""
                    last = row["last_name"] or ""
                    org = row["organization"] or ""
                    job = row["job_title"] or ""
                    creation = row["creation_date"]
                    if creation:
                        try:
                            ts = datetime.fromtimestamp(creation + 978307200).isoformat()
                        except Exception:
                            ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    else:
                        ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    name = f"{first} {last}".strip()
                    text = f"{name}\n{org}\n{job}"
                    if len(text.strip()) < 2:
                        continue
                    source = "contacts"
                    source_id = f"contact:{name}"
                    fid = fingerprint(source, source_id, text, ts)
                    if store.exists(fid):
                        continue
                    emb = get_embedding(text[:4000])
                    chunks = chunk_document(text, metadata={"name": name, "org": org, "db": str(contact_db)})
                    for i, chunk in enumerate(chunks):
                        cid = f"{fid}-{i}"
                        store.add(cid, source, source_id, ts, chunk["text"], ["contacts"], {"name": name, "org": org, "db": str(contact_db)}, emb)
                        count += 1
            except Exception as e:
                print(f"contacts error: {e}")
    return count

