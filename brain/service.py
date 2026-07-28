import os
import sys
import time
import signal
import threading
from pathlib import Path
from brain.store import Store
from brain.collectors.inbox import start_inbox_watcher
from brain.device_id import get_device_id

PID_FILE = Path("/tmp/second-brain-service.pid")
LOG_FILE = Path("/data/second-brain/logs/service.log")
INBOX_DIR = Path("/data/second-brain/inbox")

running = True


def log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] {msg}\n")


def signal_handler(signum, frame):
    global running
    running = False
    log("Shutting down...")


def write_pid():
    PID_FILE.write_text(str(os.getpid()))


def remove_pid():
    if PID_FILE.exists():
        PID_FILE.unlink()


def main():
    global running
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    write_pid()
    log("Starting Second Brain service")
    device_id = get_device_id()
    log(f"Device ID: {device_id}")
    store = Store()
    log("Store initialized")
    watcher = start_inbox_watcher(store)
    log("Inbox watcher started")
    log(f"Watching: {INBOX_DIR}")
    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        watcher.join()
        store.close()
        remove_pid()
        log("Service stopped")


if __name__ == "__main__":
    main()
