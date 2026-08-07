"""
jarvis/dashboard.py — Lightweight FastAPI dashboard for Jarvis.

Serves HTML pages that read from the running daemon (default http://127.0.0.1:8765)
and the local SQLite Store.  Uses HTMX for partial-page updates — no heavy
frontend framework.

Run standalone:
    from jarvis.dashboard import app
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8766)

Or via CLI:
    jarvis dashboard --port 8766
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jarvis.store import Store

# ── Configuration ────────────────────────────────────────────────────────────

DEFAULT_DAEMON_URL = "http://127.0.0.1:8765"
DEFAULT_PORT = 8766

# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(title="Jarvis Dashboard", docs_url=None, redoc_url=None)

# Static files directory
_STATIC_DIR = Path(__file__).parent / "dashboard" / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/dashboard/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fetch_daemon(path: str, daemon_url: str = DEFAULT_DAEMON_URL) -> dict | list | None:
    """Fetch JSON from the daemon HTTP API. Returns None on failure."""
    url = f"{daemon_url.rstrip('/')}{path}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def _get_store() -> Store:
    """Open a Store instance for direct DB reads."""
    return Store()


# ── HTML layout ──────────────────────────────────────────────────────────────

_LAYOUT = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Jarvis Dashboard</title>
  <script src="https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           margin: 0; background: #0f1117; color: #e0e0e0; }
    .header { background: #1a1c23; padding: 14px 24px; border-bottom: 1px solid #333; }
    .header h1 { margin: 0; font-size: 1.3em; color: #4fc3f7; }
    .nav { display: flex; gap: 16px; margin-top: 8px; }
    .nav a { color: #81d4fa; text-decoration: none; padding: 4px 12px; border-radius: 4px;
             background: #2a2d35; transition: background 0.2s; }
    .nav a:hover { background: #3a3f4a; }
    .container { max-width: 960px; margin: 0 auto; padding: 24px; }
    .card { background: #1a1c23; border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #2a2d35; }
    th { color: #4fc3f7; font-weight: 600; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; }
    .badge-raw { background: #2e245e; color: #b39ddb; }
    .badge-session { background: #1b3a2a; color: #81c784; }
    .badge-reflection { background: #2e1a0f; color: #ffb74d; }
    .badge-arc { background: #311b92; color: #ce93d8; }
    .badge-escalated { background: #3e152a; color: #e57373; }
    .badge-unclassified { background: #333; color: #aaa; }
    .muted { color: #666; font-size: 0.85em; }
    .stat { display: inline-block; margin-right: 24px; }
    .stat .num { font-size: 1.8em; font-weight: bold; color: #4fc3f7; }
    .stat .label { font-size: 0.8em; color: #888; }
  </style>
</head>
<body>
  <div class="header">
    <h1>Jarvis Dashboard</h1>
    <div class="nav">
      <a href="/dashboard/" hx-get="/dashboard/" hx-push-url="true" hx-trigger="click">Overview</a>
      <a href="/dashboard/memories" hx-get="/dashboard/memories" hx-push-url="true" hx-trigger="click">Memories</a>
      <a href="/dashboard/graph" hx-get="/dashboard/graph" hx-push-url="true" hx-trigger="click">Knowledge Graph</a>
      <a href="/dashboard/alerts" hx-get="/dashboard/alerts" hx-push-url="true" hx-trigger="click">Alerts</a>
      <a href="/dashboard/consolidation" hx-get="/dashboard/consolidation" hx-push-url="true" hx-trigger="click">Consolidation</a>
      <a href="/dashboard/thoughts" hx-get="/dashboard/thoughts" hx-push-url="true" hx-trigger="click">💭 Thoughts</a>
      <a href="/dashboard/queue" hx-get="/dashboard/queue" hx-push-url="true" hx-trigger="click">📋 Queue</a>
    </div>
  </div>
  <div class="container" id="content">
    {content}
  </div>
</body>
</html>
"""


def _page(title: str, content: str) -> HTMLResponse:
    """Wrap content in the standard layout."""
    html = _LAYOUT.replace("{content}", f"<h2>{title}</h2>{content}")
    return HTMLResponse(content=html)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    """Overview page with daemon status and memory stats."""
    daemon = _fetch_daemon("/status") or {}
    store = _get_store()
    try:
        stats = store.stats()
    except Exception:
        stats = []
    finally:
        store.close()

    total = sum(s["count"] for s in stats)
    tier_counts = {}
    for s in stats:
        tier_counts[s["tier"]] = tier_counts.get(s["tier"], 0) + s["count"]

    content = f"""
    <div class="card">
      <h3>Daemon Status</h3>
      <p><span class="muted">PID:</span> {daemon.get('pid', '—')}</p>
      <p><span class="muted">Uptime:</span> {daemon.get('uptime_seconds', '—')}s</p>
      <p><span class="muted">Last ingest:</span> {daemon.get('last_ingest_ts', '—')}</p>
      <p><span class="muted">Pending queue:</span> {daemon.get('pending_queue_depth', 0)}</p>
      <p><span class="muted">Retry queue:</span> {daemon.get('retry_queue_depth', 0)}</p>
      <p><span class="muted">Conflicts:</span> {daemon.get('conflict_count', 0)}</p>
    </div>
    <div class="card">
      <h3>Memory Overview</h3>
      <div>
        <span class="stat"><span class="num">{total}</span><div class="label">Total Memories</div></span>
        <span class="stat"><span class="num">{len(tier_counts)}</span><div class="label">Tiers</div></span>
      </div>
      <table>
        <tr><th>Tier</th><th>Count</th><th>Source</th></tr>
        {''.join(f'<tr><td><span class="badge badge-{s["tier"]}">{s["tier"]}</span></td><td>{s["count"]}</td><td>{s["source"]}</td></tr>' for s in stats)}
      </table>
    </div>
    """
    return _page("Overview", content)




@app.get("/dashboard/memories", response_class=HTMLResponse)
def dashboard_memories(request: Request, source: str | None = None, tier: str | None = None, route: str | None = None, n: int = 50):
    """Browse memories with filters."""
    store = _get_store()
    try:
        if source:
            memories = store.get_by_route(source, limit=n)
        elif tier:
            memories = store.get_by_tier(tier, limit=n)
        else:
            memories = store.get_recent_raw(hours=48, limit=n)
    except Exception:
        memories = []
    links = {}
    try:
        links = store.lookup_entities([m["id"] for m in memories]) if memories else {}
    finally:
        store.close()

    rows = ""
    for m in memories:
        ts = m.get("timestamp", "—")
        src = m.get("source", "—")
        tier_badge = f'<span class="badge badge-{m.get("tier", "raw")}">{m.get("tier", "raw")}</span>'
        content_preview = (m.get("content") or "")[:200]
        if len(m.get("content", "")) > 200:
            content_preview += "…"
        ents = links.get(m["id"]) or []
        ent_html = ", ".join(f'<span class="muted">{e["name"]}</span>' for e in ents) if ents else "<span class=\"muted\">—</span>"
        rows += f"""
        <tr>
          <td>{tier_badge}</td>
          <td><span class="muted">{ts}</span></td>
          <td>{src}</td>
          <td><span class="muted">{m.get("route", "unclassified")}</span></td>
          <td>{content_preview}</td>
          <td>{ent_html}</td>
        </tr>
        """

    content = f"""
    <div class="card">
      <form hx-get="/dashboard/memories" hx-push-url="true" style="margin-bottom:12px;">
        <input type="text" name="source" placeholder="Filter by source" style="padding:4px 8px;margin-right:8px;">
        <input type="text" name="tier" placeholder="Filter by tier" style="padding:4px 8px;margin-right:8px;">
        <input type="text" name="route" placeholder="Filter by route" style="padding:4px 8px;margin-right:8px;">
        <button type="submit" style="padding:4px 12px;">Filter</button>
      </form>
      <table>
        <tr><th>Tier</th><th>Timestamp</th><th>Source</th><th>Route</th><th>Content</th><th>Entities</th></tr>
        {rows}
      </table>
    </div>
    """
    return _page("Memories", content)


@app.get("/dashboard/graph", response_class=HTMLResponse)
def dashboard_graph(request: Request, q: str | None = None):
    """Force-directed entity relationship graph."""
    daemon = _fetch_daemon("/entities?q=" + (q or "")) or []

    nodes_js = "[]"
    edges_js = "[]"
    if daemon:
        nodes = []
        for e in daemon:
            nodes.append({"id": e.get("id", ""), "name": e.get("canonical_name", ""), "type": e.get("entity_type", "")})
        edges = []
        for e in daemon:
            eid = e.get("id", "")
            rels = _fetch_daemon(f"/relationships?entity={eid}") or []
            for r in rels:
                edges.append({
                    "source": r.get("source_name", r.get("source_entity", "")),
                    "target": r.get("target_name", r.get("target_entity", "")),
                    "relation": r.get("relation_type", ""),
                })
        nodes_js = json.dumps(nodes)
        edges_js = json.dumps(edges)

    content = f"""
    <div class="card">
      <form hx-get="/dashboard/graph" hx-push-url="true" style="margin-bottom:12px;">
        <input type="text" name="q" placeholder="Search entities…" value="{q or ''}" style="padding:4px 8px;">
        <button type="submit" style="padding:4px 12px;">Search</button>
      </form>
      <div id="graph" style="height:400px;background:#0f1117;border:1px solid #333;border-radius:4px;"></div>
      <p class="muted">Showing {len(daemon)} entity(ies). Click an entity to see details.</p>
    </div>
    <script>
      const nodes = {nodes_js};
      const edges = {edges_js};
      const container = document.getElementById('graph');
      if (nodes.length === 0) {{
        container.innerHTML = '<p class="muted">No entities found. Run <code>jarvis graph build</code> to extract entities.</p>';
      }} else {{
        container.innerHTML = '<p class="muted">Entities: ' +
          nodes.map(n => n.name + ' (' + n.type + ')').join(', ') + '</p>';
        if (edges.length > 0) {{
          container.innerHTML += '<p class="muted">Relationships: ' +
            edges.map(e => e.source + ' → ' + e.target + ' (' + e.relation + ')').join('; ') + '</p>';
        }}
      }}
    </script>
    """
    return _page("Knowledge Graph", content)



@app.get("/dashboard/alerts", response_class=HTMLResponse)
def dashboard_alerts(request: Request):
    """Show escalated memories."""
    store = _get_store()
    try:
        rows = store.conn.execute(
            "SELECT id, timestamp, source, content, tags, route FROM memories "
            "WHERE route = 'escalate' AND superseded = 0 ORDER BY timestamp DESC LIMIT 50"
        ).fetchall()
        alerts = [dict(r) for r in rows]
    except Exception:
        alerts = []
    finally:
        store.close()

    if not alerts:
        content = '<p class="muted">No alerts. All clear!</p>'
    else:
        rows_html = ""
        for a in alerts:
            content_preview = (a.get("content") or "")[:300]
            rows_html += f"""
            <tr>
              <td><span class="badge badge-escalated">ESCALATED</span></td>
              <td><span class="muted">{a.get("timestamp", "—")}</span></td>
              <td>{a.get("source", "—")}</td>
              <td>{content_preview}</td>
            </tr>
            """
        content = f"""
        <table>
          <tr><th>Status</th><th>Timestamp</th><th>Source</th><th>Content</th></tr>
          {rows_html}
        </table>
        """
    return _page("Alerts", content)


@app.get("/dashboard/consolidation", response_class=HTMLResponse)
def dashboard_consolidation(request: Request):
    """Show consolidation status and logs."""
    store = _get_store()
    try:
        stats = store.stats()
        log_rows = store.conn.execute(
            "SELECT ts, memory_id, route, confidence, applied FROM decision_log ORDER BY ts DESC LIMIT 50"
        ).fetchall()
    except Exception:
        stats = []
        log_rows = []
    finally:
        store.close()

    tier_counts = {}
    for s in stats:
        tier_counts[s["tier"]] = tier_counts.get(s["tier"], 0) + s["count"]

    stats_html = "".join(
        f'<span class="stat"><span class="num">{c}</span><div class="label">{t}</div></span>'
        for t, c in tier_counts.items()
    )

    if log_rows:
        log_table = "<table><tr><th>Timestamp</th><th>Memory ID</th><th>Route</th><th>Confidence</th><th>Applied</th></tr>"
        for r in log_rows:
            log_table += f"<tr><td><span class='muted'>{r['ts']}</span></td><td><span class='muted'>{r['memory_id'][:12]}…</span></td><td>{r['route']}</td><td>{r['confidence']}</td><td>{'✅' if r['applied'] else '—'}</td></tr>"
        log_table += "</table>"
    else:
        log_table = '<p class="muted">No consolidation logs yet.</p>'

    content = f"""
    <div class="card">
      <h3>Memory Tier Distribution</h3>
      <div>{stats_html}</div>
    </div>
    <div class="card">
      <h3>Decision Log (last 50)</h3>
      {log_table}
    </div>
    """
    return _page("Consolidation", content)


# ── Mayor: Thoughts + Queue ──────────────────────────────────────────────────

@app.get("/dashboard/thoughts", response_class=HTMLResponse)
def dashboard_thoughts(request: Request):
    """Submit ideas to the Mayor and see recent submissions."""
    import urllib.request, json
    tasks = []
    try:
        from jarvis.task_queue import TaskQueue
        tq = TaskQueue()
        try:
            tasks = tq.list_tasks(limit=20)
        finally:
            tq.close()
    except Exception:
        pass

    rows = ""
    for t in tasks[:20]:
        sc = {"pending_review": "#ffb74d", "approved": "#81c784", "in_progress": "#4fc3f7", "completed": "#666", "blocked": "#e57373"}.get(t.get("status"), "#666")
        rows += f'<tr><td><span style="color:{sc}">●</span> {t.get("status","?")}</td><td>{t.get("agent","?")}</td><td>{t.get("title","?")[:60]}</td><td><span class="muted">{t.get("created_at","?")[:19]}</span></td></tr>'

    content = f'''
    <div class="card">
      <h3>Submit an Idea</h3>
      <p class="muted">Type your idea below (use Fn-Fn for macOS dictation). The Mayor will parse it into a task.</p>
      <form id="idea-form" style="margin-bottom:16px;">
        <textarea id="idea-text" placeholder="e.g. the dashboard graph should show entity connections as a force-directed network"
          style="width:100%;min-height:80px;background:#0f1117;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:12px;"></textarea>
        <button type="submit" style="margin-top:8px;padding:8px 24px;background:#4fc3f7;color:#0f1117;border:none;border-radius:4px;cursor:pointer;font-weight:bold;">Submit Idea</button>
        <span id="idea-result" style="margin-left:16px;"></span>
      </form>
    </div>
    <div class="card">
      <h3>Recent Tasks</h3>
      <table><tr><th>Status</th><th>Agent</th><th>Title</th><th>Created</th></tr>{rows}</table>
    </div>
    <script>
      document.getElementById('idea-form').addEventListener('submit', async (e) => {{
        e.preventDefault();
        const text = document.getElementById('idea-text').value;
        const r = document.getElementById('idea-result');
        r.innerHTML = '<span class="muted">Submitting...</span>';
        try {{
          const resp = await fetch('/api/idea', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{idea:text,source:'dashboard'}})}});
          const d = await resp.json();
          if(d.task_id){{r.innerHTML='<span style="color:#81c784">✅ Queued as '+d.agent+' task</span>';document.getElementById('idea-text').value='';setTimeout(()=>location.reload(),1500);}}
          else{{r.innerHTML='<span style="color:#e57373">❌ '+(d.error||'Failed')+'</span>';}}
        }} catch(err){{r.innerHTML='<span style="color:#e57373">❌ Mayor not running</span>';}}
      }});
    </script>
    '''
    return _page("Thoughts", content)

@app.get("/dashboard/queue", response_class=HTMLResponse)
def dashboard_queue(request: Request):
    """Task queue with approve/reject buttons."""
    import urllib.request, json
    tasks = []
    try:
        req = urllib.request.Request("/api/tasks?limit=50")
        with urllib.request.urlopen(req, timeout=5) as resp:
            tasks = json.loads(resp.read().decode()).get("tasks", [])
    except Exception:
        return _page("Task Queue", '<p class="muted">⚠️ Mayor is not running. Start it with: <code>jarvis mayor</code></p>')

    rows = ""
    for t in tasks:
        st = t.get("status", "?")
        sc = {"pending_review": "#ffb74d", "approved": "#81c784", "in_progress": "#4fc3f7", "completed": "#666", "blocked": "#e57373"}.get(st, "#666")
        actions = ""
        if st == "pending_review":
            actions = f'<button hx-post="/api/tasks/approve?id={t["id"]}" hx-swap="none" onclick="this.closest(\'tr\').remove()" style="padding:4px 8px;background:#4fc3f7;color:#0f1117;border:none;border-radius:3px;cursor:pointer;">✓</button>'
        elif st == "completed" and t.get("commit_hash"):
            actions = f'<span class="muted">{t["commit_hash"][:8]}</span>'
        rows += f'<tr><td><span style="color:{sc}">●</span> {st}</td><td>{t.get("agent","?")}</td><td>{t.get("title","?")[:50]}</td><td>{t.get("priority",3)}</td><td><span class="muted">{t.get("created_at","?")[:19]}</span></td><td>{actions}</td></tr>'

    pending = len([t for t in tasks if t.get("status") == "pending_review"])
    btn = f'<button hx-post="/api/tasks/approve?all=true" hx-swap="none" onclick="setTimeout(()=>location.reload(),500)" style="padding:6px 16px;background:#81c784;color:#0f1117;border:none;border-radius:4px;cursor:pointer;font-weight:bold;">Approve All ({pending})</button>' if pending > 0 else ""

    content = f'''
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <h3>Task Queue</h3>{btn}
      </div>
      <table><tr><th>Status</th><th>Agent</th><th>Title</th><th>Pri</th><th>Created</th><th>Actions</th></tr>{rows}</table>
    </div>
    '''
    return _page("Task Queue", content)


# ── Mayor API Routes ─────────────────────────────────────────────────────────

_mayor_instance: "Mayor" | None = None


def _start_mayor():
    """Start the Mayor background loop in a thread (called at startup)."""
    global _mayor_instance
    if _mayor_instance is not None:
        return
    try:
        from jarvis.mayor import Mayor
        import threading
        _mayor_instance = Mayor()
        t = threading.Thread(target=_mayor_instance.run_loop, daemon=True)
        t.start()
    except Exception as e:
        print(f"Mayor background loop not started: {e}")


@app.post("/api/idea")
def api_submit_idea(request: Request, payload: dict):
    """Submit an idea to the Mayor."""
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from jarvis.task_queue import TaskQueue
    from jarvis.mayor import parse_idea
    try:
        idea = payload.get("idea", "")
        source = payload.get("source", "dashboard")
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not idea:
        return JSONResponse({"error": "no idea provided"}, status_code=400)
    try:
        tq = TaskQueue()
        try:
            task_data = parse_idea(idea)
            if not task_data:
                task_data = {"agent": "code", "title": idea[:80], "description": idea, "priority": 3}
            task_id = tq.add_task(title=task_data["title"], description=task_data.get("description", ""), agent=task_data.get("agent", "code"), priority=task_data.get("priority", 3), source=source, raw_idea=idea)
            return JSONResponse({"task_id": task_id, "agent": task_data.get("agent"), "title": task_data["title"], "status": "pending_review"})
        finally:
            tq.close()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/tasks")
def api_tasks(status: str | None = None, limit: int = 50):
    """List tasks in the queue."""
    from jarvis.task_queue import TaskQueue
    tq = TaskQueue()
    try:
        return JSONResponse({"tasks": tq.list_tasks(status=status, limit=limit)})
    finally:
        tq.close()


@app.post("/api/tasks/approve")
def api_approve_task(request: Request):
    """Approve a task (or all pending)."""
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from jarvis.task_queue import TaskQueue
    q = dict(request.query_params)
    tq = TaskQueue()
    try:
        task_id = q.get("id")
        if q.get("all", "").lower() == "true":
            return JSONResponse({"approved": tq.approve_all()})
        elif task_id:
            return JSONResponse({"success": tq.approve_task(task_id), "task_id": task_id})
        return JSONResponse({"error": "provide ?id= or ?all=true"}, status_code=400)
    finally:
        tq.close()


@app.post("/api/tasks/reject")
def api_reject_task(request: Request):
    """Reject a pending task."""
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from jarvis.task_queue import TaskQueue
    task_id = request.query_params.get("id")
    if not task_id:
        return JSONResponse({"error": "provide ?id="}, status_code=400)
    tq = TaskQueue()
    try:
        return JSONResponse({"success": tq.reject_task(task_id), "task_id": task_id})
    finally:
        tq.close()


@app.get("/api/status")
def api_status():
    """Mayor status."""
    from jarvis.task_queue import TaskQueue
    tq = TaskQueue()
    try:
        return JSONResponse({"mode": "coding", "queue_stats": tq.stats(), "running": _mayor_instance is not None})
    finally:
        tq.close()


# ── Knowledge Graph API Routes ─────────────────────────────────────────────────

_ENTITY_COLS = "id, canonical_name, entity_type, source_count, first_seen, last_seen"


@app.get("/api/entities")
def api_entities(q: str | None = None, type: str | None = None, limit: int = 50):
    """List/search knowledge graph entities.

    Supports:
      ?q=      — substring (and fuzzy) search over canonical names
      ?type=   — filter by entity_type (e.g. person, organization, place)
      ?limit=  — max results (clamped to 1..500, default 50)
    """
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500
    store = _get_store()
    try:
        where = []
        params: list = []
        if q:
            where.append("canonical_name LIKE ?")
            params.append(f"%{q}%")
        if type:
            where.append("entity_type = ?")
            params.append(type)
        sql = f"SELECT {_ENTITY_COLS} FROM entities"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY source_count DESC, canonical_name ASC LIMIT ?"
        rows = store.conn.execute(sql, (*params, limit)).fetchall()
        entities = [dict(r) for r in rows]
        # When searching, also surface the closest exact/fuzzy match even if its
        # canonical name doesn't contain the literal substring (mirrors daemon).
        if q:
            from jarvis.graph import resolve_entity
            eid = resolve_entity(store, q)
            if eid and not any(e["id"] == eid for e in entities):
                row = store.conn.execute(
                    f"SELECT {_ENTITY_COLS} FROM entities WHERE id = ?",
                    (eid,),
                ).fetchone()
                if row:
                    entities.insert(0, dict(row))
        return JSONResponse({"entities": entities, "count": len(entities)})
    finally:
        store.close()


@app.get("/api/entities/{entity_id}/relationships")
def api_entity_relationships(entity_id: str):
    """Return relationship edges for a single entity (reuses graph.get_related)."""
    store = _get_store()
    try:
        row = store.conn.execute(
            f"SELECT {_ENTITY_COLS} FROM entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "entity not found"}, status_code=404)
        from jarvis.graph import get_related
        relationships = get_related(store, entity_id)
        return JSONResponse({
            "entity_id": entity_id,
            "relationships": relationships,
            "count": len(relationships),
        })
    finally:
        store.close()

# ── Thin-client read/write API (Round 5) ──────────────────────────────────────
# Handlers are sync `def` so FastAPI/starlette run them in a worker thread; heavy
# Store/LLM work must never block the single event loop (was async -> intermittent
# connection resets on /api/health while the loop was busy).
import os as _os
import time as _time
_SERVER_START = _time.time()


def _client_token_ok(host: str, supplied: str) -> bool:
    token = _os.environ.get("JARVIS_TOKEN")
    if not token:
        return True
    if supplied == token:
        return True
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    return False


def _host_ok(request: Request) -> bool:
    return _client_token_ok(request.client.host if request.client else "", request.headers.get("X-Jarvis-Token", ""))


@app.post("/api/remember")
def api_remember(request: Request, payload: dict):
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from jarvis.brain import Brain
    items = payload.get("memories") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return JSONResponse({"error": "expected a list of memories"}, status_code=400)
    store = _get_store()
    added = 0
    skipped = 0
    try:
        brain = Brain(store)
        for it in items:
            if not isinstance(it, dict):
                skipped += 1
                continue
            content = it.get("content") or ""
            if not content.strip():
                skipped += 1
                continue
            try:
                if brain.remember(content, source=it.get("source", "device"), tags=list(it.get("tags") or []), classify=False):
                    added += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1
    finally:
        store.close()
    return {"added": added, "skipped": skipped}


@app.post("/api/backfill")
def api_backfill(request: Request, payload: dict):
    """Bulk-import full memory records, preserving original field values.

    Unlike /api/remember (which re-timestamps, re-chunks, and re-tiers via
    Brain.remember), this is the faithful one-time migration path: it inserts
    each record exactly as written on the source store — same id, source,
    source_id, timestamp, tier, route, tags, metadata — and recomputes the
    embedding locally (deterministic for the shared embed model), so the
    canonical store is byte-equivalent in SQLite terms to what the client had.

    Body: {"memories": [...], "verify": <bool>}
    Each memory dict may carry: id, source, source_id, timestamp, content,
    tags (list), metadata (dict), tier, route, expires_at, consolidated_from,
    superseded (bool). Returns {added, skipped}.
    """
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    items = payload.get("memories") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return JSONResponse({"error": "expected a list of memories"}, status_code=400)

    from jarvis.embed import get_embeddings
    store = _get_store()
    added = 0
    skipped = 0
    try:
        # Drop anything unusable, then precompute embeddings in one batched call.
        valid = []
        for it in items:
            if not isinstance(it, dict):
                skipped += 1
                continue
            content = it.get("content") or ""
            if not content.strip():
                skipped += 1
                continue
            valid.append(it)
        if valid:
            try:
                embs = get_embeddings([m["content"] for m in valid])
            except Exception:  # noqa: BLE001 - fall back to injecting no embeddings on embed failure
                embs = []
            for m, emb in zip(valid, embs):
                try:
                    store.add(
                        fid=m.get("id") or None,
                        source=m.get("source", "device"),
                        source_id=m.get("source_id") or "",
                        # Chroma rejects None metadata values, so never pass None.
                        timestamp=m.get("timestamp") or "",
                        content=m["content"],
                        tags=list(m.get("tags") or []),
                        metadata=dict(m.get("metadata") or {}),
                        embedding=emb,
                        tier=m.get("tier", "raw") or "raw",
                        expires_at=m.get("expires_at"),
                        consolidated_from=m.get("consolidated_from"),
                        superseded=bool(m.get("superseded", False)),
                        route=m.get("route", "unclassified") or "unclassified",
                    )
                    added += 1
                except Exception:  # noqa: BLE001 - any single bad record is skipped, never fatal
                    skipped += 1
    finally:
        store.close()
    return {"added": added, "skipped": skipped}


@app.get("/api/search")
def api_search(request: Request, q: str = "", n: int = 10, source: str | None = None):
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from jarvis.embed import get_embedding
    if not q.strip():
        return JSONResponse({"error": "q is required"}, status_code=400)
    store = _get_store()
    try:
        emb = get_embedding(q)
        rows = store.search(emb, n_results=n, source_filter=source)
        mems = [{k: r.get(k) for k in ("id", "source", "timestamp", "tier", "route", "content", "tags")} for r in rows]
        links = store.lookup_entities([r.get("id") for r in rows]) if rows else {}
        entities = {mid: [{"name": e["name"], "entity_type": e["entity_type"]} for e in ents] for mid, ents in links.items()}
        return {"query": q, "count": len(rows), "memories": mems, "entities": entities}
    finally:
        store.close()


@app.post("/api/chat")
def api_chat(request: Request, payload: dict):
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from jarvis.agent import run_turn
    from jarvis.sessions import SessionDB
    message = payload.get("message") or ""
    if not message.strip():
        return JSONResponse({"error": "message is required"}, status_code=400)
    store = _get_store()
    sdb = SessionDB()
    try:
        session_id = payload.get("session_id")
        if not session_id:
            session_id = sdb.create_session(title="API Chat")
        answer, tool_log = run_turn(
            message, session_id,
            max_steps=int(payload.get("max_steps", 8) or 8),
            session_db=sdb, store_db=store,
            verbose=bool(payload.get("verbose", False)),
            model=payload.get("model") or None,
        )
        return {"answer": answer, "session_id": session_id, "tool_log": tool_log}
    finally:
        store.close()
        sdb.close()


@app.get("/api/sessions")
def api_sessions():
    from jarvis.sessions import SessionDB
    sdb = SessionDB()
    try:
        return {"sessions": sdb.list_sessions(limit=50)}
    finally:
        sdb.close()


@app.post("/api/sessions")
def api_create_session(request: Request, payload: dict | None = None):
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from jarvis.sessions import SessionDB
    payload = payload or {}
    sdb = SessionDB()
    try:
        sid = sdb.create_session(title=payload.get("title", "Chat"))
        return {"session_id": sid}
    finally:
        sdb.close()


@app.get("/api/sessions/{sid}/messages")
def api_session_messages(sid: str):
    from jarvis.sessions import SessionDB
    sdb = SessionDB()
    try:
        return {"session_id": sid, "messages": sdb.get_messages(sid, limit=200)}
    finally:
        sdb.close()


@app.get("/api/export")
def api_export(request: Request, fmt: str = "json"):
    if not _host_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    store = _get_store()
    try:
        rows = store.conn.execute("SELECT * FROM memories WHERE superseded = 0 ORDER BY timestamp DESC").fetchall()
        mems = [dict(r) for r in rows]
        if fmt == "md":
            from fastapi.responses import Response
            lines = ["# Jarvis Memory Export\n"]
            for m in mems:
                lines.append(f"## [{m['source']}] {m['timestamp']}\n")
                lines.append(m["content"] + "\n")
            return Response("\n".join(lines), media_type="text/markdown")
        return JSONResponse({"count": len(mems), "memories": mems})
    finally:
        store.close()


@app.get("/api/health")
async def api_health(request: Request):
    """Pure liveness — async + does NO blocking work, so it is served directly on
    the event loop (never queued in the threadpool) and always responds fast,
    even while other handlers are deep in Store/LLM calls."""
    return {"ok": True, "mode": _os.environ.get("JARVIS_MODE", "local"),
            "uptime": round(_time.time() - _SERVER_START, 1)}


@app.get("/api/health/deep")
def api_health_deep(request: Request):
    """Store-aware health for the notifier/ops — may be slower under inference load."""
    store = None
    n = None
    try:
        store = _get_store()
        n = store.conn.execute("SELECT COUNT(*) FROM memories WHERE superseded = 0").fetchone()[0]
    except Exception:
        n = None
    finally:
        if store:
            store.close()
    return {"ok": True, "mode": _os.environ.get("JARVIS_MODE", "local"), "memories": n,
            "uptime": round(_time.time() - _SERVER_START, 1)}
# ── CLI entry point ────────────────────────────────────────────────────────────

def run_dashboard(port: int = DEFAULT_PORT, daemon_url: str = DEFAULT_DAEMON_URL):
    """Start the dashboard server with Mayor background loop."""
    import uvicorn

    from jarvis.inbox_ingest import start_background_ingester

    # Start the Mayor background loop (task dispatch, mode switching)
    _start_mayor()
    # Start the throttled box-inbox backlog ingester (same-process, embed-only)
    start_background_ingester()

    print(f"Starting Jarvis Dashboard on http://0.0.0.0:{port}")
    print(f"Daemon URL: {daemon_url}")
    print(f"Submit ideas: POST /api/idea")
    print(f"Task queue:   GET  /api/tasks")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run_dashboard()

