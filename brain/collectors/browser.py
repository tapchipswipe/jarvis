import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from brain.store import fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document

AI_DOMAINS = {"chatgpt.com", "claude.ai", "gemini.google.com", "perplexity.ai", "kilocode.ai", "openai.com", "anthropic.com"}

HISTORY_PATHS = [
    Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History",
    Path.home() / "Library" / "Safari" / "History.db",
]


def read_browser_history(store, days_back=7):
    for db_path in HISTORY_PATHS:
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            if db_path.name == "History.db":
                cur = conn.execute("SELECT url, title, datetime(visit_time/1000000-11644473600, 'unixepoch', 'localtime') as ts FROM history_views, history_items WHERE history_items.id = history_views.history_id AND visit_time > ? ORDER BY visit_time DESC LIMIT 500", (datetime.now(timezone.utc).timestamp() - days_back * 86400,))
            else:
                cur = conn.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch', 'localtime') as ts FROM urls WHERE last_visit_time > ? ORDER BY last_visit_time DESC LIMIT 500", (datetime.now(timezone.utc).timestamp() - days_back * 86400,))
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                url = row["url"]
                if not any(d in url for d in AI_DOMAINS):
                    continue
                text = f"{row['title'] or ''} | {url}"
                source = "browser"
                source_id = url
                ts = row["ts"] or datetime.utcnow().isoformat()
                fid = fingerprint(source, source_id, text, ts)
                if store.exists(fid):
                    continue
                from brain.extract import extract_metadata
                extraction = extract_metadata(text)
                base_tags = ["browser"] + extraction.get("tags", [])[:5]
                emb = get_embedding(text[:4000])
                chunks = chunk_document(text, metadata={"url": url, "domain": url.split("/")[2] if "://" in url else url, "entities": extraction.get("entities", [])})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"url": url, "entities": extraction.get("entities", [])}, emb)
        except Exception:
            pass
