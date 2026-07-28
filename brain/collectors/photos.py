import subprocess
import json
from pathlib import Path
from datetime import datetime
from brain.store import fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document

PHOTO_DIRS = [
    Path.home() / "Pictures",
    Path.home() / "Photos",
]


def sync_photos(store):
    count = 0
    for photo_dir in PHOTO_DIRS:
        if not photo_dir.exists():
            continue
        for ext in ["*.jpg", "*.jpeg", "*.png", "*.heic", "*.raw", "*.dng"]:
            for photo_path in photo_dir.rglob(ext):
                try:
                    result = subprocess.run(["exiftool", "-json", "-datecreated", "-gpslatitude", "-gpslongitude", "-title", "-description", str(photo_path)], capture_output=True, text=True, timeout=10)
                    if result.returncode != 0:
                        continue
                    exif = json.loads(result.stdout)[0] if result.stdout.strip() else {}
                    text = f"Photo: {photo_path.name}\nDate: {exif.get('DateCreated', '')}\nTitle: {exif.get('Title', '')}\nDescription: {exif.get('Description', '')}\nGPS: {exif.get('GPSLatitude', '')}, {exif.get('GPSLongitude', '')}"
                    source = "photo"
                    source_id = str(photo_path)
                    ts = exif.get("DateCreated", datetime.utcfromtimestamp(photo_path.stat().st_mtime).isoformat())
                    fid = fingerprint(source, source_id, text, ts)
                    if store.exists(fid):
                        continue
                    emb = get_embedding(text[:4000])
                    chunks = chunk_document(text, metadata={"path": str(photo_path)})
                    for i, chunk in enumerate(chunks):
                        cid = f"{fid}-{i}"
                        store.add(cid, source, source_id, ts, chunk["text"], ["photo"], {"path": str(photo_path)}, emb)
                        count += 1
                except Exception:
                    pass
    return count
