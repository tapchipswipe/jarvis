# Second Brain — Windows Service Entry Point
# Runs inbox watcher + file watchers on Lightspeed

import os
import sys
import time
import signal
import threading
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PID_FILE = Path("C:/data/second-brain/logs/service.pid")
LOG_FILE = Path("C:/data/second-brain/logs/service.log")
INBOX_DIR = Path("C:/data/second-brain/inbox")

running = True

def log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
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
    log("Starting Second Brain service (Windows)")
    device_id = "lightspeed"
    log(f"Device ID: {device_id}")
    from brain.store import Store
    from brain.collectors.inbox import start_inbox_watcher
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
