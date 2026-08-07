import json
from datetime import datetime, timedelta, timezone

from jarvis.brain import Brain
from jarvis.embed import get_embedding
from jarvis.store import Store, fingerprint

PROMPT_DAILY = """You are a memory consolidation engine. Read the raw memories below and write a concise session summary (200-400 words). Group by topic. Focus on key events, decisions, and insights. Write in first-person plural (we/our). Do not invent facts. End with 1-2 sentence "takeaway" block."""

PROMPT_WEEKLY = """You are a memory consolidation engine. Read the session summaries below and write a weekly reflection (300-500 words). Identify themes, progress, and shifts in direction. Compare against prior weeks if mentioned. End with 3 bullet points: wins, blockers, next focus."""

PROMPT_MONTHLY = """You are a memory consolidation engine. Read the weekly reflections below and write a monthly arc (400-600 words). Describe the overarching narrative: what we started, what changed, where we are now. Be specific. End with 3-5 sentence executive summary."""


def cluster_by_topic(memories: list[dict], max_clusters: int = 5) -> list[list[dict]]:
    tags = {}
    for m in memories:
        for tag in m.get("tags", []):
            tags.setdefault(tag, []).append(m)
    clusters = [c for c in tags.values() if len(c) >= 2]
    singles = [m for m in memories if not any(m in c for c in clusters)]
    result = clusters + [[m] for m in singles]
    return result[:max_clusters]


def summarize_cluster(memories: list[dict], prompt: str) -> str | None:
    if not memories:
        return None
    combined = "\n\n".join(f"[{m['timestamp']}] [{m['source']}] {m['content']}" for m in memories)
    brain_store = Store()
    jarvis = Brain(brain_store)
    try:
        resp, _ = jarvis.query(f"{prompt}\n\nMESSAGES:\n{combined}", n_results=0)
        return resp
    except Exception:
        return None
    finally:
        brain_store.close()


def run_daily():
    store = Store()
    raws = store.get_recent_raw(hours=24)
    if len(raws) < 20:
        store.close()
        return 0
    clusters = cluster_by_topic(raws)
    added = 0
    for cluster in clusters:
        summary = summarize_cluster(cluster, PROMPT_DAILY)
        if not summary:
            continue
        all_ids = [m["id"] for m in cluster]
        fid = fingerprint("consolidation", f"daily-{datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()}", summary, datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        existing = fid.rsplit("-", 1)[0]
        if store.exists(fid):
            continue
        tags = ["consolidated", "session"] + list(set(t for m in cluster for t in m.get("tags", [])))[:5]
        meta = {"consolidated_from": json.dumps(all_ids), "count": len(cluster)}
        text = summary[:4000]
        emb = get_embedding(text)
        expires = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7)).isoformat()
        store.add(fid, "consolidation", existing, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), summary, tags, meta, emb, tier="session", expires_at=expires, consolidated_from=json.dumps(all_ids))
        added += 1
    store.close()
    return added


def run_weekly():
    store = Store()
    sessions = store.get_by_tier("session", limit=50)
    if len(sessions) < 10:
        store.close()
        return 0
    summary_texts = [s["content"] for s in sessions]
    combined = "\n\n".join(summary_texts)
    reflection = summarize_cluster([{"id": s["id"], "content": s["content"], "timestamp": s["timestamp"], "source": s["source"], "tags": json.loads(s["tags"])} for s in sessions], PROMPT_WEEKLY)
    if not reflection:
        store.close()
        return 0
    fid = fingerprint("consolidation", f"weekly-{datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()}", reflection, datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
    if store.exists(fid):
        store.close()
        return 0
    all_ids = [s["id"] for s in sessions]
    tags = ["consolidated", "reflection"] + list(set(t for s in sessions for t in json.loads(s["tags"])))[:5]
    meta = {"consolidated_from": json.dumps(all_ids), "count": len(sessions)}
    text = reflection[:4000]
    emb = get_embedding(text)
    expires = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).isoformat()
    store.add(fid, "consolidation", fid, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), reflection, tags, meta, emb, tier="reflection", expires_at=expires, consolidated_from=json.dumps(all_ids))
    store.close()
    return 1


def run_monthly():
    store = Store()
    reflections = store.get_by_tier("reflection", limit=20)
    if len(reflections) < 4:
        store.close()
        return 0
    texts = [r["content"] for r in reflections]
    combined = "\n\n".join(texts)
    arc = summarize_cluster([{"id": r["id"], "content": r["content"], "timestamp": r["timestamp"], "source": r["source"], "tags": json.loads(r["tags"])} for r in reflections], PROMPT_MONTHLY)
    if not arc:
        store.close()
        return 0
    fid = fingerprint("consolidation", f"monthly-{datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()}", arc, datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
    if store.exists(fid):
        store.close()
        return 0
    all_ids = [r["id"] for r in reflections]
    tags = ["consolidated", "arc"]
    meta = {"consolidated_from": json.dumps(all_ids), "count": len(reflections)}
    text = arc[:4000]
    emb = get_embedding(text)
    store.add(fid, "consolidation", fid, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), arc, tags, meta, emb, tier="arc", consolidated_from=json.dumps(all_ids))
    store.close()
    return 1

