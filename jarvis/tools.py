"""
jarvis/tools.py — Registered tools for the Jarvis agent loop.
Each tool is wrapped to never raise; errors are returned as dicts.
"""
import os
import json
import re
import uuid
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

from jarvis.store import Store
from jarvis.embed import get_embedding
from jarvis.graph import resolve_entity, get_entity_timeline
from jarvis.consolidation import run_daily, run_weekly, run_monthly


# ---------------------------------------------------------------------------
# Safety wrapper
# ---------------------------------------------------------------------------

def _safe(fn):
    def wrapper(session_store, args):
        try:
            return fn(session_store, args)
        except Exception as e:
            return {"error": str(e), "details": type(e).__name__}
    return wrapper


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

@_safe
def search_memories(session_store, args):
    query = args.get("query", "")
    source = args.get("source")
    n = int(args.get("n", 8))
    if not query:
        return {"results": [], "note": "empty query"}
    store = Store()
    try:
        emb = get_embedding(query)
        rows = store.search(emb, n_results=n, source_filter=source)
        results = []
        for r in rows:
            results.append({
                "id": r.get("id"),
                "content": r.get("content"),
                "source": r.get("source"),
                "timestamp": r.get("timestamp"),
                "tier": r.get("tier"),
            })
        return {"results": results}
    finally:
        store.close()


@_safe
def check_calendar(session_store, args):
    range_start = args.get("range_start")
    range_end = args.get("range_end")
    store = Store()
    try:
        query = "SELECT * FROM memories WHERE source = 'calendar' AND superseded = 0"
        params = []
        if range_start:
            query += " AND timestamp >= ?"
            params.append(range_start)
        if range_end:
            query += " AND timestamp <= ?"
            params.append(range_end)
        query += " ORDER BY timestamp DESC LIMIT 50"
        rows = store.conn.execute(query, params).fetchall()
        events = []
        for row in rows:
            d = dict(row)
            content = d.get("content", "")
            events.append({
                "id": d.get("id"),
                "title": content.split("\n")[0] if content else "",
                "content": content,
                "timestamp": d.get("timestamp"),
            })
        return {"events": events}
    finally:
        store.close()


@_safe
def get_entity_context(session_store, args):
    name = args.get("name", "")
    if not name:
        return {"error": "name is required"}
    store = Store()
    try:
        entity_id = resolve_entity(store, name)
        if not entity_id:
            return {"error": f"entity not found: {name}"}
        memories = get_entity_timeline(store, entity_id)
        return {"entity_name": name, "entity_id": entity_id, "memories": memories}
    finally:
        store.close()


@_safe
def summarize(session_store, args):
    memory_ids = args.get("memory_ids", [])
    if not memory_ids or len(memory_ids) < 2:
        return {"error": "insufficient memories", "count": len(memory_ids)}
    store = Store()
    try:
        contents = []
        for mid in memory_ids:
            row = store.conn.execute(
                "SELECT content FROM memories WHERE id = ? AND superseded = 0", (mid,)
            ).fetchone()
            if row:
                contents.append(row["content"])
        if len(contents) < 2:
            return {"error": "insufficient memories", "count": len(contents)}
        combined = "\n\n".join(contents)
        summary = combined[:2000] + ("..." if len(combined) > 2000 else "")
        return {"summary": summary, "count": len(contents)}
    finally:
        store.close()


@_safe
def search_web(session_store, args):
    query = args.get("query", "")
    if not query:
        return {"results": [], "note": "empty query"}
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return {"error": f"search failed: {e}", "results": []}

    results = []
    try:
        link_blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        )
        for href, title_html in link_blocks[:5]:
            title = re.sub(r"<[^>]+>", "", title_html).strip()
            if href.startswith("//duckduckgo.com/l/?uddg="):
                parsed = urllib.parse.urlparse(href)
                q = urllib.parse.parse_qs(parsed.query)
                href = q.get("uddg", [href])[0]
            results.append({"title": title, "url": href, "snippet": ""})

        snippets = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL
        )
        for i, s in enumerate(snippets[: len(results)]):
            results[i]["snippet"] = re.sub(r"<[^>]+>", "", s).strip()
    except Exception as e:
        return {"error": f"parse failed: {e}", "results": results}

    return {"results": results}


@_safe
def create_reminder(session_store, args):
    title = args.get("title", "")
    if not title:
        return {"error": "title is required", "status": "error"}
    due = args.get("due")
    notes = args.get("notes", "")
    from jarvis.paths import data_dir
    reminder_dir = data_dir("data")
    reminder_dir.mkdir(parents=True, exist_ok=True)
    reminder_path = reminder_dir / "reminders.json"
    reminder_id = f"rem-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    entry = {
        "reminder_id": reminder_id,
        "title": title,
        "due": due,
        "notes": notes,
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending",
    }
    try:
        existing = []
        if reminder_path.exists():
            try:
                with open(reminder_path, "r") as f:
                    existing = json.load(f)
                    if not isinstance(existing, list):
                        existing = []
            except Exception:
                existing = []
        existing.append(entry)
        with open(reminder_path, "w") as f:
            json.dump(existing, f, indent=2)
        return {"reminder_id": reminder_id, "status": "created"}
    except Exception as e:
        return {"error": str(e), "details": type(e).__name__}


# ---------------------------------------------------------------------------
# Tool registry and schema
# ---------------------------------------------------------------------------

TOOLS = {
    "search_memories": {
        "schema": {
            "type": "function",
            "function": {
                "name": "search_memories",
                "description": "Search the user's vector memory store for relevant past entries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural language search query."},
                        "source": {"type": "string", "description": "Optional source filter (e.g., calendar, files, manual)."},
                        "n": {"type": "integer", "description": "Number of results to return (default 8)."},
                    },
                    "required": ["query"],
                },
            },
        },
        "fn": search_memories,
    },
    "check_calendar": {
        "schema": {
            "type": "function",
            "function": {
                "name": "check_calendar",
                "description": "Retrieve calendar events for a date range.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "range_start": {"type": "string", "description": "ISO-8601 start of window."},
                        "range_end": {"type": "string", "description": "ISO-8601 end of window."},
                    },
                },
            },
        },
        "fn": check_calendar,
    },
    "get_entity_context": {
        "schema": {
            "type": "function",
            "function": {
                "name": "get_entity_context",
                "description": "Look up all memories associated with a named entity (person, place, topic).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Entity name to resolve."},
                    },
                    "required": ["name"],
                },
            },
        },
        "fn": get_entity_context,
    },
    "summarize": {
        "schema": {
            "type": "function",
            "function": {
                "name": "summarize",
                "description": "Combine and shorten a list of memory contents into a brief summary.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of memory IDs to summarize.",
                        },
                    },
                    "required": ["memory_ids"],
                },
            },
        },
        "fn": summarize,
    },
    "search_web": {
        "schema": {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Perform a web search via DuckDuckGo and return top results.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query string."},
                    },
                    "required": ["query"],
                },
            },
        },
        "fn": search_web,
    },
    "create_reminder": {
        "schema": {
            "type": "function",
            "function": {
                "name": "create_reminder",
                "description": "Create a new reminder entry for the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short reminder title."},
                        "due": {"type": "string", "description": "Optional ISO-8601 due date."},
                        "notes": {"type": "string", "description": "Optional long-form notes."},
                    },
                    "required": ["title"],
                },
            },
        },
        "fn": create_reminder,
    },
}

TOOLS_SCHEMA = [v["schema"] for v in TOOLS.values()]


def execute_tool(name, session_store, args):
    tool = TOOLS.get(name)
    if not tool:
        return {"error": f"unknown tool: {name}"}
    return tool["fn"](session_store, args)
