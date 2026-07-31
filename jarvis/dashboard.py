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
from fastapi.responses import HTMLResponse
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
        rows += f"""
        <tr>
          <td>{tier_badge}</td>
          <td><span class="muted">{ts}</span></td>
          <td>{src}</td>
          <td><span class="muted">{m.get("route", "unclassified")}</span></td>
          <td>{content_preview}</td>
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
        <tr><th>Tier</th><th>Timestamp</th><th>Source</th><th>Route</th><th>Content</th></tr>
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


# ── CLI entry point ────────────────────────────────────────────────────────────

def run_dashboard(port: int = DEFAULT_PORT, daemon_url: str = DEFAULT_DAEMON_URL):
    """Start the dashboard server."""
    import uvicorn

    print(f"Starting Jarvis Dashboard on http://0.0.0.0:{port}")
    print(f"Daemon URL: {daemon_url}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run_dashboard()

