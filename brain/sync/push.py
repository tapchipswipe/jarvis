import os
import subprocess
import time
from pathlib import Path
from brain.device_id import get_device_id

DEVICE_ID = get_device_id()

LIGHTSPEED_HOST = os.getenv("LIGHTSPEED_HOST", "lightspeed")
LIGHTSPEED_USER = os.getenv("LIGHTSPEED_USER", os.getenv("USER", "user"))
LIGHTSPEED_INBOX = "/data/second-brain/inbox"
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
    ssh_run(f"mkdir -p {LIGHTSPEED_INBOX}/{DEVICE_ID}")
