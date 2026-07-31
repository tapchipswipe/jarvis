import click
import json
import os
from pathlib import Path
from jarvis.store import Store
from jarvis.brain import Brain
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.classifier import classify
from jarvis.consolidation import run_daily, run_weekly, run_monthly
from jarvis.routes import classify_existing
from jarvis.sessions import SessionDB
from datetime import datetime, timedelta


def _get_rich_console():
    try:
        from rich.console import Console
        return Console(force_terminal=True, width=80)
    except Exception:
        return None


def _get_scheduled_scans() -> list[str]:
    lines = []
    try:
        import platform
        if platform.system() == "Darwin":
            cron_path = os.path.expanduser("~/jarvis/scripts/crontab.txt")
            if os.path.exists(cron_path):
                lines.append(f"macOS crontab: {cron_path}")
                with open(cron_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            lines.append(f"  {line}")
        elif platform.system() == "Windows":
            lines.append("Windows Task Scheduler:")
            lines.append("  Check Task Scheduler Library for tasks named 'Jarvis' or consolidation jobs.")
    except Exception:
        pass
    return lines


@click.group()
def cli():
    """Jarvis — local ambient memory agent."""
    pass


@cli.command()
@click.argument("text")
@click.option("--source", default="manual", help="Source tag")
@click.option("--tag", multiple=True, help="Tags to attach")
@click.option("--classify", is_flag=True, help="Run classifier after storing")
def remember(text, source, tag, classify):
    store = Store()
    brain = Brain(store)
    added = brain.remember(text, source=source, tags=list(tag), classify=classify)
    store.close()
    click.echo(f"Remembered {added} chunk(s).{' Classified.' if classify else ''}")


@cli.command()
@click.option("--source", default=None, help="Filter by source")
@click.option("--tag", default=None, help="Filter by tag")
@click.option("--tier", default=None, help="Filter by tier (raw, session, reflection, arc)")
@click.option("-n", default=20, help="Number of results")
def memories(source, tag, tier, n):
    store = Store()
    query = "SELECT * FROM memories WHERE superseded = 0"
    params = []
    if source:
        query += " AND source = ?"
        params.append(source)
    if tier:
        query += " AND tier = ?"
        params.append(tier)
    if tag:
        query += " AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)"
        params.append(tag)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(n)
    cur = store.conn.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    store.close()
    if not rows:
        click.echo("No memories found.")
        return
    for r in rows:
        tags = ", ".join(json.loads(r["tags"])) if r["tags"] else ""
        click.echo(f"[{r['tier']}] [{r['source']}] {r['timestamp']} {tags}")
        click.echo(f"  {r['content'][:200]}")
        click.echo(f"  id={r['id']}")
        click.echo("")


@cli.command()
@click.option("--days", default=7, help="Look back N days")
@click.option("-n", default=50, help="Number of results")
def timeline(days, n):
    store = Store()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    cur = store.conn.execute("SELECT * FROM memories WHERE superseded = 0 AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?", (cutoff, n))
    rows = [dict(r) for r in cur.fetchall()]
    store.close()
    if not rows:
        click.echo("No memories in timeline.")
        return
    for r in rows:
        tags = ", ".join(json.loads(r["tags"])) if r["tags"] else ""
        click.echo(f"{r['timestamp']} [{r['source']}] {tags}")
        click.echo(f"  {r['content'][:200]}")
        click.echo("")


@cli.command()
@click.argument("query")
@click.option("--source", default=None, help="Filter by source")
@click.option("-n", default=10, help="Number of results")
@click.option("--verbose", is_flag=True, help="Show detailed source context")
def search(query, source, n, verbose):
    store = Store()
    brain = Brain(store)
    response, memories = brain.query(query, n_results=n, source_filter=source, verbose=verbose)
    store.close()
    click.echo("--- Response ---")
    click.echo(response)
    click.echo("\n--- Sources ---")
    for m in memories:
        tags = ", ".join(json.loads(m["tags"])) if m["tags"] else ""
        click.echo(f"- [{m['source']}] [{m['tier']}] {m['timestamp']} {tags}")
        click.echo(f"  {m['content'][:120]}...")
        click.echo(f"  id={m['id']}")


@cli.command()
def status():
    store = Store()
    stats = store.stats()
    store.close()
    if not stats:
        click.echo("No memories yet. Run /chat or /remember to start.")
        return
    click.echo(f"{'Source':15s} | {'Tier':10s} | {'Route':15s} | {'Count':5s} | Oldest | Newest")
    click.echo("-" * 80)
    for s in stats:
        click.echo(f"{s['source']:15s} | {s['tier']:10s} | {s['route']:15s} | {s['count']:5d} | {s['oldest']} | {s['newest']}")


@cli.command()
@click.argument("memory_id")
@click.argument("correction_text")
def correct(memory_id, correction_text):
    store = Store()
    brain = Brain(store)
    added = brain.correct(memory_id, correction_text)
    store.close()
    click.echo(f"Correction recorded: {added} chunk(s). Original memory {memory_id} marked superseded.")


@cli.command()
@click.argument("memory_id")
@click.option("--model", default=None, help="Model override")
@click.option("--dry-run", is_flag=True, help="Show envelope without applying")
def classify(memory_id, model, dry_run):
    """Classify a memory with the local LLM classifier."""
    store = Store()
    row = store.conn.execute("SELECT * FROM memories WHERE id = ? AND superseded = 0", (memory_id,)).fetchone()
    if not row:
        store.close()
        click.echo("Memory not found.")
        return
    memory = dict(row)
    if memory.get("route") and memory.get("route") != "unclassified":
        click.echo(f"Memory already classified as route={memory['route']}. Use /correct for corrections.")
        store.close()
        return
    click.echo(f"Classifying memory {memory_id}...")
    envelope = classify_existing(store, memory, model=model)
    route = envelope.get("route", "escalate")
    confidence = envelope.get("confidence", "low")
    store.close()
    click.echo(f"Route: {route}")
    click.echo(f"Confidence: {confidence}")
    if envelope.get("escalate_reason"):
        click.echo(f"Reason: {envelope['escalate_reason']}")
    if envelope.get("action_atom"):
        click.echo(f"Action: {envelope['action_atom']}")
    if envelope.get("target_list"):
        click.echo(f"Target list: {envelope['target_list']}")
    if envelope.get("tag_seeds"):
        click.echo(f"Tags: {', '.join(envelope['tag_seeds'])}")
    click.echo(f"Applied: {'dry-run' if dry_run else 'yes'}")


@cli.command()
@click.option("--limit", default=50, help="Max memories to classify")
@click.option("--model", default=None, help="Model override")
@click.option("--dry-run", is_flag=True, help="Show envelopes without applying")
def classify_recent(limit, model, dry_run):
    """Classify all unclassified memories in bulk."""
    store = Store()
    rows = store.get_unclassified(limit=limit)
    if not rows:
        click.echo("No unclassified memories found.")
        store.close()
        return
    click.echo(f"Classifying {len(rows)} unclassified memories...")
    counts = {}
    for m in rows:
        env = classify_existing(store, m, model=model)
        route = env.get("route", "escalate")
        counts[route] = counts.get(route, 0) + 1
        click.echo(f"  {m['id'][:12]}... -> {route} ({env.get('confidence', 'low')})")
    store.close()
    click.echo("Summary:")
    for route, count in sorted(counts.items()):
        click.echo(f"  {route}: {count}")



@cli.command()
@click.option("--source", default="all", help="Target for sync: all, files, browser, calendar, email, photos, bookmarks, rss, system, deep, git, shell, kilo, notes, reminders, contacts, messages, photos_ocr")
def sync(source):
    from jarvis.collectors.sync_runner import run_sync
    click.echo(f"Running scheduled sync ({source})...")
    results = run_sync(source)
    click.echo("Sync complete:")
    for source_name, count in results.items():
        if isinstance(count, int):
            click.echo(f"  {source_name}: {count} items added")
        else:
            click.echo(f"  {source_name}: {count}")


@cli.command()
@click.option("--source", default="all", help="Target for scan: all, files, browser, calendar, email, photos, bookmarks, rss, system, deep, git, shell, notes, reminders, contacts, messages, devices")
def scan(source):
    """Run a live ingestion scan across all data sources with progress."""
    from jarvis.collectors.sync_runner import run_sync
    from jarvis.sync.push import get_lightspeed_stats
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

    scheduled = _get_scheduled_scans()
    if scheduled:
        click.echo("[bold]Scheduled scans:[/bold]")
        for line in scheduled:
            click.echo(f"  {line}")
    else:
        click.echo("[dim]No scheduled scans configured.[/dim]")
    click.echo("")

    if source == "all":
        local_targets = ["files", "ai_kilo", "browser", "ai_gemini", "calendar", "email", "photos", "bookmarks", "rss", "system", "deep", "git", "shell", "notes", "reminders", "contacts", "messages"]
        remote_check = True
    elif source == "devices":
        local_targets = []
        remote_check = True
    else:
        local_targets = [source]
        remote_check = False
    results = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task(f"Scanning ({source})", total=len(local_targets) + (1 if remote_check else 0))

        if local_targets:
            def _cb(name, result):
                results[name] = result
                status = f"{result}" if isinstance(result, int) else str(result)
                progress.update(task, description=f"[cyan]{name}[/cyan]: {status}", advance=1)
            run_sync("all" if source == "all" else source, progress_callback=_cb)

        if remote_check:
            progress.update(task, description="[magenta]devices[/magenta]: checking...", advance=0)
            stats = get_lightspeed_stats()
            if stats:
                total = sum(stats.get("sources", {}).values())
                results["devices"] = f"lightspeed ({total} memories)"
                progress.update(task, description=f"[magenta]devices[/magenta]: lightspeed ({total} memories)", advance=1)
            else:
                results["devices"] = "lightspeed (unreachable)"
                progress.update(task, description="[magenta]devices[/magenta]: lightspeed (unreachable)", advance=1)

    click.echo("")
    click.echo("[bold]Scan complete:[/bold]")
    for name, count in results.items():
        if isinstance(count, int):
            click.echo(f"  [green]{name}[/green]: {count} items added")
        else:
            click.echo(f"  [yellow]{name}[/yellow]: {count}")


@cli.command()
@click.option("--source", default=None, help="Filter by source")
@click.option("--tag", default=None, help="Filter by tag")
@click.option("--tier", default=None, help="Filter by tier (raw, session, reflection, arc)")
@click.option("--route", default=None, help="Filter by route (idea_capture, reference_note, context_list_update, escalate, unclassified)")
@click.option("--device", default=None, help="Filter by device prefix")
@click.option("-n", default=20, help="Number of results")
@click.option("--offset", default=0, help="Skip first N results")
def explore(source, tag, tier, route, device, n, offset):
    """View all stored memories with optional filters."""
    store = Store()
    query = "SELECT * FROM memories WHERE superseded = 0"
    params = []
    if source:
        query += " AND source = ?"
        params.append(source)
    if tier:
        query += " AND tier = ?"
        params.append(tier)
    if route:
        query += " AND route = ?"
        params.append(route)
    if tag:
        query += " AND tags LIKE ?"
        params.append(f"%{tag}%")
    if device:
        query += " AND source_id LIKE ?"
        params.append(f"{device}%")
    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([n * 3, offset])
    cur = store.conn.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    store.close()
    if not rows:
        click.echo("No memories found.")
        return
    click.echo(f"[bold]Memories (showing {len(rows)})[/bold]")
    for i, r in enumerate(rows[:n], 1):
        tags = ", ".join(json.loads(r["tags"])) if r["tags"] else ""
        device_tag = ""
        source_id = r.get("source_id", "")
        if "/" in source_id:
            device_tag = f" [dim]({source_id.split('/')[0]})[/dim]"
        route_tag = f" [yellow][{r.get('route', 'unclassified')}][/yellow]" if r.get("route") != "unclassified" else ""
        click.echo(f"\n{i}. [{r['tier']}] [{r['source']}] {r['timestamp']} {tags}{device_tag}{route_tag}")
        click.echo(f"   id={r['id']}")
        preview = r['content'][:200].replace('\n', ' ')
        click.echo(f"   {preview}{'...' if len(r['content']) > 200 else ''}")

    if len(rows) > n:
        click.echo(f"\n[dim]... and {len(rows) - n} more. Use --offset {offset + n} to continue.[/dim]")

@cli.command()
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "md"], case_sensitive=False), default="json", help="Export format: json or markdown")
@click.option("--output", "-o", default=None, help="Output path ('-' for stdout; default: timestamped file under the jarvis data dir)")
@click.option("--source", default=None, help="Filter by source")
@click.option("--tier", default=None, help="Filter by tier (raw, session, reflection, arc)")
def export(fmt, output, source, tier):
    """Export all memories to JSON or Markdown.

    Writes a timestamped file by default; use --output - to print to stdout.
    """
    fmt = "markdown" if fmt == "md" else fmt
    store = Store()
    try:
        query = "SELECT * FROM memories WHERE superseded = 0"
        params = []
        if source:
            query += " AND source = ?"
            params.append(source)
        if tier:
            query += " AND tier = ?"
            params.append(tier)
        query += " ORDER BY timestamp DESC"
        rows = [dict(r) for r in store.conn.execute(query, params).fetchall()]
    finally:
        store.close()

    for r in rows:
        r["tags"] = json.loads(r["tags"]) if r.get("tags") else []
        r["metadata"] = json.loads(r["metadata"]) if r.get("metadata") else {}

    if fmt == "json":
        payload = {
            "exported_at": datetime.utcnow().isoformat(),
            "count": len(rows),
            "memories": rows,
        }
        content = json.dumps(payload, indent=2, default=str)
    else:
        content = _render_markdown(rows)

    if output == "-":
        click.echo(content)
        return
    if output:
        out_path = Path(output)
    else:
        out_path = _export_default_dir() / _export_filename(fmt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    click.echo(f"Exported {len(rows)} memory/memories to {out_path}")


def _export_default_dir() -> Path:
    """Directory for timestamped exports (override with JARVIS_DATA_DIR)."""
    data_dir = os.environ.get("JARVIS_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "exports"
    return Path.home() / "jarvis" / "data" / "exports"


def _export_filename(fmt: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    ext = "md" if fmt == "markdown" else "json"
    return f"jarvis-export-{stamp}.{ext}"


def _render_markdown(rows: list[dict]) -> str:
    lines = ["# Jarvis Memory Export", ""]
    lines.append(f"**Exported:** {datetime.utcnow().isoformat()}")
    lines.append(f"**Memories:** {len(rows)}")
    lines.append("")
    for r in rows:
        tags = ", ".join(r.get("tags") or [])
        lines.append(f"## [{r.get('tier', 'raw')}] {r.get('timestamp', '')}")
        lines.append(f"- **Source:** {r.get('source', '')}")
        lines.append(f"- **ID:** {r.get('id', '')}")
        if tags:
            lines.append(f"- **Tags:** {tags}")
        if r.get("route") and r.get("route") != "unclassified":
            lines.append(f"- **Route:** {r.get('route')}")
        lines.append("")
        lines.append((r.get("content") or "").strip())
        lines.append("")
    return "\n".join(lines)


@cli.command()
@click.option("--model", default=lambda: os.environ.get("JARVIS_CHAT_MODEL"), help="Chat model override (defaults to JARVIS_CHAT_MODEL env, then agent fallback list)")
@click.option("--verbose", is_flag=True, help="Show tool calls and sources")
@click.option("--new", "is_new", is_flag=True, help="Start a fresh session")
@click.option("--resume", default=None, help="Resume session by ID")
@click.option("--max-steps", default=8, type=int, help="Max agent steps per turn")
def chat(model, verbose, is_new, resume, max_steps):
    """Chat with your Jarvis agent. Supports multi-turn dialogue with tool use."""
    session_db = SessionDB()
    store = Store()

    # Determine session ID
    session_id = None
    if resume:
        session_id = resume
        session = session_db.get_session(session_id)
        if not session:
            click.echo(f"Session {session_id} not found. Starting a new session.")
            is_new = True
            session_id = None
    if is_new or not session_id:
        title = click.prompt("Session title", default="New Chat")
        session_id = session_db.create_session(title=title)
        click.echo(f"New session: {session_id}")

    # Show history
    messages = session_db.get_messages(session_id, limit=20)
    if messages:
        click.echo(f"--- Session loaded ({len(messages)} messages) ---")
        for m in messages[-10:]:
            role_badge = {"user": "you", "assistant": "jarvis", "tool": "tool"}.get(m["role"], m["role"])
            content_preview = (m.get("content") or "")[:120]
            click.echo(f"  [{role_badge}] {content_preview}")

    click.echo("Commands: /quit  /clear  /sources")
    try:
        while True:
            user_input = click.prompt("you")
            if user_input.strip().lower() in ("/quit", "/exit", "/q"):
                click.echo("Session saved. Goodbye.")
                break
            if user_input.strip().lower() == "/clear":
                session_db.append_message(session_id, "user", "/clear")
                session_db.append_message(session_id, "assistant", "Session cleared.")
                click.echo("Session cleared.")
                continue
            if user_input.strip().lower() == "/sources":
                msgs = session_db.get_messages(session_id, limit=100)
                tool_msgs = [m for m in msgs if m["role"] == "system" or ("tool" in (m.get("tool_calls") or "{}"))]
                click.echo(f"Last {len(tool_msgs)} tool interactions:")
                for m in tool_msgs[-10:]:
                    tc = m.get("tool_calls") or "{}"
                    click.echo(f"  {m['role']}: {json.dumps(tc)[:100]}")
                continue

            answer, tool_log = run_turn(
                user_input, session_id, session_db,
                store_db=store, max_steps=max_steps, verbose=verbose, model=model
            )
            click.echo(f"jarvis: {answer}")
            if verbose and tool_log:
                click.echo(f"  [tools: {len(tool_log)} calls]")
                for entry in tool_log:
                    click.echo(f"    → {entry['tool']}({json.dumps(entry['args'])[:80]})")
    except click.Abort:
        click.echo("\nGoodbye.")
    finally:
        store.close()
        session_db.close()


def _show_alerts(brain: Brain):
    click.echo("--- Recent Brain Activity (last 24h) ---")
    activities = brain.get_recent_activity(hours=24, limit=20)
    if not activities:
        click.echo("No recent activity.")
        return
    by_source = {}
    for a in activities:
        src = a.get("source", "unknown")
        by_source.setdefault(src, []).append(a)
    for src, items in by_source.items():
        click.echo(f"\n[{src}] {len(items)} memories")
        for item in items[:3]:
            tags = ", ".join(json.loads(item["tags"])) if item["tags"] else ""
            click.echo(f"  {item['timestamp']} {tags}")
            click.echo(f"    {item['content'][:150]}")
        if len(items) > 3:
            click.echo(f"  ... and {len(items) - 3} more")


@cli.command()
@click.argument("feature_request")
@click.option("--status", default="requested", help="Initial status")
def upgrade(feature_request, status):
    store = Store()
    brain = Brain(store)
    added = brain.upgrade(feature_request, status=status)
    store.close()
    click.echo(f"Upgrade recorded: {added} chunk(s). Request: {feature_request}")


@cli.command()
@click.option("--hours", default=24, help="Look back N hours")
@click.option("-n", default=20, help="Number of results")
def alerts(hours, n):
    store = Store()
    brain = Brain(store)
    activities = brain.get_recent_activity(hours=hours, limit=n)
    store.close()
    if not activities:
        click.echo("No recent activity.")
        return
    click.echo(f"--- Recent Brain Activity (last {hours}h) ---")
    by_source = {}
    for a in activities:
        src = a.get("source", "unknown")
        by_source.setdefault(src, []).append(a)
    for src, items in by_source.items():
        click.echo(f"\n[{src}] {len(items)} memories")
        for item in items[:3]:
            tags = ", ".join(json.loads(item["tags"])) if item["tags"] else ""
            click.echo(f"  {item['timestamp']} {tags}")
            click.echo(f"    {item['content'][:150]}")
        if len(items) > 3:
            click.echo(f"  ... and {len(items) - 3} more")




@cli.command()
@click.argument("title")
@click.argument("body")
def notify(title, body):
    """Send a test notification (terminal-notifier → osascript → log)."""
    from jarvis.notify import send_notification
    send_notification(title, body)
    click.echo(f"Sent: [{title}] {body}")



consolidate = click.Group(name="consolidate")
cli.add_command(consolidate, name="consolidate")


@consolidate.command()
def daily():
    added = run_daily()
    click.echo(f"Daily consolidation complete. {added} new session summary(ies) stored.")


@consolidate.command()
def weekly():
    added = run_weekly()
    if added:
        click.echo(f"Weekly consolidation complete. {added} new reflection(s) stored.")
    else:
        click.echo("Weekly consolidation skipped (insufficient sessions).")



@cli.command(name="graph")
@click.argument("action", default="build")
@click.option("--hours", default=24, help="Lookback window for inference")
@click.option("-n", default=500, help="Max memories to scan")
def graph_cmd(action, hours, n):
    """Knowledge Graph utilities.

    jarvis graph build   - run entity extraction + relationship inference
    """
    if action != "build":
        click.echo(f"Unknown graph action: {action}")
        return
    store = Store()
    try:
        from jarvis.graph import infer_relationships
        from jarvis.extract_entities import extract_entities
    except ImportError as e:
        click.echo(f"Graph modules unavailable: {e}")
        store.close()
        return
    click.echo(f"Scanning last {hours}h of memories (limit {n})...")
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    rows = store.conn.execute(
        "SELECT id, source, content FROM memories WHERE tier = 'raw' AND timestamp >= ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?",
        (cutoff, n)
    ).fetchall()
    entities_linked = 0
    for row in rows:
        mid = row["id"]
        source = row["source"] or ""
        content = row["content"] or ""
        ents = extract_entities(content, source_type=source)
        for ent in ents:
            eid = store.get_or_create_entity(ent["name"], entity_type=ent.get("type", "person"))
            if eid:
                store.link_memory_entity(mid, eid)
                entities_linked += 1
    infer_relationships(store, limit_hours=hours, max_memories=n)
    stats = store.conn.execute(
        "SELECT entity_type, COUNT(*) as c FROM entities GROUP BY entity_type"
    ).fetchall()
    click.echo(f"Linked {entities_linked} entity mentions.")
    if stats:
        click.echo("Entities:" + ", ".join(f"{r['entity_type']}={r['c']}" for r in stats))
    else:
        click.echo("No entities yet.")
    store.close()


@consolidate.command()
def monthly():
    added = run_monthly()
    if added:
        click.echo(f"Monthly consolidation complete. {added} new arc(s) stored.")
    else:
        click.echo("Monthly consolidation skipped (insufficient reflections).")

@cli.command()
@click.option("--port", default=8766, help="Port to run the dashboard on")
@click.option("--daemon-url", default="http://127.0.0.1:8765", help="Daemon base URL")
def dashboard(port, daemon_url):
    """Start the Jarvis web dashboard (FastAPI + UVicorn)."""
    from jarvis.dashboard import run_dashboard
    run_dashboard(port=port, daemon_url=daemon_url)


@cli.command()
@click.argument("idea")
@click.option("--source", default="cli", help="Where the idea came from")
def think(idea, source):
    """Submit an idea to the Mayor. It will be parsed into a task and queued for review.

    \b
    Examples:
      jarvis think "the dashboard graph should use D3.js"
      jarvis think "add a /export command to dump memories as JSON"
      jarvis think "scan for hardcoded API keys in the codebase"
    """
    import urllib.request, urllib.error, json
    mayor_url = os.environ.get("MAYOR_URL", "http://127.0.0.1:8767")
    payload = json.dumps({"idea": idea, "source": source}).encode()
    req = urllib.request.Request(
        f"{mayor_url}/idea",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            click.echo(f"✅ Idea submitted!")
            click.echo(f"   Task ID: {result.get('task_id', '?')}")
            click.echo(f"   Agent:   {result.get('agent', '?')}")
            click.echo(f"   Title:   {result.get('title', '?')}")
            click.echo(f"   Status:  {result.get('status', '?')}")
            click.echo(f"   Review at: jarvis task list")
    except urllib.error.URLError:
        click.echo("❌ Mayor not running. Start it with: jarvis mayor")
    except Exception as e:
        click.echo(f"❌ Error: {e}")


@cli.group(name="task")
def task_group():
    """Manage the Mayor's task queue."""
    pass


@task_group.command()
@click.option("--status", default=None, help="Filter by status (pending_review/approved/in_progress/completed/blocked)")
@click.option("--agent", default=None, help="Filter by agent (code/design/qa/security/research)")
def list(status, agent):
    """List tasks in the queue."""
    from jarvis.task_queue import TaskQueue
    tq = TaskQueue()
    try:
        tasks = tq.list_tasks(status=status, agent=agent)
        if not tasks:
            click.echo("No tasks found.")
            return
        click.echo(f"{'ID':<14} {'Status':<16} {'Agent':<10} {'Pri':<4} {'Title'}")
        click.echo("-" * 80)
        for t in tasks:
            click.echo(f"{t['id']:<14} {t['status']:<16} {t.get('agent','?'):<10} {t.get('priority',3):<4} {t.get('title','?')[:50]}")
    finally:
        tq.close()


@task_group.command()
@click.argument("task_id")
def approve(task_id):
    """Approve a task for execution."""
    from jarvis.task_queue import TaskQueue
    tq = TaskQueue()
    try:
        if tq.approve_task(task_id):
            click.echo(f"✅ Task {task_id} approved.")
        else:
            click.echo(f"❌ Could not approve {task_id} (not found or not pending).")
    finally:
        tq.close()


@task_group.command(name="approve-all")
def approve_all():
    """Approve all pending tasks."""
    from jarvis.task_queue import TaskQueue
    tq = TaskQueue()
    try:
        count = tq.approve_all()
        click.echo(f"✅ Approved {count} task(s).")
    finally:
        tq.close()


@task_group.command()
@click.argument("task_id")
def reject(task_id):
    """Reject a pending task."""
    from jarvis.task_queue import TaskQueue
    tq = TaskQueue()
    try:
        if tq.reject_task(task_id):
            click.echo(f"❌ Task {task_id} rejected.")
        else:
            click.echo(f"❌ Could not reject {task_id}.")
    finally:
        tq.close()


@task_group.command()
def stats():
    """Show task queue statistics."""
    from jarvis.task_queue import TaskQueue
    tq = TaskQueue()
    try:
        s = tq.stats()
        if not s:
            click.echo("Task queue is empty.")
            return
        for status, count in s.items():
            click.echo(f"  {status}: {count}")
    finally:
        tq.close()


@cli.command()
@click.option("--port", default=8767, help="Port for the Mayor HTTP API")
@click.option("--root", default=None, help="Project root directory")
def mayor(port, root):
    """Start the Mayor orchestrator daemon (runs 24/7 on Lightspeed).

    The Mayor receives ideas, parses them into tasks, and dispatches
    approved tasks to coding agents. It switches between coding mode
    (8am-11pm) and memory mode (11pm-8am) automatically.
    """
    from jarvis.mayor import run_mayor
    run_mayor(port=port, project_root=root)


if __name__ == "__main__":
    cli()
