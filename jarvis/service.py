import os
import sys
import time
import signal
import subprocess
from pathlib import Path

PID_FILE = Path("/tmp/jarvis-daemon.pid")
LOG_FILE = Path("/data/jarvis/logs/daemon.log")
DAEMON_MODULE = "jarvis.sync.daemon"
VERSION_COMMAND_PATH = Path(__file__).resolve().parent / "cli.py"

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
    log("Starting Jarvis service (daemon mode)")
    
    # Run the version command to display Jarvis version
    subprocess.run([sys.executable, str(VERSION_COMMAND_PATH)])

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
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        remove_pid()
        log("Service stopped")


if __name__ == "__main__":
    main()