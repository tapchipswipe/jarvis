import os
import sys
import json
import time
import signal
import hashlib
import logging
import threading
import platform
from datetime import datetime, timezone
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import watchdog.observers
from watchdog.events import FileSystemEventHandler

from jarvis.store import Store, fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.classifier import classify, validate_envelope, apply_envelope
from jarvis.routes import ROUTE_TAG_MAP
from jarvis.device_id import get_device_id

try:
    from jarvis.triggers import TriggerEngine, load_triggers
except ImportError:
    TriggerEngine = None  # type: ignore[assignment,misc]
    load_triggers = None  # type: ignore[assignment]

SYSTEM = platform.system()
if SYSTEM == "Windows":
    DEFAULT_INBOX = Path("C:/data/jarvis/inbox")
    DEFAULT_LOG_DIR = Path("C:/data/jarvis/logs")
    DEFAULT_PID_FILE = Path("C:/data/jarvis/logs/daemon.pid")
else:
    DEFAULT_INBOX = Path("/data/jarvis/inbox")
    DEFAULT_LOG_DIR = Path("/data/jarvis/logs")
    DEFAULT_PID_FILE = Path("/tmp/jarvis-daemon.pid")

STATE_DIR = Path.home() / ".config" / "jarvis"
STATE_FILE = STATE_DIR / "daemon-state.json"
CONFIG_FILE = STATE_DIR / "config.toml"

RETRY_BACKOFFS = [60, 300, 1800, 7200]
MAX_RETRIES = 5
WATCHDOG_POLL_INTERVAL = 5
TRIGGER_POLL_INTERVAL = 60

running = True


def _load_config():
    cfg = {
        "port": 8765,
        "bind": "0.0.0.0",
        "inbox_dir": str(DEFAULT_INBOX),
        "log_dir": str(DEFAULT_LOG_DIR),
        "max_retries": MAX_RETRIES,
        "retry_backoff_base": 60,
        "poll_interval": WATCHDOG_POLL_INTERVAL,
        "classifier_model": os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M"),
        "ollama_host": os.getenv("OLLAMA_HOST", "127.0.0.1"),
        "ollama_port": int(os.getenv("OLLAMA_PORT", "11434")),
        "use_sidecar": True,
    }
    if CONFIG_FILE.exists():
        try:
            import tomllib
            with open(CONFIG_FILE, "rb") as f:
                toml = tomllib.load(f)
            daemon = toml.get("daemon", {})
            for k in ("port", "bind", "inbox_dir", "log_dir", "max_retries", "retry_backoff_base", "poll_interval"):
                if k in daemon:
                    cfg[k] = daemon[k]
            clf = toml.get("classifier", {})
            if "model" in clf:
                cfg["classifier_model"] = clf["model"]
            if "host" in clf:
                cfg["ollama_host"] = clf["host"]
            if "port" in clf:
                cfg["ollama_port"] = int(clf["port"])
            if "use_sidecar" in clf:
                cfg["use_sidecar"] = bool(clf["use_sidecar"])
        except Exception:
            pass
    for k, v in os.environ.items():
        if k == "SB_DAEMON_PORT":
            cfg["port"] = int(v)
        elif k == "SB_DAEMON_BIND":
            cfg["bind"] = v
        elif k == "OLLAMA_MODEL":
            cfg["classifier_model"] = v
        elif k == "OLLAMA_HOST":
            cfg["ollama_host"] = v
        elif k == "OLLAMA_PORT":
            cfg["ollama_port"] = int(v)
    return cfg


def _setup_logging(log_dir: str):
    p = Path(log_dir)
    p.mkdir(parents=True, exist_ok=True)
    log_path = p / "daemon.log"
    logger = logging.getLogger("daemon")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


logger = logging.getLogger("daemon")


class DaemonState:
    def __init__(self):
        self.pid = os.getpid()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.last_ingest_ts = None
        self.pending_queue = []
        self.retry_queue = []
        self.conflicts = []
        self.devices = {}
        self.activity_log = []
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.last_ingest_ts = data.get("last_ingest_ts")
                self.pending_queue = data.get("pending_queue", [])
                self.retry_queue = data.get("retry_queue", [])
                self.conflicts = data.get("conflicts", [])
                self.devices = data.get("devices", {})
                self.activity_log = data.get("activity_log", [])
            except Exception:
                pass

    def save(self):
        with self._lock:
            data = {
                "pid": self.pid,
                "started_at": self.started_at,
                "last_ingest_ts": self.last_ingest_ts,
                "pending_queue": self.pending_queue,
                "retry_queue": self.retry_queue,
                "conflicts": self.conflicts,
                "devices": self.devices,
                "activity_log": self.activity_log[-200:],
            }
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def log_activity(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M")
        self.activity_log.append({"ts": ts, "msg": msg})
        if len(self.activity_log) > 200:
            self.activity_log = self.activity_log[-200:]


class IngestHandler(FileSystemEventHandler):
    def __init__(self, state: DaemonState, store: Store, inbox_dir: Path, cfg: dict):
        self.state = state
        self.store = store
        self.inbox_dir = inbox_dir
        self.cfg = cfg
        self.seen = set()
        self._process_lock = threading.Lock()

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        self._enqueue(path)

    def on_moved(self, event):
        if event.is_directory:
            return
        dest = Path(event.dest_path)
        self._enqueue(dest)

    def _enqueue(self, path: Path):
        if path.suffix.lower() == ".json":
            return
        if path.suffix.lower() not in {".md", ".txt", ".csv"}:
            return
        key = f"{path}:{path.stat().st_mtime}"
        if key in self.seen:
            return
        self.seen.add(key)
        with self._process_lock:
            self.state.pending_queue.append({
                "path": str(path),
                "added_at": datetime.now(timezone.utc).isoformat(),
            })
            self.state.save()

    def process_pending(self):
        with self._process_lock:
            queue = list(self.state.pending_queue)
            self.state.pending_queue = []
        if not queue:
            return
        for item in queue:
            try:
                self._ingest_file(Path(item["path"]))
            except Exception as e:
                logger.exception("Ingest failed for %s: %s", item["path"], e)
                self._enqueue_retry(item, str(e))
        self.state.save()

    def _ingest_file(self, path: Path):
        if not path.exists():
            return
        text = path.read_text(errors="ignore")
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        if self.store.exists_by_content(content_hash):
            logger.info("Duplicate skipped: %s", path)
            self.state.log_activity(f"duplicate skipped {path.name}")
            return
        device_id = path.parent.name
        source_id = str(path)
        ts_iso = datetime.now(timezone.utc).isoformat()
        fid = fingerprint("device", source_id, text, ts_iso)
        if self.store.exists(fid):
            return
        sidecar = self._try_load_sidecar(path)
        route = sidecar.get("route") if isinstance(sidecar, dict) else None
        tag_seeds = sidecar.get("tag_seeds", []) if isinstance(sidecar, dict) else []
        confidence = sidecar.get("confidence") if isinstance(sidecar, dict) else None
        action_atom = sidecar.get("action_atom") if isinstance(sidecar, dict) else None
        target_list = sidecar.get("target_list") if isinstance(sidecar, dict) else None
        escalate_reason = sidecar.get("escalate_reason") if isinstance(sidecar, dict) else None
        from jarvis.extract import extract_metadata
        extraction = extract_metadata(text)
        base_tags = ["device", device_id] + tag_seeds + extraction.get("tags", [])[:5]
        meta = {"device": device_id, "path": str(path), "entities": extraction.get("entities", [])}
        if isinstance(sidecar, dict):
            meta["sidecar"] = sidecar
        chunks = chunk_document(text, metadata=meta)
        added = 0
        for i, chunk in enumerate(chunks):
            cid = f"{fid}-{i}"
            emb = get_embedding(chunk["text"])
            self.store.add(cid, "device", source_id, ts_iso, chunk["text"], base_tags, meta, emb, route=route or "unclassified")
            added += 1
        if sidecar and route and route != "unclassified":
            envelope = {
                "route": route,
                "slug": sidecar.get("slug"),
                "source_url_list": sidecar.get("source_url_list", []),
                "inbox_path": source_id,
                "target_list": target_list,
                "action_atom": action_atom,
                "tag_seeds": tag_seeds,
                "confidence": confidence or "medium",
                "escalate_reason": escalate_reason,
                "notes": None,
            }
            if validate_envelope(envelope):
                apply_envelope(self.store, fid, envelope, log=True)
        elif not sidecar:
            self._classify_and_apply(fid, text, source_id, base_tags, meta, chunks)
        self.state.last_ingest_ts = ts_iso
        self.state.devices[device_id] = {"last_seen": ts_iso, "last_push": ts_iso}
        self.state.log_activity(f"{device_id}: ingested {added} chunk(s) from {path.name}")
        logger.info("Ingested %s -> %d chunks [device=%s]", path, added, device_id)

    def _classify_and_apply(self, fid, text, source_id, tags, meta, chunks):
        try:
            envelope = classify(text, source_id=source_id, model=self.cfg.get("classifier_model"))
            if not validate_envelope(envelope):
                envelope = {
                    "route": "escalate", "slug": None, "source_url_list": [],
                    "inbox_path": source_id, "target_list": None, "action_atom": None,
                    "tag_seeds": [], "confidence": "low",
                    "escalate_reason": "envelope validation failed after classification",
                    "notes": None,
                }
            apply_envelope(self.store, fid, envelope, log=True)
            for i, chunk in enumerate(chunks):
                cid = f"{fid}-{i}"
                self.store.conn.execute(
                    "UPDATE memories SET route = ?, tags = ? WHERE id = ?",
                    (envelope.get("route", "unclassified"), json.dumps(sorted(set(tags) | set(ROUTE_TAG_MAP.get(envelope.get("route", "unclassified"), [])))), cid)
                )
            self.store.conn.commit()
            self.state.log_activity(f"classified {Path(source_id).name} -> {envelope.get('route')}")
        except Exception as e:
            logger.exception("Classification failed for %s: %s", fid, e)
            self._enqueue_retry({"path": source_id, "hash": fid}, str(e))

    def _try_load_sidecar(self, path: Path) -> dict | None:
        sidecar_path = path.with_suffix(".json")
        if sidecar_path.exists():
            try:
                return json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def _enqueue_retry(self, item: dict, reason: str):
        existing = [r for r in self.state.retry_queue if r.get("hash") == item.get("hash")]
        if existing:
            r = existing[0]
            r["attempts"] = r.get("attempts", 0) + 1
            r["reason"] = reason
            r["next_retry"] = datetime.now(timezone.utc).isoformat()
        else:
            self.state.retry_queue.append({
                "path": item.get("path"),
                "hash": item.get("hash"),
                "reason": reason,
                "attempts": 1,
                "next_retry": datetime.now(timezone.utc).isoformat(),
            })

    def process_retry_queue(self):
        now = datetime.now(timezone.utc)
        ready = []
        remaining = []
        for r in self.state.retry_queue:
            if r.get("attempts", 0) >= self.cfg.get("max_retries", MAX_RETRIES):
                continue
            nr = r.get("next_retry")
            if nr and now >= datetime.fromisoformat(nr):
                ready.append(r)
            else:
                remaining.append(r)
        self.state.retry_queue = remaining
        for item in ready:
            path = Path(item.get("path", ""))
            if path.exists():
                try:
                    text = path.read_text(errors="ignore")
                    self._classify_and_apply(item.get("hash", ""), text, str(path), [], {}, chunk_document(text))
                except Exception as e:
                    logger.exception("Retry failed for %s: %s", item.get("path"), e)
                    item["attempts"] = item.get("attempts", 0) + 1
                    backoff = RETRY_BACKOFFS[min(item.get("attempts", 1) - 1, len(RETRY_BACKOFFS) - 1)]
                    item["next_retry"] = (datetime.fromtimestamp(now.timestamp() + backoff)).isoformat()
                    remaining.append(item)
        self.state.save()


class DaemonHTTPHandler(BaseHTTPRequestHandler):
    state: DaemonState = None
    store: Store = None
    cfg: dict = None
    handler: IngestHandler = None

    def log_message(self, fmt, *args):
        logger.debug(fmt, *args)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/status":
            uptime = 0
            if self.state.started_at:
                started = datetime.fromisoformat(self.state.started_at.replace("Z", "+00:00"))
                uptime = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
            self._json({
                "pid": self.state.pid,
                "started_at": self.state.started_at,
                "uptime_seconds": uptime,
                "last_ingest_ts": self.state.last_ingest_ts,
                "pending_queue_depth": len(self.state.pending_queue),
                "retry_queue_depth": len(self.state.retry_queue),
                "conflict_count": len(self.state.conflicts),
            })
        elif path == "/devices":
            self._json(self.state.devices)
        elif path == "/queue":
            self._json({
                "pending": self.state.pending_queue,
                "retry": self.state.retry_queue,
            })
        elif path == "/conflicts":
            self._json(self.state.conflicts)
        elif path == "/entities":
            q = ""
            if parsed.query:
                q = dict(x.split("=") for x in parsed.query.split("&") if "=" in x).get("q", "")
            if q:
                try:
                    from jarvis.graph import resolve_entity
                    row = self.store.conn.execute(
                        "SELECT id, canonical_name, entity_type, source_count, first_seen, last_seen FROM entities WHERE canonical_name LIKE ?",
                        (f"%{q}%",)
                    ).fetchall()
                    results = [dict(r) for r in row]
                    # If exact-ish fuzzy match, resolve to exact id
                    ent_id = resolve_entity(self.store, q)
                    if ent_id and not any(r["id"] == ent_id for r in results):
                        erow = self.store.conn.execute("SELECT id, canonical_name, entity_type, source_count, first_seen, last_seen FROM entities WHERE id = ?", (ent_id,)).fetchone()
                        if erow:
                            results.insert(0, dict(erow))
                    self._json(results)
                except ImportError:
                    self._json([], 200)
            else:
                self._json([], 200)
        elif path.startswith("/entity/"):
            eid = path.split("/")[-1]
            row = self.store.conn.execute(
                "SELECT id, canonical_name, entity_type, source_count, first_seen, last_seen FROM entities WHERE id = ?",
                (eid,)
            ).fetchone()
            if not row:
                self._json({"error": "not found"}, 404)
            else:
                ent = dict(row)
                # include related entities
                try:
                    from jarvis.graph import get_related
                    ent["related"] = get_related(self.store, eid)
                except ImportError:
                    ent["related"] = []
                self._json(ent)
        elif path == "/relationships":
            entity_id = ""
            if parsed.query:
                entity_id = dict(x.split("=") for x in parsed.query.split("&") if "=" in x).get("entity", "")
            if not entity_id:
                self._json({"error": "missing entity param"}, 400)
            else:
                rows = self.store.conn.execute(
                    """
                    SELECT r.id, r.source_entity, r.target_entity, r.relation_type,
                           r.confidence, r.created_at, r.source_memory_id,
                           s.canonical_name AS source_name,
                           t.canonical_name AS target_name
                    FROM relationships r
                    JOIN entities s ON r.source_entity = s.id
                    JOIN entities t ON r.target_entity = t.id
                    WHERE r.source_entity = ? OR r.target_entity = ?
                    ORDER BY r.confidence DESC
                    """,
                    (entity_id, entity_id)
                ).fetchall()
                self._json([dict(r) for r in rows])
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/sync":
            self.handler.process_pending()
            self.state.save()
            self._json({"status": "ok", "processed": True})
        elif path == "/classify":
            self.handler.process_retry_queue()
            self.state.save()
            self._json({"status": "ok", "retry_processed": True})
        else:
            self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path.startswith("/conflicts/"):
            cid = path.split("/")[-1]
            self.state.conflicts = [c for c in self.state.conflicts if c.get("id") != cid]
            self.state.save()
            self._json({"status": "ok"})
        else:
            self._json({"error": "not found"}, 404)


class Daemon:
    def __init__(self):
        self.cfg = _load_config()
        self.log_dir = self.cfg.get("log_dir", str(DEFAULT_LOG_DIR))
        _setup_logging(self.log_dir)
        self.inbox_dir = Path(self.cfg.get("inbox_dir", str(DEFAULT_INBOX)))
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.state = DaemonState()
        self.store = Store()
        self.handler = IngestHandler(self.state, self.store, self.inbox_dir, self.cfg)
        self.trigger_engine = self._build_trigger_engine()
        self.observer = None
        self.httpd = None
        self._write_pid()

    def _write_pid(self):
        pid_file = DEFAULT_PID_FILE
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))

    def _remove_pid(self):
        try:
            DEFAULT_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _catch_up(self):
        logger.info("Catching up on existing inbox files...")
        for p in self.inbox_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".csv"}:
                self.handler._enqueue(p)
        self.handler.process_pending()
        self.state.save()

    def _build_trigger_engine(self) -> 'TriggerEngine | None':
        if TriggerEngine is None or load_triggers is None:
            self.logger.warning('TriggerEngine not available; skipping trigger thread')
            return None
        try:
            triggers = load_triggers()
            engine = TriggerEngine(triggers)
            self.logger.info('TriggerEngine loaded with %d trigger(s)', len(triggers))
            return engine
        except Exception as exc:
            self.logger.error('Failed to build TriggerEngine: %s', exc, exc_info=True)
            return None


    def _start_watchdog(self):
        self.observer = watchdog.observers.Observer()
        self.observer.schedule(self.handler, str(self.inbox_dir), recursive=True)
        self.observer.start()
        logger.info("Watchdog started on %s", self.inbox_dir)

    def _start_http(self):
        host = self.cfg.get("bind", "127.0.0.1")
        port = int(self.cfg.get("port", 8765))
        DaemonHTTPHandler.state = self.state
        DaemonHTTPHandler.store = self.store
        DaemonHTTPHandler.cfg = self.cfg
        DaemonHTTPHandler.handler = self.handler
        self.httpd = HTTPServer((host, port), DaemonHTTPHandler)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        logger.info("HTTP API started on %s:%d", host, port)

    def _retry_loop(self):
        while running:
            time.sleep(30)
            self.handler.process_retry_queue()
            self.state.save()

    def _trigger_loop(self) -> None:
        '''Background thread: evaluate triggers every TRIGGER_POLL_INTERVAL s.'''
        if self.trigger_engine is None:
            return
        # Wait a little after startup so the initial catch-up settles
        time.sleep(30)
        while running:
            try:
                self.trigger_engine.evaluate(self.store, self.state)
            except Exception as exc:
                self.logger.error('Trigger loop error: %s', exc, exc_info=True)
            time.sleep(TRIGGER_POLL_INTERVAL)


    def start(self):
        logger.info("Daemon started (PID %d)", os.getpid())
        self._catch_up()
        self._start_watchdog()
        self._start_http()
        t = threading.Thread(target=self._retry_loop, daemon=True)
        t.start()
        if self.trigger_engine is not None:
            tt = threading.Thread(target=self._trigger_loop, daemon=True)
            tt.start()
            self.logger.info('Trigger thread started (interval=%ds)', TRIGGER_POLL_INTERVAL)
        try:
            while running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        global running
        running = False
        if self.observer:
            self.observer.stop()
            self.observer.join()
        if self.httpd:
            self.httpd.shutdown()
        self.store.close()
        self._remove_pid()
        logger.info("Daemon stopped")


def _signal_handler(signum, frame):
    global running
    running = False


def main():
    global running
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    daemon = Daemon()
    daemon.start()


if __name__ == "__main__":
    main()
