import plistlib
from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

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
                ts = bm.get("date", datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
                fid = fingerprint(source, source_id, text, ts)
                if store.exists(fid):
                    continue
                chunks = chunk_document(text, metadata={"url": bm.get("url", "")})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    emb = get_embedding(chunk["text"])
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
    """Parse Chrome Bookmarks JSON file and extract bookmark entries."""
    import json
    results = []
    try:
        data = json.loads(bm_path.read_bytes())
        roots = data.get("roots", {})
        for root_key in ("bookmark_bar", "other", "synced"):
            root = roots.get(root_key, {})
            results.extend(_walk_chrome_children(root))
    except Exception:
        pass
    return results


def _walk_chrome_children(node):
    """Recursively walk Chrome bookmark tree nodes."""
    results = []
    children = node.get("children", [])
    for child in children:
        if child.get("type") == "url":
            url = child.get("url", "")
            name = child.get("name", "")
            date_added = child.get("date_added", "")
            results.append({"name": name, "url": url, "date": date_added})
        elif child.get("type") == "folder":
            results.extend(_walk_chrome_children(child))
    return results

