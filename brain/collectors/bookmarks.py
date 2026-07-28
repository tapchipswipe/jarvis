import plistlib
from pathlib import Path
from datetime import datetime
from brain.store import fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document

BOOKMARK_PATHS = [
    Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Bookmarks",
    Path.home() / "Library" / "Safari" / "Bookmarks.plist",
]


def sync_bookmarks(store):
    count = 0
    for bm_path in BOOKMARK_PATHS:
        if not bm_path.exists():
            continue
        try:
            bookmarks = []
            if bm_path.suffix == ".plist":
                data = plistlib.loads(bm_path.read_bytes())
                bookmarks = _walk_safari(data)
            elif bm_path.name == "Bookmarks":
                bookmarks = _walk_chrome(bm_path)
            for bm in bookmarks:
                text = f"{bm.get('name', '')} | {bm.get('url', '')}"
                source = "bookmark"
                source_id = bm.get("url", "")
                ts = bm.get("date", datetime.utcnow().isoformat())
                fid = fingerprint(source, source_id, text, ts)
                if store.exists(fid):
                    continue
                emb = get_embedding(text[:4000])
                chunks = chunk_document(text, metadata={"url": bm.get("url", "")})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["bookmark"], {"url": bm.get("url", "")}, emb)
                    count += 1
        except Exception:
            pass
    return count


def _walk_safari(node):
    results = []
    for child in node.get("Children", []):
        if "URLString" in child:
            results.append({"name": child.get("Name", ""), "url": child.get("URLString", ""), "date": str(child.get("URIDictionary", {}).get("lastVisitedDate", ""))})
        results.extend(_walk_safari(child))
    return results


def _walk_chrome(bm_path):
    import sqlite3
    results = []
    try:
        conn = sqlite3.connect(f"file:{bm_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT name, url, date_created FROM bookmarks WHERE url IS NOT NULL ORDER BY date_created DESC LIMIT 500")
        rows = cur.fetchall()
        conn.close()
        for row in rows:
            results.append({"name": row["name"], "url": row["url"], "date": row["date_created"]})
    except Exception:
        pass
    return results
