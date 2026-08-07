"""
jarvis/notify.py

Cross-platform notification and briefing writer.

Notification delivery (in order of preference):
  1. terminal-notifier  (macOS only, if installed)
  2. osascript          (macOS built-in)
  3. ~/.config/jarvis/notifications.log  (always written; cross-platform fallback)

A record is always appended to the notifications log irrespective of which
channel succeeds, so the log serves as a durable event history.

Briefings are always written to
  ~/.config/jarvis/briefings/YYYY-MM-DD.md

Public API
-----------
send_notification(title, body, *, category=None)  -> None
write_briefing(title, content)                     -> Path
"""

from __future__ import annotations

import logging
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("jarvis.notify")

# ── Paths ────────────────────────────────────────────────────────────────────
# Resolved lazily so JARVIS_CONFIG_DIR / JARVIS_USER set at runtime are honoured.
# Tests may override STATE_DIR directly (kept as a module attribute for that).

STATE_DIR = None  # type: ignore[assignment]  # None -> resolve from env

SYSTEM = platform.system()


def _state_dir() -> Path:
    if STATE_DIR is not None:
        return Path(STATE_DIR)  # type: ignore[arg-type]
    from jarvis.paths import config_dir
    return config_dir()


def _notifications_log() -> Path:
    return _state_dir() / "notifications.log"


def _briefings_dir() -> Path:
    return _state_dir() / "briefings"


# ── Subprocess helper ────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 8) -> bool:
    """Run a subprocess; return True on exit-code 0, False otherwise."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0
    except Exception as exc:         # noqa: BLE001
        logger.debug("_run(%s) raised: %s", cmd, exc)
        return False


# ── Desktop notification backends ────────────────────────────────────────────

def _send_terminal_notifier(title: str, body: str, category: str | None) -> bool:
    if SYSTEM != "Darwin":
        return False
    cmd = ["terminal-notifier", "-title", title, "-message", body]
    if category:
        cmd += ["-group", category]
    return _run(cmd)


def _send_osascript(title: str, body: str) -> bool:
    if SYSTEM != "Darwin":
        return False
    script = (
        f'display notification "{body}" '
        f'with title "{title}" '
        f'sound name "default"'
    )
    return _run(["osascript", "-e", script])


# ── Durable log ──────────────────────────────────────────────────────────────

def _log_notification(title: str, body: str, channel: str | None = None) -> None:
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if channel is None:
        channel = "oscript" if SYSTEM == "Darwin" else "log"
    line = f"[{ts}] [{channel}] {title} | {body}\n"
    try:
        with _notifications_log().open("a", encoding="utf-8") as fh:
            fh.write(line)
        logger.debug("Notification logged: [%s] %s", channel, title)
    except Exception as exc:         # noqa: BLE001
        logger.warning("Could not write notifications log: %s", exc)


# ── Public API ───────────────────────────────────────────────────────────────

def send_notification(
    title: str,
    body: str,
    *,
    category: str | None = None,
) -> None:
    """Send a desktop notification.

    Tries terminal-notifier → osascript → always writes the notifications
    log as a persistent audit trail regardless of channel outcome.

    Backends are short-circuited: the first one that returns True wins and
    the rest are skipped, so only a single desktop popup is shown.
    """
    if _send_terminal_notifier(title, body, category):
        channel = "terminal-notifier"
    elif _send_osascript(title, body):
        channel = "osascript"
    else:
        channel = "log"
    _log_notification(title, body, channel)    # durable record, always written


def write_briefing(title: str, content: str) -> Path:
    """Append a briefing entry to today's briefing file.

    Returns the path of the file written.
    """
    briefings_dir = _briefings_dir()
    briefings_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = briefings_dir / f"{today}.md"

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = (
        f"\n---\n"
        f"## {title}  ({ts})\n\n"
        f"{content}\n"
    )

    header = ""
    if not path.exists() or path.stat().st_size == 0:
        header = f"# Jarvis Briefings — {today}\n"

    try:
        with path.open("a", encoding="utf-8") as fh:
            if header:
                fh.write(header)
            fh.write(entry)
        logger.info("Briefing written: %s", path)
    except Exception as exc:         # noqa: BLE001
        logger.error("Could not write briefing: %s", exc)

    return path
