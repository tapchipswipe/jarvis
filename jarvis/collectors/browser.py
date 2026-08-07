import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

HISTORY_PATHS = [
    ("chrome", Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History"),
    ("safari", Path.home() / "Library" / "Safari" / "History.db"),
    ("firefox", Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"),
]


def _read_chrome(store, db_path: Path, days_back: int):
    count = 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("""
            SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch', 'localtime') as ts
            FROM urls
            WHERE last_visit_time > ?
            ORDER BY last_visit_time DESC
            LIMIT 2000
        """, (datetime.now(timezone.utc).timestamp() - days_back * 86400,))
        rows = cur.fetchall()
        conn.close()
        for row in rows:
            url = row["url"]
            text = f"{row['title'] or ''} | {url}"
            source = "browser"
            source_id = url
            ts = row["ts"] or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            fid = fingerprint(source, source_id, text, ts)
            if store.exists(fid):
                continue
            from jarvis.extract import extract_metadata
            extraction = extract_metadata(text)
            base_tags = ["browser", "chrome"] + extraction.get("tags", [])[:5]
            chunks = chunk_document(text, metadata={"url": url, "domain": url.split("/")[2] if "://" in url else url, "entities": extraction.get("entities", [])})
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                emb = get_embedding(chunk["text"])
                store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"url": url, "entities": extraction.get("entities", [])}, emb)
                count += 1
    except Exception as e:
        print(f"browser chrome error: {e}")
    return count


def _read_safari(store, db_path: Path, days_back: int):
    count = 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("""
            SELECT url, title, datetime(visit_time/1000000-11644473600, 'unixepoch', 'localtime') as ts
            FROM history_views, history_items
            WHERE history_items.id = history_views.history_id
            AND visit_time > ?
            ORDER BY visit_time DESC
            LIMIT 2000
        """, (datetime.now(timezone.utc).timestamp() - days_back * 86400,))
        rows = cur.fetchall()
        conn.close()
        for row in rows:
            url = row["url"]
            text = f"{row['title'] or ''} | {url}"
            source = "browser"
            source_id = url
            ts = row["ts"] or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            fid = fingerprint(source, source_id, text, ts)
            if store.exists(fid):
                continue
            from jarvis.extract import extract_metadata
            extraction = extract_metadata(text)
            base_tags = ["browser", "safari"] + extraction.get("tags", [])[:5]
            chunks = chunk_document(text, metadata={"url": url, "domain": url.split("/")[2] if "://" in url else url, "entities": extraction.get("entities", [])})
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                emb = get_embedding(chunk["text"])
                store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"url": url, "entities": extraction.get("entities", [])}, emb)
                count += 1
    except Exception as e:
        print(f"browser safari error: {e}")
    return count


def _read_firefox(store, profiles_dir: Path, days_back: int):
    count = 0
    if not profiles_dir.exists():
        return 0
    for profile_dir in profiles_dir.iterdir():
        places = profile_dir / "places.sqlite"
        if not places.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{places}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.execute("""
                SELECT p.url, p.title, datetime(v.visit_date/1000000-11644473600, 'unixepoch', 'localtime') as ts
                FROM moz_places p
                JOIN moz_historyvisits v ON p.id = v.place_id
                WHERE v.visit_date > ?
                ORDER BY v.visit_date DESC
                LIMIT 2000
            """, (datetime.now(timezone.utc).timestamp() - days_back * 86400 * 1000000,))
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                url = row["url"]
                text = f"{row['title'] or ''} | {url}"
                source = "browser"
                source_id = url
                ts = row["ts"] or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                fid = fingerprint(source, source_id, text, ts)
                if store.exists(fid):
                    continue
                from jarvis.extract import extract_metadata
                extraction = extract_metadata(text)
                base_tags = ["browser", "firefox"] + extraction.get("tags", [])[:5]
                chunks = chunk_document(text, metadata={"url": url, "domain": url.split("/")[2] if "://" in url else url, "entities": extraction.get("entities", [])})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    emb = get_embedding(chunk["text"])
                    store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"url": url, "entities": extraction.get("entities", [])}, emb)
                    count += 1
        except Exception as e:
            print(f"browser firefox error: {e}")
    return count


_READERS = {
    "chrome": _read_chrome,
    "safari": _read_safari,
    "firefox": _read_firefox,
}


def read_browser_history(store, days_back=7):
    count = 0
    for key, db_path in HISTORY_PATHS:
        if not db_path.exists():
            continue
        reader = _READERS.get(key)
        if reader:
            count += reader(store, db_path, days_back)
    return count

