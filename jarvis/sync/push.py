import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from jarvis.device_id import get_device_id

DEVICE_ID = get_device_id()

LIGHTSPEED_HOST = os.getenv("LIGHTSPEED_HOST", "lightspeed")
LIGHTSPEED_USER = os.getenv("LIGHTSPEED_USER", os.getenv("USER", "user"))
LIGHTSPEED_INBOX = os.getenv("LIGHTSPEED_INBOX", "C:/data/jarvis/inbox")
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "PasswordAuthentication=no", "-o", "ConnectTimeout=5"]

# After attempt 1, 2, 3, 4+ respectively (seconds between retries).
PUSH_BACKOFFS = [60, 300, 1800, 7200]
# Batch pushes smaller than this many files use per-file SCP.
BATCH_MIN_FILES = 30


def push_backoff(attempts: int) -> int:
    """Seconds to wait before the next attempt after *attempts* failures."""
    if attempts <= 0:
        return 0
    idx = min(attempts - 1, len(PUSH_BACKOFFS) - 1)
    return PUSH_BACKOFFS[idx]


def lightspeed_reachable() -> bool:
    host = LIGHTSPEED_HOST
    for probe in [
        ["ping", "-c", "1", "-W", "2", host],
        ["ssh"] + SSH_OPTS + [f"{LIGHTSPEED_USER}@{host}", "echo ok"],
    ]:
        try:
            result = subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return False


def scp_put(local_path: Path, remote_path: str):
    remote = f"{LIGHTSPEED_USER}@{LIGHTSPEED_HOST}:{remote_path}"
    subprocess.run(["scp"] + SSH_OPTS + [str(local_path), remote], check=True)


def ssh_run(command: str):
    remote = f"{LIGHTSPEED_USER}@{LIGHTSPEED_HOST}"
    subprocess.run(["ssh"] + SSH_OPTS + [remote, command], check=True)


def push_file(local_path: Path, source: str = "device") -> str:
    if not local_path.exists():
        return ""
    if not lightspeed_reachable():
        return ""
    remote_name = f"{DEVICE_ID}/{local_path.name}"
    remote_path = f"{LIGHTSPEED_INBOX}/{remote_name}"
    scp_put(local_path, remote_path)
    return remote_name


def push_text(text: str, filename: str, source: str = "device") -> str:
    if not lightspeed_reachable():
        return ""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(text)
        tmp = Path(f.name)
    remote_name = f"{DEVICE_ID}/{filename}"
    remote_path = f"{LIGHTSPEED_INBOX}/{remote_name}"
    scp_put(tmp, remote_path)
    tmp.unlink()
    return remote_name


def ensure_remote_dir():
    if not lightspeed_reachable():
        return
    ssh_run(f"powershell -Command \"New-Item -ItemType Directory -Force -Path '{LIGHTSPEED_INBOX}/{DEVICE_ID}'\"")


def stage_bundle(entries: list[dict], device_id: str | None = None) -> Path:
    """Write entry .txt/.json pairs under a temp dir and tar.gz them.

    *entries* is a list of dicts with 'content' and 'sidecar' (JSON str or
    dict). The bundle extracts to ``<device_id>/...`` on the remote inbox.
    Returns path to the .tar.gz bundle.
    """
    device_id = device_id or DEVICE_ID
    basedir = Path(tempfile.mkdtemp(prefix="jarvis_push_"))
    devdir = basedir / device_id
    devdir.mkdir(parents=True, exist_ok=True)
    for e in entries:
        content = e["content"]
        sidecar = e.get("sidecar")
        if isinstance(sidecar, str):
            try:
                sidecar = json.loads(sidecar)
            except (ValueError, TypeError):
                sidecar = {}
        # Full 64-hex digest as the bundle id so two distinct memories can never
        # share a filename (a 16-hex prefix would be a latent collision that
        # could overwrite a peer file and drop a memory).
        cid = hashlib.sha256(content.encode()).hexdigest()
        base = f"{cid}_{sidecar.get('tier', 'raw')}_{sidecar.get('source', 'device')}"
        (devdir / f"{base}.txt").write_text(content, encoding="utf-8")
        (devdir / f"{base}.json").write_text(
            json.dumps(sidecar, ensure_ascii=False), encoding="utf-8"
        )
    bundle = basedir / f"jarvis-bundle-{int(time.time())}.tar.gz"
    env = dict(os.environ)
    env["COPYFILE_DISABLE"] = "1"  # never ship macOS AppleDouble (._) files
    subprocess.run(
        ["tar", "-czf", str(bundle), device_id],
        cwd=str(basedir), check=True, env=env,
    )
    return bundle


def push_bundle(bundle: Path, inbox: str | None = None) -> bool:
    """SCP a staged bundle to the inbox and extract it remotely.

    Falls back to False so callers can retry per-file.
    """
    inbox = inbox or LIGHTSPEED_INBOX
    remote_bundle = f"{inbox}/bundle_{uuid.uuid4().hex[:8]}.tar.gz"
    try:
        scp_put(bundle, remote_bundle)
        ssh_run(
            "powershell -Command \"" + (
                f"tar -xzf '{remote_bundle}' -C '{inbox}'; "
                f"Remove-Item '{remote_bundle}' -Force"
            ) + "\""
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def get_lightspeed_stats() -> dict:
    """Return remote memory stats from Lightspeed, or {} if unreachable."""
    if not lightspeed_reachable():
        return {}
    try:
        remote_cmd = (
            "python -c \"import sqlite3, json; "
            "conn = sqlite3.connect('C:/data/jarvis/data/meta.db'); "
            "conn.row_factory = sqlite3.Row; "
            "rows = conn.execute('SELECT source, tier, COUNT(*) as count FROM memories WHERE superseded = 0 GROUP BY source, tier').fetchall(); "
            "for r in rows: print(f'{r[\"source\"]}|{r[\"tier\"]}|{r[\"count\"]}'); "
            "conn.close()\""
        )
        result = subprocess.run(
            ["ssh"] + SSH_OPTS + [f"{LIGHTSPEED_USER}@{LIGHTSPEED_HOST}", remote_cmd],
            capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode == 0:
            sources = {}
            for line in result.stdout.strip().splitlines():
                if "|" in line:
                    src, tier, count = line.split("|")
                    sources[f"{src}:{tier}"] = int(count)
            total = sum(sources.values())
            return {"total": total, "sources": sources, "raw": result.stdout[:500]}
    except Exception:
        pass
    return {}
