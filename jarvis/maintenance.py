"""jarvis/maintenance.py — callable memory-maintenance routines.

Extracted from the CLI so automated callers (Mayor idle loop, cron, and the
``jarvis reindex``/``jarvis promote`` commands) can run the same logic without
shelling out.
"""
from __future__ import annotations

from jarvis.store import Store
from jarvis.embed import get_embedding


def reindex_missing(store: Store | None = None, limit: int = 200) -> int:
    """Embed memories missing from the vector store (embedded_at IS NULL).

    Returns the number of memories re-indexed. Safe to call repeatedly.
    """
    owned = store is None
    store = store or Store()
    try:
        rows = store.get_unembedded(limit=limit)
        done = 0
        for m in rows:
            emb = get_embedding(m["content"])
            meta = {
                "source": m["source"],
                "timestamp": m["timestamp"],
                "tier": m["tier"],
                "weight": m["weight"],
                "route": m["route"],
            }
            try:
                store.collection.add(
                    ids=[m["id"]], embeddings=[emb],
                    documents=[m["content"]], metadatas=[meta],
                )
            except Exception:  # noqa: BLE001, S110 - vector write is best-effort
                pass
            store.mark_embedded(m["id"])
            done += 1
        return done
    finally:
        if owned:
            store.close()


def promote_old(store: Store | None = None, days: int = 7, limit: int = 500) -> int:
    """Promote raw memories older than *days* to the session tier.

    Returns the number promoted.
    """
    store = store or Store()
    owned = False
    try:
        return store.promote_raw_to_session(days=days, limit=limit)
    finally:
        if owned:
            store.close()
