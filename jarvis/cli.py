import click
import json
import os
from jarvis.store import Store
from jarvis.brain import Brain
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.classifier import classify
from jarvis.routes import classify_existing
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
    cur = store.conn.execute("SELECT * FROM memories WHERE superseded = 0 ORDER BY timestamp DESC LIMIT ?", (n * 3,))
    rows = [dict(r) for r in cur.fetchall()]
    if source:
        rows = [r for r in rows if r["source"] == source]
    if tier:
        rows = [r for r in rows if r["tier"] == tier]
    if tag:
        rows = [r for r in rows if tag in json.loads(r["tags"])]
    rows = sorted(rows, key=lambda r: r["timestamp"], reverse=True)[:n]
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
    cur = store.conn.execute("SELECT * FROM memories WHERE superseded = 0 AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?", (cutoff, n * 3))
    rows = [dict(r) for r in cur.fetchall()]
    rows = sorted(rows, key=lambda r: r["timestamp"], reverse=True)[:n]
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
@click.option("-n", default=30, help="Number of top tags/entities")
def graph(n):
    store = Store()
    cur = store.conn.execute("SELECT tags FROM memories WHERE superseded = 0")
    from collections import Counter
    tag_counts = Counter()
    for row in cur:
        for tag in json.loads(row["tags"]):
            tag_counts[tag] += 1
    store.close()
    if not tag_counts:
        click.echo("No tags found.")
        return
    click.echo("Top tags/entities:")
    for tag, count in tag_counts.most_common(n):
        click.echo(f"  {tag}: {count}")


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
@click.option("--source", default="all", help="Target for sync: all, files, browser, calendar, email, photos, bookmarks, rss, system, deep, git")
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
@click.option("--model", default=None, help="Model override")
@click.option("--verbose", is_flag=True, help="Show full source context")
def chat(model, verbose):
    store = Store()
    brain = Brain(store, model=model) if model else Brain(store)
    session = []
    last_memories = []
    click.echo("Chat with your Jarvis. Ctrl+C or /quit to exit. Commands: /sources /clear /alerts /upgrade /quit")
    try:
        while True:
            user_input = click.prompt("you")
            if user_input.strip().lower() in ("/quit", "/exit", "/q"):
                if brain.save_session(session):
                    click.echo("Session saved.")
                click.echo("Exiting chat.")
                break
            if user_input.strip().lower() == "/clear":
                session.clear()
                last_memories = []
                click.echo("Session cleared.")
                continue
            if user_input.strip().lower() == "/sources":
                if not last_memories:
                    click.echo("No sources from last response.")
                    continue
                click.echo("Sources:")
                for m in last_memories:
                    tags = ", ".join(json.loads(m["tags"])) if m["tags"] else ""
                    click.echo(f"  [{m['source']}] [{m['tier']}] {m['timestamp']} {tags}")
                    click.echo(f"    {m['content'][:250]}")
                    click.echo(f"    id={m['id']}")
                continue
            if user_input.strip().lower() == "/alerts":
                _show_alerts(brain)
                continue
            if user_input.strip().lower().startswith("/upgrade "):
                feature = user_input.strip()[9:]
                added = brain.upgrade(feature)
                if added:
                    click.echo(f"jarvis: Upgrade request recorded: {feature}")
                else:
                    click.echo("jarvis: Already recorded.")
                continue
            response, memories, source_count = brain.chat(session, user_input)
            last_memories = memories
            click.echo(f"jarvis: {response}")
            badge = f"[{source_count} sources]" if source_count > 0 else "[no sources]"
            click.echo(f"  {badge}")
            if verbose and memories:
                click.echo("  Details:")
                for m in memories[:3]:
                    tags = ", ".join(json.loads(m["tags"])) if m["tags"] else ""
                    click.echo(f"    - [{m['source']}] {m['timestamp']} {tags}")
                    click.echo(f"      {m['content'][:200]}")
    except click.Abort:
        click.echo("\nExiting chat.")
    finally:
        store.close()


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


if __name__ == "__main__":
    cli()
