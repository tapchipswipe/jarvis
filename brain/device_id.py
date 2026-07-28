import hashlib
import json
import os
from pathlib import Path

DEVICE_ID_FILE = Path.home() / ".config" / "second-brain" / "device-id"


def get_device_id() -> str:
    if DEVICE_ID_FILE.exists():
        return DEVICE_ID_FILE.read_text().strip()
    hostname = os.uname().nodename
    mac_addr = _mac_address()
    raw = f"{hostname}:{mac_addr}"
    device_id = hashlib.sha256(raw.encode()).hexdigest()[:12]
    DEVICE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_ID_FILE.write_text(device_id)
    return device_id


def _mac_address() -> str:
    try:
        ifaces = os.listdir("/sys/class/net")
        for iface in ["eth0", "en0", "wlan0", "wlan1"]:
            if iface in ifaces:
                addr = Path(f"/sys/class/net/{iface}/address").read_text().strip()
                if addr != "00:00:00:00:00:00":
                    return addr
        for iface in ifaces:
            if iface == "lo":
                continue
            addr = Path(f"/sys/class/net/{iface}/address").read_text().strip()
            if addr != "00:00:00:00:00:00":
                return addr
    except Exception:
        pass
    return "unknown"
