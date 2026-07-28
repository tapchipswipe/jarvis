import json
from pathlib import Path
from datetime import datetime
from brain.store import Store, fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document


def ingest_gemini_takeout(zip_path: Path, store: Store):
    count = 0
    import zipfile, tempfile
    if not zip_path.exists():
        return 0
    extract_dir = Path(tempfile.mkdtemp()) / "gemini_takeout"
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(extract_dir)
    json_files = list(extract_dir.rglob("conversations.json"))
    if not json_files:
        json_files = list(extract_dir.rglob("*.json"))
    for jf in json_files:
        try:
            data = json.loads(jf.read_text(errors="ignore"))
            conversations = data if isinstance(data, list) else data.get("conversations", [])
            for convo in conversations:
                ts = convo.get("timestamp", datetime.utcnow().isoformat())
                text = json.dumps(convo, ensure_ascii=False)
                source = "ai_gemini"
                source_id = convo.get("id", jf.name)
                fid = fingerprint(source, source_id, text, ts)
                if store.exists(fid):
                    continue
                emb = get_embedding(text[:4000])
                chunks = chunk_document(text, metadata={"source_file": str(jf)})
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], ["ai", "gemini"], {"file": str(jf)}, emb)
                    count += 1
        except Exception:
            pass
    return count


def run_sync(target: str = "all"):
    store = Store()
    results = {}
    if target in ("all", "files"):
        try:
            from brain.collectors import files, shell, browser, kilo
            from brain.collectors.files import start_watcher
            observer = start_watcher(store)
            observer.join(timeout=5)
        except Exception as e:
            results["files"] = f"error: {e}"
    if target in ("all", "kilo"):
        try:
            from brain.collectors import kilo
            c = kilo.ingest_kilo_sessions(store)
            results["ai_kilo"] = c
        except Exception as e:
            results["ai_kilo"] = f"error: {e}"
    if target in ("all", "browser"):
        try:
            from brain.collectors import browser
            browser.read_browser_history(store, days_back=7)
            results["browser"] = "ok"
        except Exception as e:
            results["browser"] = f"error: {e}"
    if target in ("all", "gemini"):
        downloads = Path.home() / "Downloads"
        takeouts = sorted(downloads.glob("takeout-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if takeouts:
            c = ingest_gemini_takeout(takeouts[0], store)
            results["ai_gemini"] = c
        else:
            results["ai_gemini"] = "no_takeout"
    if target in ("all", "calendar"):
        try:
            from brain.collectors import calendar
            c = calendar.sync_calendar(store)
            results["calendar"] = c
        except Exception as e:
            results["calendar"] = f"error: {e}"
    if target in ("all", "email"):
        try:
            from brain.collectors import email
            c = email.sync_email(store)
            results["email"] = c
        except Exception as e:
            results["email"] = f"error: {e}"
    if target in ("all", "photos"):
        try:
            from brain.collectors import photos
            c = photos.sync_photos(store)
            results["photos"] = c
        except Exception as e:
            results["photos"] = f"error: {e}"
    if target in ("all", "bookmarks"):
        try:
            from brain.collectors import bookmarks
            c = bookmarks.sync_bookmarks(store)
            results["bookmarks"] = c
        except Exception as e:
            results["bookmarks"] = f"error: {e}"
    if target in ("all", "rss"):
        try:
            from brain.collectors import rss
            c = rss.sync_rss(store)
            results["rss"] = c
        except Exception as e:
            results["rss"] = f"error: {e}"
    if target in ("all", "system"):
        try:
            from brain.collectors import system
            c = system.sync_system(store)
            results["system"] = c
        except Exception as e:
            results["system"] = f"error: {e}"
    if target in ("all", "deep"):
        try:
            from brain.collectors import deep
            c = deep.sync_deep(store)
            results["deep"] = c
        except Exception as e:
            results["deep"] = f"error: {e}"
    if target in ("all", "git"):
        try:
            from brain.collectors import git
            c = git.sync_git(store)
            results["git"] = c
        except Exception as e:
            results["git"] = f"error: {e}"
    store.close()
    return results