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
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.notify")

# ── Paths ────────────────────────────────────────────────────────────────────
STATE_DIR = Path.home() / ".config" / "jarvis"
NOTIFICATIONS_LOG = STATE_DIR / "notifications.log"
BRIEFINGS_DIR = STATE_DIR / "briefings"

SYSTEM = platform.system()


# ── Subprocess helper ────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 8) -> bool:
    """Run a subprocess; return True on exit-code 0, False otherwise."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except Exception as exc:         # noqa: BLE001
        logger.debug("_run(%s) raised: %s", cmd, exc)
        return False


# ── Desktop notification backends ────────────────────────────────────────────

def _send_terminal_notifier(title: str, body: str, category: Optional[str]) -> bool:
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

def _log_notification(title: str, body: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    channel = "oscript" if SYSTEM == "Darwin" else "log"
    line = f"[{ts}] [{channel}] {title} | {body}\n"
    try:
        with NOTIFICATIONS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
        logger.debug("Notification logged: [%s] %s", channel, title)
    except Exception as exc:         # noqa: BLE001
        logger.warning("Could not write notifications log: %s", exc)


# ── Public API ───────────────────────────────────────────────────────────────

def send_notification(
    title: str,
    body: str,
    *,
    category: Optional[str] = None,
) -> None:
    """Send a desktop notification.

    Tries terminal-notifier → osascript → always writes the notifications
    log as a persistent audit trail regardless of channel outcome.
    """
    _send_terminal_notifier(title, body, category)
    _send_osascript(title, body)      # best-effort; exit code ignored on purpose
    _log_notification(title, body)    # durable record, always written


def write_briefing(title: str, content: str) -> Path:
    """Append a briefing entry to today's briefing file.

    Returns the path of the file written.
    """
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = BRIEFINGS_DIR / f"{today}.md"

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
