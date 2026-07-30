import json
from pathlib import Path
from datetime import datetime
from jarvis.store import Store, fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document


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


def run_sync(target: str = "all", progress_callback=None):
    store = Store()
    results = {}
    if target in ("all", "files"):
        try:
            from jarvis.collectors import files, shell, browser, kilo
            from jarvis.collectors.files import start_watcher
            observer = start_watcher(store)
            observer.join(timeout=5)
            results["files"] = "ok"
        except Exception as e:
            results["files"] = f"error: {e}"
        if progress_callback:
            progress_callback("files", results.get("files"))
    if target in ("all", "kilo"):
        try:
            from jarvis.collectors import kilo
            c = kilo.ingest_kilo_sessions(store)
            results["ai_kilo"] = c
        except Exception as e:
            results["ai_kilo"] = f"error: {e}"
        if progress_callback:
            progress_callback("ai_kilo", results.get("ai_kilo"))
    if target in ("all", "browser"):
        try:
            from jarvis.collectors import browser
            c = browser.read_browser_history(store, days_back=7)
            results["browser"] = c
        except Exception as e:
            results["browser"] = f"error: {e}"
        if progress_callback:
            progress_callback("browser", results.get("browser"))
    if target in ("all", "gemini"):
        downloads = Path.home() / "Downloads"
        takeouts = sorted(downloads.glob("takeout-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if takeouts:
            c = ingest_gemini_takeout(takeouts[0], store)
            results["ai_gemini"] = c
        else:
            results["ai_gemini"] = "no_takeout"
        if progress_callback:
            progress_callback("ai_gemini", results.get("ai_gemini"))
    if target in ("all", "calendar"):
        try:
            from jarvis.collectors import calendar
            c = calendar.sync_calendar(store)
            results["calendar"] = c
        except Exception as e:
            results["calendar"] = f"error: {e}"
        if progress_callback:
            progress_callback("calendar", results.get("calendar"))
    if target in ("all", "email"):
        try:
            from jarvis.collectors import email
            c = email.sync_email(store)
            results["email"] = c
        except Exception as e:
            results["email"] = f"error: {e}"
        if progress_callback:
            progress_callback("email", results.get("email"))
    if target in ("all", "photos"):
        try:
            from jarvis.collectors import photos
            c = photos.sync_photos(store)
            results["photos"] = c
        except Exception as e:
            results["photos"] = f"error: {e}"
        if progress_callback:
            progress_callback("photos", results.get("photos"))
    if target in ("all", "bookmarks"):
        try:
            from jarvis.collectors import bookmarks
            c = bookmarks.sync_bookmarks(store)
            results["bookmarks"] = c
        except Exception as e:
            results["bookmarks"] = f"error: {e}"
        if progress_callback:
            progress_callback("bookmarks", results.get("bookmarks"))
    if target in ("all", "rss"):
        try:
            from jarvis.collectors import rss
            c = rss.sync_rss(store)
            results["rss"] = c
        except Exception as e:
            results["rss"] = f"error: {e}"
        if progress_callback:
            progress_callback("rss", results.get("rss"))
    if target in ("all", "system"):
        try:
            from jarvis.collectors import system
            c = system.sync_system(store)
            results["system"] = c
        except Exception as e:
            results["system"] = f"error: {e}"
        if progress_callback:
            progress_callback("system", results.get("system"))
    if target in ("all", "deep"):
        try:
            from jarvis.collectors import deep
            c = deep.sync_deep(store)
            results["deep"] = c
        except Exception as e:
            results["deep"] = f"error: {e}"
        if progress_callback:
            progress_callback("deep", results.get("deep"))
    if target in ("all", "git"):
        try:
            from jarvis.collectors import git
            c = git.sync_git(store)
            results["git"] = c
        except Exception as e:
            results["git"] = f"error: {e}"
        if progress_callback:
            progress_callback("git", results.get("git"))
    if target in ("all", "shell"):
        try:
            from jarvis.collectors import shell
            c = shell.sync_shell(store)
            results["shell"] = c
        except Exception as e:
            results["shell"] = f"error: {e}"
        if progress_callback:
            progress_callback("shell", results.get("shell"))
    if target in ("all", "notes"):
        try:
            from jarvis.collectors import notes
            c = notes.sync_notes(store)
            results["notes"] = c
        except Exception as e:
            results["notes"] = f"error: {e}"
        if progress_callback:
            progress_callback("notes", results.get("notes"))
    if target in ("all", "reminders"):
        try:
            from jarvis.collectors import reminders
            c = reminders.sync_reminders(store)
            results["reminders"] = c
        except Exception as e:
            results["reminders"] = f"error: {e}"
        if progress_callback:
            progress_callback("reminders", results.get("reminders"))
    if target in ("all", "contacts"):
        try:
            from jarvis.collectors import contacts
            c = contacts.sync_contacts(store)
            results["contacts"] = c
        except Exception as e:
            results["contacts"] = f"error: {e}"
        if progress_callback:
            progress_callback("contacts", results.get("contacts"))
    if target in ("all", "messages"):
        try:
            from jarvis.collectors import messages
            c = messages.sync_messages(store)
            results["messages"] = c
        except Exception as e:
            results["messages"] = f"error: {e}"
        if progress_callback:
            progress_callback("messages", results.get("messages"))
    if target in ("all", "photos_ocr"):
        try:
            from jarvis.collectors import photos
            c = photos.sync_photos(store)
            results["photos_ocr"] = c
        except Exception as e:
            results["photos_ocr"] = f"error: {e}"
        if progress_callback:
            progress_callback("photos_ocr", results.get("photos_ocr"))
    store.close()
    return results
