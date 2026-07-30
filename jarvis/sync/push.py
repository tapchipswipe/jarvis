import os
import subprocess
import time
from pathlib import Path
from jarvis.device_id import get_device_id

DEVICE_ID = get_device_id()

LIGHTSPEED_HOST = os.getenv("LIGHTSPEED_HOST", "lightspeed")
LIGHTSPEED_USER = os.getenv("LIGHTSPEED_USER", os.getenv("USER", "user"))
LIGHTSPEED_INBOX = os.getenv("LIGHTSPEED_INBOX", "C:/data/jarvis/inbox")
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "PasswordAuthentication=no", "-o", "ConnectTimeout=5"]


def lightspeed_reachable() -> bool:
    host = LIGHTSPEED_HOST
    for probe in [
        ["ping", "-c", "1", "-W", "2", host],
        ["ssh"] + SSH_OPTS + [f"{LIGHTSPEED_USER}@{host}", "echo ok"],
    ]:
        try:
            result = subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
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
            capture_output=True, text=True, timeout=30
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
