import click
import json
from brain.store import Store
from brain.brain import Brain
from brain.embed import get_embedding
from brain.ingest import chunk_document
from datetime import datetime, timedelta


@click.group()
def cli():
    """Second Brain — local ambient memory agent."""
    pass


@cli.command()
@click.argument("text")
@click.option("--source", default="manual", help="Source tag")
@click.option("--tag", multiple=True, help="Tags to attach")
def remember(text, source, tag):
    store = Store()
    brain = Brain(store)
    added = brain.remember(text, source=source, tags=list(tag))
    store.close()
    click.echo(f"Remembered {added} chunk(s).")


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
    for s in stats:
        click.echo(f"{s['source']:15s} | tier={s['tier']:10s} | count={s['count']:5d} | oldest={s['oldest']} | newest={s['newest']}")


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
@click.option("--source", default="all", help="Target for sync: all, files, browser, calendar, email, photos, bookmarks, rss, system, deep, git")
def sync(source):
    from brain.collectors.sync_runner import run_sync
    click.echo(f"Running scheduled sync ({source})...")
    results = run_sync(source)
    click.echo("Sync complete:")
    for source_name, count in results.items():
        if isinstance(count, int):
            click.echo(f"  {source_name}: {count} items added")
        else:
            click.echo(f"  {source_name}: {count}")


@cli.command()
@click.option("--model", default=None, help="Model override")
@click.option("--verbose", is_flag=True, help="Show full source context")
def chat(model, verbose):
    store = Store()
    brain = Brain(store, model=model) if model else Brain(store)
    session = []
    last_memories = []
    click.echo("Chat with your second brain. Ctrl+C or /quit to exit. Commands: /sources /clear /alerts /quit")
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
            response, memories, source_count = brain.chat(session, user_input)
            last_memories = memories
            click.echo(f"brain: {response}")
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
