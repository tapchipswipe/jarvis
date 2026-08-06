"""Jarvis data collectors.

``capture()`` is the thin-client write path (raw text -> outbox -> server).
In local mode collectors write to the Store directly via their own logic; here
``capture`` returns None so callers know to use the local path.
"""
from __future__ import annotations


def capture(text: str, source: str = "device", tags=None, metadata=None):
    """Queue a piece of raw capture to the server (client mode only).

    Returns True if queued, False if blank/duplicate, or None when not running
    as a thin client (in which case callers use the store directly).
    """
    from jarvis import remote
    if not remote.is_remote():
        return None
    from jarvis.cache import Cache
    cache = Cache()
    try:
        return cache.enqueue(text, source=source, tags=list(tags or []), metadata=metadata or {})
    finally:
        cache.close()
