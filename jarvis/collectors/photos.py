import json
import subprocess
from datetime import datetime
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

PHOTO_DIRS = [
    Path.home() / "Pictures",
    Path.home() / "Photos",
]
OCR_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _has_tesseract() -> bool:
    try:
        return subprocess.run(["which", "tesseract"], capture_output=True, text=True, check=False).returncode == 0
    except Exception:
        return False


def _has_exiftool() -> bool:
    try:
        return subprocess.run(["which", "exiftool"], capture_output=True, text=True, check=False).returncode == 0
    except Exception:
        return False


def _ocr_image(path: Path) -> str | None:
    try:
        result = subprocess.run(["tesseract", str(path), "stdout"], capture_output=True, text=True, timeout=30, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def sync_photos(store):
    count = 0
    if not _has_exiftool():
        # exiftool is required to read photo metadata; skip quietly rather than
        # erroring on every image.
        print("photos: skipping (exiftool not installed)")
        return count
    for photo_dir in PHOTO_DIRS:
        if not photo_dir.exists():
            continue
        for ext in ["*.jpg", "*.jpeg", "*.png", "*.heic", "*.raw", "*.dng"]:
            for photo_path in photo_dir.rglob(ext):
                try:
                    result = subprocess.run(["exiftool", "-json", "-datecreated", "-gpslatitude", "-gpslongitude", "-title", "-description", str(photo_path)], capture_output=True, text=True, timeout=10, check=False)
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
                    if photo_path.suffix.lower() in OCR_EXTENSIONS and _has_tesseract():
                        ocr_text = _ocr_image(photo_path)
                        if ocr_text:
                            ocr_source = "photos_ocr"
                            ocr_id = f"ocr:{photo_path}"
                            ocr_ts = datetime.utcfromtimestamp(photo_path.stat().st_mtime).isoformat()
                            ocr_fid = fingerprint(ocr_source, ocr_id, ocr_text, ocr_ts)
                            if not store.exists(ocr_fid):
                                ocr_emb = get_embedding(ocr_text[:4000])
                                ocr_chunks = chunk_document(ocr_text, metadata={"path": str(photo_path)})
                                for i, chunk in enumerate(ocr_chunks):
                                    ocr_cid = f"{ocr_fid}-{i}"
                                    store.add(ocr_cid, ocr_source, ocr_id, ocr_ts, chunk["text"], ["photo", "ocr"], {"path": str(photo_path)}, ocr_emb)
                                    count += 1
                except Exception as e:
                    print(f"photos error: {e}")
    return count
