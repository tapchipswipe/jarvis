import os
import sys
import time
import signal
import subprocess
from pathlib import Path

PID_FILE = Path("/tmp/jarvis-daemon.pid")
# Resolve log dir portably: default to the home-based jarvis dir (macOS/local),
# overridable via JARVIS_ROOT or JARVIS_LOG_DIR (Lightspeed/Windows use /data).
_JARVIS_HOME = Path(os.environ.get("JARVIS_ROOT", str(Path.home() / "jarvis")))
LOG_DIR = Path(os.environ.get("JARVIS_LOG_DIR", str(_JARVIS_HOME / "logs")))
LOG_FILE = LOG_DIR / "daemon.log"
DAEMON_MODULE = "jarvis.sync.daemon"
VERSION_COMMAND_PATH = Path(__file__).resolve().parent / "cli.py"

TRIGGER_INTERVAL = 60  # seconds between trigger evaluations in this process

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


def start_trigger_loop():
    """Start the background trigger thread for this service process.

    The loop loads triggers via ``jarvis.triggers.load_triggers()``, evaluates
    them every ``TRIGGER_INTERVAL`` seconds with a TriggerContext built from
    the Store, and dispatches due actions (notify/brief/escalate) on schedule.
    Returns the TriggerLoop, or None if it could not be started (the service
    keeps running either way; failures are logged).
    """
    try:
        from jarvis.triggers import TriggerLoop
        from jarvis.store import Store

        loop = TriggerLoop(store=Store(), interval=TRIGGER_INTERVAL)
        loop.start()
        log(f"Trigger loop started (interval={loop.interval}s, {loop.trigger_count} trigger(s))")
        return loop
    except Exception as exc:        # noqa: BLE001
        log(f"Trigger loop not started: {exc}")
        return None


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
    log("Starting Jarvis service (daemon mode)")
    
    # Run the version command to display Jarvis version
    subprocess.run([sys.executable, str(VERSION_COMMAND_PATH)])

    # Background trigger thread (time/poll/event triggers fire on schedule)
    trigger_loop = start_trigger_loop()

    project_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)
    proc = subprocess.Popen(
        [sys.executable, "-m", DAEMON_MODULE],
        cwd=str(project_root),
        env=env,
    )
    log(f"Daemon PID: {proc.pid}")
    try:
        while running:
            time.sleep(1)
            if proc.poll() is not None:
                log(f"Daemon exited with code {proc.returncode}")
                break
    except KeyboardInterrupt:
        pass
    finally:
        if trigger_loop is not None:
            trigger_loop.stop()
            trigger_loop.join(timeout=5)
            log("Trigger loop stopped")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        remove_pid()
        log("Service stopped")


if __name__ == "__main__":
    main()