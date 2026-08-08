import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from jarvis.brain import Brain
from jarvis.consolidation import run_daily, run_monthly, run_weekly
from jarvis.routes import classify_existing
from jarvis.sessions import SessionDB
from jarvis.store import Store


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
@click.option(
    "--data-dir",
    default=None,
    help="Isolate storage under a custom data directory (multi-user profiles). "
         "Env: JARVIS_DATA_DIR",
)
@click.option(
    "--config-dir",
    default=None,
    help="Isolate config under a custom config directory. Env: JARVIS_CONFIG_DIR",
)
@click.option(
    "--user",
    "user_name",
    default=None,
    help="Profile name; data/config live under ~/jarvis/users/<user>. Env: JARVIS_USER",
)
@click.pass_context
def cli(ctx, data_dir, config_dir, user_name):
    """Jarvis — local ambient memory agent.

    Multi-user: pass --user/-u (or set JARVIS_USER) to run an isolated profile,
    or --data-dir/--config-dir for fully custom storage locations.
    """
    if data_dir:
        os.environ["JARVIS_DATA_DIR"] = data_dir
    if config_dir:
        os.environ["JARVIS_CONFIG_DIR"] = config_dir
    if user_name:
        os.environ["JARVIS_USER"] = user_name
    ctx.obj = {"user": user_name or os.environ.get("JARVIS_USER")}


@cli.command()
@click.pass_context
def profiles(ctx):
    """Show the active profile and the resolved storage/config paths."""
    from jarvis.paths import config_dir, data_dir, user_name

    click.echo(f"Active user/profile : {user_name()}")
    click.echo(f"Data root (store)   : {data_dir()}")
    click.echo(f"Config root         : {config_dir()}")
    click.echo("")
    click.echo("Override via env or the global options:")
    click.echo("  jarvis --user <name> <cmd>       # ~/jarvis/users/<name>/...")
    click.echo("  jarvis --data-dir <dir> <cmd>    # fully custom data root")
    click.echo("  JARVIS_DATA_DIR=<dir> jarvis status")


@cli.command()
@click.argument("text")
@click.option("--source", default="manual", help="Source tag")
@click.option("--tag", multiple=True, help="Tags to attach")
@click.option("--classify", is_flag=True, help="Run classifier after storing")
def remember(text, source, tag, classify):
    from jarvis import remote
    if remote.is_remote():
        from jarvis.cache import Cache, flush_outbox
        cache = Cache()
        try:
            queued = int(cache.enqueue(text, source=source, tags=list(tag)))
            res = flush_outbox(cache)
        finally:
            cache.close()
        note = " (classify runs on the server)" if classify else ""
        status = "to server" if res.get("pushed", 0) else ("to outbox; offline" if res.get("offline") else "to outbox")
        click.echo(f"Captured {queued} memory to Jarvis {status}{note}.")
        return
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
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON instead of a table")
def memories(source, tag, tier, n, as_json):
    from jarvis import remote
    if remote.is_remote():
        try:
            data = remote.memories(limit=n, source=source, tier=tier)
        except Exception as e:  # noqa: BLE001 - surface box read failures
            click.echo(f"Could not reach the box: {e}")
            return
        rows = data.get("memories", [])
        if tag:
            rows = [r for r in rows if tag in (r.get("tags") or [])]
    else:
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
        for r in rows:
            r["tags"] = json.loads(r["tags"]) if r["tags"] else []
    if as_json:
        click.echo(json.dumps(rows, default=str))
        return
    if not rows:
        click.echo("No memories found.")
        return
    for r in rows:
        tags = ", ".join(r["tags"] or [])
        click.echo(f"[{r['tier']}] [{r['source']}] {r['timestamp']} {tags}")
        click.echo(f"  {r['content'][:200]}")
        click.echo(f"  id={r['id']}")
        click.echo("")


@cli.command()
@click.option("--days", default=7, help="Look back N days")
@click.option("-n", default=50, help="Number of results")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON instead of a table")
def timeline(days, n, as_json):
    from jarvis import remote
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
    if remote.is_remote():
        try:
            data = remote.memories(limit=n, since=cutoff)
        except Exception as e:  # noqa: BLE001 - surface box read failures
            click.echo(f"Could not reach the box: {e}")
            return
        rows = data.get("memories", [])
    else:
        store = Store()
        cur = store.conn.execute("SELECT * FROM memories WHERE superseded = 0 AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?", (cutoff, n))
        rows = [dict(r) for r in cur.fetchall()]
        store.close()
        for r in rows:
            r["tags"] = json.loads(r["tags"]) if r["tags"] else []
    if as_json:
        click.echo(json.dumps(rows, default=str))
        return
    if not rows:
        click.echo("No memories in timeline.")
        return
    for r in rows:
        tags = ", ".join(r["tags"] or [])
        click.echo(f"{r['timestamp']} [{r['source']}] {tags}")
        click.echo(f"  {r['content'][:200]}")
        click.echo("")


@cli.command()
@click.argument("query")
@click.option("--source", default=None, help="Filter by source")
@click.option("-n", default=10, help="Number of results")
@click.option("--verbose", is_flag=True, help="Show detailed source context")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON (memories + response)")
def search(query, source, n, verbose, as_json):
    from jarvis import remote
    if remote.is_remote():
        from jarvis.cache import Cache
        cache = Cache()
        try:
            result = remote.search(query, n=n, source=source)
            cache.store_tail(result.get("memories", []))
            if as_json:
                click.echo(json.dumps(result, default=str))
                return
            click.echo(f"--- {result.get('count', 0)} result(s) ---")
            for m in result.get("memories", []):
                click.echo(f"- [{m['source']}] [{m['tier']}] {m['timestamp']}")
                click.echo(f"  {m['content'][:120]}...")
                ents = (result.get("entities") or {}).get(m.get("id"))
                if ents:
                    click.echo(f"  entities: {', '.join(e['name'] for e in ents)}")
        except urllib.error.HTTPError as e:
            # Server reached but returned an HTTP error (e.g. 403 bad token after
            # Round 3 guards) — that's NOT an offline situation, so surface it.
            detail = ""
            if e.fp:
                try:
                    detail = json.loads(e.fp.read().decode("utf-8") or "{}").get("error", "")
                except (json.JSONDecodeError, ValueError, UnicodeDecodeError, OSError):
                    detail = ""
            click.echo(f"--- Server error ({e.code}) ---")
            click.echo(f"  {detail or e.reason}")
        except Exception:  # noqa: BLE001 - connectivity fallback; the server was unreachable
            hits = cache.tail_search(query, limit=n)
            if as_json:
                click.echo(json.dumps({"offline": True, "cached": hits}, default=str))
                return
            click.echo("--- Offline (cached subset) ---")
            for m in hits:
                click.echo(f"- [stale] [{m['source']}] {m['timestamp']}")
                click.echo(f"  {m['content'][:120]}...")
        finally:
            cache.close()
        return
    store = Store()
    brain = Brain(store)
    response, memories = brain.query(query, n_results=n, source_filter=source, verbose=verbose)
    links = store.lookup_entities([m["id"] for m in memories]) if memories else {}
    store.close()
    if as_json:
        click.echo(json.dumps({"response": response, "memories": memories}, default=str))
        return
    click.echo("--- Response ---")
    click.echo(response)
    click.echo("\n--- Sources ---")
    for m in memories:
        tags = ", ".join(json.loads(m["tags"])) if m["tags"] else ""
        click.echo(f"- [{m['source']}] [{m['tier']}] {m['timestamp']} {tags}")
        click.echo(f"  {m['content'][:120]}...")
        click.echo(f"  id={m['id']}")
        ents = links.get(m["id"])
        if ents:
            click.echo(f"  entities: {', '.join(e['name'] for e in ents)}")
@cli.command()
def status():
    """Show status. In thin-client mode this reports the live box, not the local
    (rollback) store — the box is the single source of truth in FULL-THIN."""
    from jarvis import remote
    if remote.is_remote():
        from jarvis.cache import Cache
        cache = Cache()
        try:
            pending = cache.pending_count()
        finally:
            cache.close()
        try:
            deep = remote.health_deep()
            click.echo(
                f"Box (thin client): ok={deep.get('ok')} memories={deep.get('memories')} "
                f"mode={deep.get('mode')} uptime={int(deep.get('uptime', 0))}s"
            )
            if pending:
                click.echo(f"Outbox: {pending} pending write(s) not yet flushed (run `jarvis flush`).")
            else:
                click.echo("Outbox: 0 pending.")
        except Exception:  # noqa: BLE001 - box unreachable; report the local view
            click.echo("Box unreachable — offline. Reporting the local (rollback/cache) view.")
        return
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
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from jarvis.collectors.sync_runner import run_sync
    from jarvis.sync.push import get_lightspeed_stats

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
@click.option("--since", default=None, help="Only export memories with timestamp >= this ISO value")
def export(fmt, output, source, tier, since):
    """Export all memories to JSON or Markdown.

    In thin-client mode it pulls from the live box (single source of truth); in local
    mode it reads the local store. Writes a timestamped file by default; --output - = stdout.
    """
    from jarvis import remote

    fmt = "markdown" if fmt == "md" else fmt

    def _normalize(rows):
        for r in rows:
            if isinstance(r.get("tags"), str):
                try:
                    r["tags"] = json.loads(r["tags"])
                except Exception:  # noqa: BLE001 - best-effort tag parse
                    r["tags"] = []
            if isinstance(r.get("metadata"), str):
                try:
                    r["metadata"] = json.loads(r["metadata"])
                except Exception:  # noqa: BLE001 - best-effort metadata parse
                    r["metadata"] = {}
        return rows

    rows = []
    if remote.is_remote():
        try:
            data = remote.export("json")
        except Exception as e:  # noqa: BLE001 - surface box export failures
            click.echo(f"Export failed (box): {e}")
            return
        rows = _normalize(data.get("memories") or [])
        if source:
            rows = [r for r in rows if r.get("source") == source]
        if tier:
            rows = [r for r in rows if r.get("tier") == tier]
    else:
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
        rows = _normalize(rows)

    if since:
        rows = [r for r in rows if (r.get("timestamp") or "") >= since]

    if fmt == "json":
        payload = {
            "exported_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
    from jarvis.paths import data_dir
    return data_dir("data", "exports")


def _export_filename(fmt: str) -> str:
    stamp = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d-%H%M%S")
    ext = "md" if fmt == "markdown" else "json"
    return f"jarvis-export-{stamp}.{ext}"


def _render_markdown(rows: list[dict]) -> str:
    lines = ["# Jarvis Memory Export", ""]
    lines.append(f"**Exported:** {datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}")
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
@click.option("--batch", default=200, type=int, help="Memories per request")
@click.option("--limit", default=0, type=int, help="Cap on memories to push (0 = all)")
@click.option("--dry-run", is_flag=True, help="Build manifest + verify server only; push nothing")
def backfill(batch, limit, dry_run):
    """One-time migration: push the local store to the Lightspeed server,
    preserving original ids / timestamps / tiers / routes.

    Reads the *local* Mac store (superseded = 0), computes a hash manifest,
    and posts field-preserving batches to the server's /api/backfill. Verifies
    the server's active memory count matches the source before and after.
    Set JARVIS_REMOTE + JARVIS_MODE=client (or --data-dir) as usual.
    """
    import hashlib

    from jarvis import remote

    if not remote.server_url():
        raise click.ClickException("Set JARVIS_REMOTE to the Lightspeed server URL.")

    store = Store()
    try:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT * FROM memories WHERE superseded = 0 ORDER BY timestamp ASC").fetchall()]
    finally:
        store.close()
    if limit and limit > 0:
        rows = rows[:limit]

    if not rows:
        click.echo("No active memories to backfill.")
        return

    def _manifest_hash(r: dict) -> str:
        blob = "\x1f".join([
            str(r.get("id", "")), str(r.get("source", "")),
            str(r.get("source_id", "")), str(r.get("timestamp", "")),
            str(r.get("content", "")), str(r.get("tier", "raw")),
            str(r.get("route", "unclassified")),
            json.dumps(json.loads(r.get("tags") or "[]"), sort_keys=True),
            json.dumps(json.loads(r.get("metadata") or "{}"), sort_keys=True),
        ])
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    manifest_hashes = [_manifest_hash(r) for r in rows]
    aggregate = hashlib.sha256("\n".join(manifest_hashes).encode()).hexdigest()
    click.echo(f"Source: {len(rows)} active memory/ies, aggregate manifest {aggregate[:16]}…")

    # Pre-flight: what does the server currently hold vs. what we send?
    try:
        server = remote.health()
        click.echo(f"Server: ok={server.get('ok')} mode={server.get('mode')}")
        deep = remote._request("GET", "/api/health/deep")
        server_before = deep.get("memories", 0)
    except Exception as exc:  # noqa: BLE001 - report and stop
        raise click.ClickException(f"Cannot reach server: {exc}")

    if dry_run:
        click.echo(f"[dry-run] Would send {len(rows)} memories. Server currently {server_before} (will not push).")
        return

    pushed = 0
    skipped = 0
    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        payload = [{
            "id": c.get("id"), "source": c.get("source", "device"),
            "source_id": c.get("source_id"), "timestamp": c.get("timestamp"),
            "content": c.get("content"), "tags": json.loads(c.get("tags") or "[]"),
            "metadata": json.loads(c.get("metadata") or "{}"),
            "tier": c.get("tier", "raw"), "route": c.get("route", "unclassified"),
            "expires_at": c.get("expires_at"),
            "consolidated_from": c.get("consolidated_from"),
            "superseded": bool(c.get("superseded", 0)),
        } for c in chunk]
        try:
            res = remote.backfill_batch(payload)
            pushed += int(res.get("added", 0))
            rem_skip = int(res.get("skipped", 0))
            skipped += rem_skip
            click.echo(f"  batch {start + 1}–{start + len(chunk)}: +{res.get('added', 0)} added, {rem_skip} skipped")
        except Exception as exc:  # noqa: BLE001 - abort on server error, keep it honest
            raise click.ClickException(f"Backfill aborted at batch {start + 1}: {exc}")

    # Verify: server active count should now match the source count (plus what it had).
    try:
        deep = remote._request("GET", "/api/health/deep")
        server_after = deep.get("memories", 0)
    except Exception as exc:  # noqa: BLE001 - verification is best-effort
        server_after = -1
        click.echo(f"Warning: could not re-check server count: {exc}")

    expected = server_before + len(rows)
    ok = server_after >= expected
    click.echo("")
    click.echo(f"Pushed {pushed} active memory/ies, {skipped} skipped.")
    click.echo(f"Server active count: {server_before} → {server_after} (expected {expected}) → "
               f"{'VERIFIED' if ok else 'MISMATCH — investigate'}")
    click.echo(f"Aggregate manifest {aggregate[:16]}… is the authoritative hash of what was sent.")


def _chat_remote(model, verbose, is_new, resume, max_steps):
    """Thin-client chat loop: every turn is routed to the box (the single brain).

    The box owns the canonical session store, so here we never open a local
    SessionDB/Store — that is what makes ``chat`` memory-less in client mode.
    Session threading is carried by the box: the first turn (no session_id) lets
    the box create a session and we thread the returned id on follow-ups.
    """
    from jarvis import remote

    session_id = resume if (resume and not is_new) else None
    if resume and not is_new:
        click.echo(f"Resuming session {resume}")
    else:
        click.echo("Starting a new session on the box.")

    click.echo("Commands: /quit  /clear  /sources")
    try:
        while True:
            user_input = click.prompt("you")
            if user_input.strip().lower() in ("/quit", "/exit", "/q"):
                click.echo("Session saved on the box. Goodbye.")
                break
            if user_input.strip().lower() == "/clear":
                session_id = None
                click.echo("Session cleared (new session on next turn).")
                continue
            if user_input.strip().lower() == "/sources":
                msgs = remote.session_messages(session_id).get("messages", []) if session_id else []
                # Tool messages are stored with role == "tool" and content = json.dumps(result).
                # (tool_calls is a list of dicts on the assistant row, so "tool" in it is never True.)
                tool_msgs = [m for m in msgs if m.get("role") == "tool"]
                click.echo(f"Last {len(tool_msgs)} tool interactions:")
                for m in tool_msgs[-10:]:
                    body = m.get("content") or json.dumps(m.get("tool_calls") or {})
                    click.echo(f"  tool: {body[:100]}")
                continue

            resp = remote.chat(user_input, session_id=session_id,
                               max_steps=max_steps, model=model)
            answer = resp.get("answer") or ""
            session_id = resp.get("session_id") or session_id
            tool_log = resp.get("tool_log") or []
            click.echo(f"jarvis: {answer}")
            if verbose and tool_log:
                click.echo(f"  [tools: {len(tool_log)} calls]")
                for entry in tool_log:
                    click.echo(f"    → {entry['tool']}({json.dumps(entry['args'])[:80]})")
    except click.Abort:
        click.echo("\nGoodbye.")


@cli.command()
@click.option("--model", default=lambda: os.environ.get("JARVIS_CHAT_MODEL"), help="Chat model override (defaults to JARVIS_CHAT_MODEL env, then agent fallback list)")
@click.option("--verbose", is_flag=True, help="Show tool calls and sources")
@click.option("--new", "is_new", is_flag=True, help="Start a fresh session")
@click.option("--resume", default=None, help="Resume session by ID")
@click.option("--max-steps", default=8, type=int, help="Max agent steps per turn")
def chat(model, verbose, is_new, resume, max_steps):
    """Chat with your Jarvis agent. Supports multi-turn dialogue with tool use."""
    from jarvis import remote as _remote_chat

    if _remote_chat.is_remote():
        _chat_remote(model, verbose, is_new, resume, max_steps)
        return

    from jarvis.agent import run_turn

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
                # Tool messages are stored with role == "tool" and content = json.dumps(result).
                tool_msgs = [m for m in msgs if m.get("role") == "tool"]
                click.echo(f"Last {len(tool_msgs)} tool interactions:")
                for m in tool_msgs[-10:]:
                    body = m.get("content") or json.dumps(m.get("tool_calls") or {})
                    click.echo(f"  tool: {body[:100]}")
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


@cli.command()
@click.option("--now", "do_now", is_flag=True,
              help="Generate the digest on demand (idea 1) instead of waiting for the schedule")
@click.option("--kind", default="morning_brief", show_default=True,
              type=click.Choice(["morning_brief", "end_of_day"]),
              help="Which digest to build")
@click.option("--json-out", is_flag=True, help="Emit the result as JSON")
def digest(do_now, kind, json_out):
    """Get Jarvis's morning/end-of-day digest.

    By default reports config; with --now it generates the digest immediately
    (runs in-process on the box via /api/digest, or locally in local mode) so
    you can preview quality without waiting for 08:00/18:00.
    """
    from jarvis import remote as _remote

    if not do_now:
        click.echo("Digests are scheduled at 08:00 (morning_brief) / 18:00 (end_of_day), "
                   "model JARVIS_DIGEST_MODEL. Use --now to generate one immediately.")
        return

    text = ""
    if _remote.is_remote():
        resp = _remote.digest(kind=kind)
        text = (resp.get("text") or "").strip()
    else:
        store = Store()
        try:
            text = Brain(store).build_digest(kind=kind)
        finally:
            store.close()

    if json_out:
        click.echo(json.dumps({"kind": kind, "text": text}))
        return
    click.echo(f"=== Jarvis {kind.replace('_', ' ')} ===")
    click.echo(text)


def _ask_grounded(question, n_results=8, source=None, model=None, history=None):
    """Return (answer, memories, entities) for a grounded question.

    Uses the box's /api/query in client mode, else the local Brain.query.
    `model` optionally overrides the auto-tiered chat model."""
    from jarvis import remote as _remote

    if _remote.is_remote():
        resp = _remote.query(question, n=n_results, source=source,
                             history=history, model=model)
        return ((resp.get("answer") or "").strip(),
                resp.get("memories") or [], resp.get("entities") or {})
    store = Store()
    try:
        brain = Brain(store, model=model) if model else Brain(store)
        answer, memories = brain.query(question, n_results=n_results,
                                       source_filter=source, history=history)
        entities = {}
        if memories:
            links = store.lookup_entities([m.get("id") for m in memories])
            entities = {mid: [{"name": e["name"], "entity_type": e["entity_type"]}
                              for e in ents] for mid, ents in links.items()}
        return answer, memories, entities
    finally:
        store.close()


def _build_followup_suggestions(entities, limit=3):
    """Derive 2-3 subtle, proactive next-step prompts from grounding entities.

    No LLM call: picks the most-referenced entity names and turns each into a
    short, low-key follow-up the user can ignore. Returns a list of phrase
    strings (without surrounding quotes). Empty when there are no entities.
    """
    counts = {}
    types = {}
    for ents in entities.values():
        for e in ents:
            name = (e.get("name") or "").strip()
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
            if name not in types:
                types[name] = e.get("entity_type") or ""
    ordered = sorted(counts, key=lambda n: (-counts[n], n.lower()))
    suggestions = []
    for name in ordered:
        if len(suggestions) >= limit:
            break
        t = (types.get(name) or "").lower()
        if "person" in t:
            phrase = f"what have I said about {name}?"
        elif "place" in t or "location" in t:
            phrase = f"what do I know about {name}?"
        else:
            phrase = f"tell me more about {name}"
        suggestions.append(phrase)
    return suggestions


def _render_followup_suggestions(entities):
    """Print a single low-key ``→ try: "…"`` line derived from grounding entities."""
    suggestions = _build_followup_suggestions(entities)
    if not suggestions:
        return
    quoted = "  ·  ".join(f'"{s}"' for s in suggestions)
    click.echo(f"\n→ try: {quoted}")


def _render_grounded(answer, memories, entities, show_entities=True, session_id=None,
                     show_sources=True, suggest=True):
    """Shared human-readable output for ask/console.

    show_sources=False hides the grounded-memory dump for natural chat; sources
    show when the user asks (/sources) or the question is a recall request.
    suggest=True appends a single, low-key proactive follow-up line derived from
    the grounding entities (no LLM call); it is silently skipped when there are
    no entities to build suggestions from."""
    if session_id:
        click.echo(f"(session {session_id})")
    click.echo(answer or "(no answer returned)")
    if show_sources and memories:
        click.echo(f"\n-- grounded in {len(memories)} memory(-ies) --")
        seen = set()
        for m in memories:
            key = (m["source"], m["timestamp"])
            if key in seen:
                continue
            seen.add(key)
            click.echo(f"  [{m['source']}] {m['timestamp']}  {m['content'][:120]}")
    if show_entities and entities:
        names = sorted({e["name"] for ents in entities.values() for e in ents})
        if names:
            click.echo(f"\n-- related entities: {', '.join(names)}")
    if suggest:
        _render_followup_suggestions(entities)


@cli.command()
@click.option("--n", "n_results", default=8, type=int, help="Memories to ground on")
@click.option("--source", default=None, help="Restrict grounding to a source (deep/manual/device/...)")
@click.option("--model", default=None, help="LLM model override")
@click.option("--json-out", is_flag=True, help="Emit the result as JSON")
@click.option("--session", "session_id", default=None, help="Resume/thread an ask session by ID")
@click.option("--save/--no-save", "save_qa", default=True,
              help="Store the Q&A back to memory (tag 'ask') [default: save]")
@click.option("--entities/--no-entities", "show_entities", default=True,
              help="Surface related knowledge-graph entities")
@click.argument("question")
def ask(question, n_results, source, model, json_out, session_id, save_qa, show_entities):
    """Ask Jarvis a one-shot question, ALWAYS grounded on the brain.

    Retrieves the most relevant memories, sends them alongside the question to
    the LLM, and prints the answer plus the grounding sources — so it never
    fabricates: if the brain has nothing relevant it will say so. In client mode
    it asks the box (the shared single brain) rather than the local store.

    Threading: pass the same --session <id> on follow-ups to carry prior turns
    as conversation context. --save (default) writes the Q&A back into memory
    (tag 'ask') so good exchanges become searchable context; use --no-save to
    opt out.
    """
    from jarvis import remote as _remote
    from jarvis.sessions import SessionDB

    sdb = None
    history = []
    sid = session_id
    answer = ""
    memories = []
    entities = {}

    try:
        if sid or save_qa:
            sdb = SessionDB()
            if sid:
                history = [m for m in sdb.get_messages(sid, limit=20)
                           if m.get("role") in ("user", "assistant") and m.get("content")]

        answer, memories, entities = _ask_grounded(
            question, n_results=n_results, source=source, model=model, history=history)

        if sdb:
            if not sid:
                sid = sdb.create_session(title="ask")
            if answer:
                sdb.append_message(sid, "user", question)
                sdb.append_message(sid, "assistant", answer)

        if save_qa and answer:
            if _remote.is_remote():
                _remote.remember_batch([{"content": f"Q: {question}\nA: {answer}",
                                         "source": "ask", "tags": ["ask"]}])
            else:
                store = Store()
                try:
                    Brain(store).remember(f"Q: {question}\nA: {answer}",
                                          source="ask", tags=["ask"], classify=False)
                finally:
                    store.close()

        if json_out:
            click.echo(json.dumps({
                "answer": answer,
                "session_id": sid,
                "entities": list(entities.values()) if entities else [],
                "sources": [{"source": m["source"], "timestamp": m["timestamp"],
                             "content": m["content"]} for m in memories],
            }))
            return

        _render_grounded(answer, memories, entities,
                         show_entities=show_entities, session_id=sid)
    finally:
        if sdb:
            sdb.close()


@cli.command()
@click.option("--session", "session_id", default=None,
              help="Resume an existing console session by ID (else start a new one)")
@click.option("--no-entities", "show_entities", is_flag=True, default=False,
              help="Hide related-knowledge-graph entities")
@click.option("--save/--no-save", "save_qa", default=True,
              help="Store console Q&A back to memory (tag 'ask') [default: save]")
def console(session_id, show_entities, save_qa):
    """Open an interactive Jarvis terminal — Iron-Man style.

    Type a question and Jarvis answers, grounded on the brain, with a persistent
    conversational thread (each follow-up sees prior turns). Slash commands:

      /help           list commands
      /session <id>   switch to another session
      /clear          start a fresh session
      /save           write the last Q&A back to memory (tag 'ask')
      /digest         generate a digest now
      /status         show box/brain status
      /quit           exit
    """
    from jarvis.sessions import SessionDB

    sdb = SessionDB()
    sid = session_id or sdb.create_session(title="console")

    click.echo("")
    click.echo("  ╔══════════════════════════════════════════════════╗")
    click.echo("  ║   J A R V I S   —   your personal Jarvis          ║")
    click.echo("  ╚══════════════════════════════════════════════════╝")
    click.echo(f"(session {sid})  type /help for commands, /quit to exit.\n")

    last_qa = {"q": "", "a": ""}
    model_override = None  # None=auto-tier; else a tier (fast/medium/big) or model id
    show_sources = False   # sources hidden by default for natural chat

    def ask_line(text):
        nonlocal last_qa
        history = [m for m in sdb.get_messages(sid, limit=20)
                   if m.get("role") in ("user", "assistant") and m.get("content")]
        sys.stdout.write("(thinking…")
        sys.stdout.flush()
        try:
            answer, memories, entities = _ask_grounded(text, history=history,
                                                       model=model_override)
        except Exception as exc:  # noqa: BLE001 - recover gracefully
            print(f"  error: {exc})")
            return
        print(")")
        sdb.append_message(sid, "user", text)
        sdb.append_message(sid, "assistant", answer)
        last_qa = {"q": text, "a": answer}
        # Sources show ONLY when the user toggles them on (/sources on) — never
        # auto-dumped, even for recall questions (which get a grounded summary).
        _render_grounded(answer, memories, entities, show_entities=show_entities,
                         show_sources=show_sources)
        if save_qa and answer:
            _save_ask(text, answer)

    try:
        while True:
            try:
                line = input("Jarvis> ")
            except EOFError:
                break
            text = line.strip()
            if not text:
                continue
            low = text.lower()
            if low in ("/quit", "/exit", "/q"):
                click.echo("Goodbye.")
                break
            if low == "/help":
                click.echo("/help /model [tier|id|auto] /sources [on|off] /session /clear /save /digest /status /quit")
                continue
            if low == "/model":
                # show current
                click.echo(f"(model tier: {model_override or 'auto'})")
                continue
            if low.startswith("/model"):
                parts = text.split()
                if len(parts) > 1 and parts[1]:
                    model_override = parts[1]
                    click.echo(f"(model set to {model_override})")
                else:
                    click.echo(f"(model tier: {model_override or 'auto'})")
                continue
            if low == "/sources":
                click.echo(f"(sources: {'on' if show_sources else 'off'})")
                continue
            if low.startswith("/sources"):
                parts = text.split()
                state = parts[1].lower() if len(parts) > 1 else ""
                if state in ("on", "true", "1", "yes"):
                    show_sources = True
                    click.echo("(sources: on)")
                elif state in ("off", "false", "0", "no"):
                    show_sources = False
                    click.echo("(sources: off)")
                else:
                    click.echo(f"(sources: {'on' if show_sources else 'off'})")
                continue
            if low == "/clear":
                sdb.close()
                sdb = SessionDB()
                sid = sdb.create_session(title="console")
                click.echo(f"(new session {sid})")
                continue
            if low.startswith("/session"):
                parts = text.split()
                if len(parts) > 1 and parts[1]:
                    sid = parts[1]
                    click.echo(f"(switched to session {sid})")
                else:
                    click.echo(f"(current session {sid})")
                continue
            if low == "/save":
                if last_qa["a"]:
                    _save_ask(last_qa["q"], last_qa["a"])
                    click.echo("(saved last Q&A to memory)")
                else:
                    click.echo("(nothing to save yet)")
                continue
            if low == "/digest":
                _run_digest_now()
                continue
            if low == "/status":
                _run_status_snippet()
                continue
            if low.startswith("/"):
                click.echo(f"unknown command: {text}  (try /help)")
                continue
            ask_line(text)
    finally:
        sdb.close()


def _save_ask(question, answer):
    """Persist a Q&A back to memory (tag 'ask') — remote or local."""
    from jarvis import remote as _remote

    content = f"Q: {question}\nA: {answer}"
    if _remote.is_remote():
        _remote.remember_batch([{"content": content, "source": "ask", "tags": ["ask"]}])
    else:
        store = Store()
        try:
            Brain(store).remember(content, source="ask", tags=["ask"], classify=False)
        finally:
            store.close()


def _run_digest_now():
    from jarvis import remote as _remote

    text = ""
    if _remote.is_remote():
        text = (_remote.digest(kind="morning_brief").get("text") or "").strip()
    else:
        store = Store()
        try:
            text = Brain(store).build_digest(kind="morning_brief")
        finally:
            store.close()
    click.echo(f"\n=== Jarvis morning brief ===\n{text}\n")


def _run_status_snippet():
    from jarvis import remote as _remote

    if _remote.is_remote():
        try:
            d = _remote.health_deep()
            click.echo(f"(box: memories={d.get('memories')} mode={d.get('mode')})")
        except Exception as e:  # noqa: BLE001
            click.echo(f"(box unreachable: {e})")
    else:
        click.echo("(local mode)")


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
        from jarvis.extract_entities import extract_entities
        from jarvis.graph import infer_relationships
    except ImportError as e:
        click.echo(f"Graph modules unavailable: {e}")
        store.close()
        return
    click.echo(f"Scanning last {hours}h of memories (limit {n})...")
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
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
@click.option("--limit", default=200, help="Max memories to embed in this pass")
def reindex(limit):
    """Embed memories that are missing from the vector store (incremental).

    Normal ingestion embeds at write time, so this is a safety net for rows
    without an embedding (e.g. imported data) or a re-run after the embedding
    model changes. It does not require a full re-sync.
    """
    from jarvis.maintenance import reindex_missing

    done = reindex_missing(limit=limit)
    if done:
        click.echo(f"Re-indexed {done} memory(-ies).")
    else:
        click.echo("No memories need re-indexing (all embedded).")


@cli.command()
@click.option("--days", default=7, help="Promote raw memories older than N days")
@click.option("--limit", default=500, help="Max memories to promote per pass")
@click.option("--dry-run", is_flag=True, help="Show what would be promoted without changing anything")
def promote(days, limit, dry_run):
    """Promote raw memories older than --days to the session tier."""
    from jarvis.maintenance import promote_old
    from jarvis.store import Store

    store = Store()
    try:
        if dry_run:
            cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
            rows = store.conn.execute(
                "SELECT id, timestamp FROM memories WHERE tier = 'raw' AND superseded = 0"
                " AND timestamp < ? ORDER BY timestamp ASC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
            click.echo(f"[dry-run] Would promote {len(rows)} memory(-ies) older than {days}d.")
            return
        promoted = promote_old(days=days, limit=limit)
        click.echo(f"Promoted {promoted} raw memory(-ies) to session tier.")
    finally:
        store.close()


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
    import json
    import urllib.error
    import urllib.request
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
            click.echo("✅ Idea submitted!")
            click.echo(f"   Task ID: {result.get('task_id', '?')}")
            click.echo(f"   Agent:   {result.get('agent', '?')}")
            click.echo(f"   Title:   {result.get('title', '?')}")
            click.echo(f"   Status:  {result.get('status', '?')}")
            click.echo("   Review at: jarvis task list")
    except urllib.error.URLError:
        click.echo("❌ Mayor not running. Start it with: jarvis mayor")
    except Exception as e:
        click.echo(f"❌ Error: {e}")


@cli.group(name="task")
def task_group():
    """Manage the Mayor's task queue."""


@task_group.command(name="list")
@click.option("--status", default=None, help="Filter by status (pending_review/approved/in_progress/completed/blocked)")
@click.option("--agent", default=None, help="Filter by agent (code/design/qa/security/research)")
def list_tasks(status, agent):
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
@click.option("--port", default=8766, help="Port to run the Jarvis server on")
@click.option("--daemon-url", default="http://127.0.0.1:8765", help="Daemon base URL")
@click.option("--tls-cert", default=None, help="PEM cert path (serve HTTPS; else JARVIS_TLS_CERT)")
@click.option("--tls-key", default=None, help="PEM key path (else JARVIS_TLS_KEY)")
@click.option("--gen-cert", "gen_cert", is_flag=True,
              help="Generate a self-signed cert+key (data dir) and print its fingerprint")
@click.option("--check", "do_check", is_flag=True, help="Validate server setup without starting it")
def server(port, daemon_url, tls_cert, tls_key, gen_cert, do_check):
    """Start the Jarvis server (FastAPI + Mayor) — the thin-client back end.

    Runs the same app as the dashboard but is the canonical store + API for
    the thin-client Mac (mode=client). Set JARVIS_TOKEN to require a token
    (loopback is always allowed). Serve HTTPS by passing --tls-cert/--tls-key
    or setting JARVIS_TLS_CERT/JARVIS_TLS_KEY.
    """
    from jarvis import server as _srv

    if gen_cert:
        from jarvis.paths import data_dir
        from jarvis.tls import cert_fingerprint, ensure_self_signed
        cert = Path(data_dir()) / "server-cert.pem"
        key = Path(data_dir()) / "server-key.pem"
        ensure_self_signed(cert, key)
        fp = cert_fingerprint(cert)
        click.echo(f"cert: {cert}")
        click.echo(f"key : {key}")
        click.echo(f"pin : JARVIS_TLS_FINGERPRINT={fp}")
        click.echo("export JARVIS_TLS_CERT=... JARVIS_TLS_KEY=... on the server")
        return

    if do_check:
        ok = _srv.app is not None
        from jarvis import dashboard as _dash
        store = _dash._get_store()
        try:
            n = store.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE superseded = 0"
            ).fetchone()[0]
            click.echo(f"OK app=loaded memories={n} port={port}")
        finally:
            store.close()
        if not ok:
            raise click.ClickException("server app failed to load")
        return
    _srv.run(port=port, daemon_url=daemon_url,
             ssl_cert=tls_cert, ssl_key=tls_key)


@cli.command()
@click.argument("dst", type=click.Path(file_okay=False), required=False)
@click.option("--strict", is_flag=True,
              help="Advisory: mark snapshot as strict (shell wrapper pauses the server)")
@click.option("--data-dir", default=None, help="Data root override (default: jarvis data dir)")
def backup(dst, strict, data_dir):
    """Crash-consistent snapshot of the store to a directory.

    SQLite files (meta.db, embed_cache.db, chroma.sqlite3) use the online-backup
    API so they are consistent even while the server is live; Chroma's HNSW
    index binaries are copied best-effort (moment-in-time). For a fully strict
    HNSW snapshot, pause the server during the maintenance window (see scripts/
    jarvis-backup.sh --strict). Output mirrors the store layout so restore is a
    plain directory copy.
    """
    from jarvis.backup import snapshot_store
    from jarvis.paths import data_dir as _data_root

    root = Path(data_dir) if data_dir else _data_root("data")
    if dst is None:
        ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d-%H%M%S")
        dst = str(_data_root("backups", f"snapshot-{ts}"))
    dst_path = Path(dst)
    res = snapshot_store(Path(root), dst_path, strict=strict)
    click.echo(f"snapshot -> {dst_path}")
    click.echo(f"  sqlite online-backed : {res['sqlite_backed']}")
    click.echo(f"  chroma index files   : {res['hnsv_copied']} (best-effort)")
    click.echo(f"  total bytes          : {res['bytes']}")
    click.echo(f"  strict               : {res['strict']}")
    click.echo(f"  took (sec)           : {res['duration_sec']}")


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




@cli.command()
def flush():
    """Push any queued captures in the outbox to the server (thin client)."""
    from jarvis import remote
    from jarvis.cache import Cache, flush_outbox
    if not remote.is_remote():
        click.echo("Not in client mode (set JARVIS_MODE=client + JARVIS_REMOTE).")
        return
    cache = Cache()
    try:
        pending = cache.pending_count()
        res = flush_outbox(cache)
        if res.get("offline"):
            click.echo(f"Server unreachable - {pending} item(s) stay queued.")
        else:
            click.echo(f"Pushed {res.get('pushed', 0)} memory(-ies); {res.get('failed', 0)} failed, kept queued.")
    finally:
        cache.close()


@cli.command()
@click.option("--max-files", default=2000, help="Cap on files walked per run")
@click.option("--root", "roots", multiple=True, type=click.Path(),
              help="Additional directory to collect from (repeatable); defaults to Documents/notes/obsidian")
@click.option("--flush", "do_flush", is_flag=True, help="Flush the outbox to the server after scanning")
def collect(max_files, do_flush, roots):
    """Thin-client ambient collection: queue new file text to the outbox -> server.

    Refuses to run outside JARVIS_MODE=client (the box is the single writer in
    FULL-THIN). Idempotent: unchanged files are skipped and duplicate content is
    dropped, so re-running is safe.
    """
    from jarvis import remote
    from jarvis.collectors import thin

    if not remote.is_remote():
        click.echo(
            "collect requires thin-client mode (JARVIS_MODE=client + JARVIS_REMOTE); "
            "the Mac never writes a local brain in FULL-THIN."
        )
        raise SystemExit(2)
    stats = thin.scan_once(roots=list(roots) or None, max_files=max_files)
    line = (
        f"Scanned {stats['files']} file(s): enqueued {stats['enqueued']}, "
        f"dup {stats['dups']}, blank {stats['blank']}, "
        f"seen-skip {stats['skipped_seen']}, errors {stats['errors']}."
    )
    click.echo(line)
    if do_flush:
        res = thin.flush_once()
        click.echo(
            f"Flushed outbox: pushed={res.get('pushed', 0)} "
            f"failed={res.get('failed', 0)} offline={bool(res.get('offline'))}."
        )


@cli.command(name="ingest-status")
def ingest_status():
    """Show the box's inbox-backlog ingester progress (thin client)."""
    from jarvis import remote
    if not remote.is_remote():
        click.echo("ingest-status requires thin-client mode (JARVIS_MODE=client + JARVIS_REMOTE).")
        return
    try:
        st = remote.ingest_status()
    except Exception as e:  # noqa: BLE001 - connectivity/endpoint-not-present
        click.echo(f"Could not reach the box (/api/ingest/status): {e}")
        return
    click.echo(
        f"Active={st.get('active')} Enabled={st.get('enabled')} "
        f"processed={st.get('processed')} added={st.get('added')} "
        f"remaining={st.get('remaining')} done={st.get('done')}"
    )
    click.echo(f"  inbox: {st.get('inbox')}")


@cli.command()
def doctor():
    """Run a quick local + box diagnostics report (thin-client friendly)."""
    import platform

    from jarvis import remote
    from jarvis.cache import Cache

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    mode = os.environ.get("JARVIS_MODE", "local")
    url = remote.server_url()
    check("mode", mode in ("client", "local"),
          f"JARVIS_MODE={mode} remote_url={'set' if url else 'none'}")

    try:
        cache = Cache()
        try:
            pending = cache.pending_count()
        finally:
            cache.close()
        check("outbox", True, f"{pending} pending write(s)")
    except Exception as e:  # noqa: BLE001 - diagnostic should never hard-fail CLI
        check("outbox", False, str(e))

    if remote.is_remote():
        try:
            d = remote.health_deep()
            check("box", d.get("ok") is True,
                  f"memories={d.get('memories')} mode={d.get('mode')} uptime={int(d.get('uptime', 0))}s")
        except Exception as e:  # noqa: BLE001
            check("box", False, f"unreachable: {e}")
        try:
            st = remote.ingest_status()
            check("ingest", True,
                  f"active={st.get('active')} remaining={st.get('remaining')}")
        except Exception as e:  # noqa: BLE001 - 404 expected pre-box-restart
            check("ingest", True, f"endpoint not on running server yet ({e.__class__.__name__})")
    else:
        check("box", True, "local mode (no box to probe)")

    ofl = platform.system()
    check("os", True, f"{ofl} / python {platform.python_version()}")

    # git sync (bot == main == origin/bot) — local, best-effort
    git = {"HEAD": None, "main": None, "origin/bot": None}
    import subprocess
    root = Path(__file__).resolve().parent.parent
    for ref in git:
        try:
            r = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", ref],
                               capture_output=True, text=True, timeout=10, check=False)
            if r.returncode == 0:
                git[ref] = r.stdout.strip()
        except Exception:  # noqa: BLE001 - git check is best-effort
            git[ref] = None
    synced = git["HEAD"] == git["main"] == git["origin/bot"] is not None
    check("git", synced,
          f"HEAD={git['HEAD']} main={git['main']} origin/bot={git['origin/bot']}")

    click.echo(f"Jarvis doctor — {len(checks)} checks\n" + "-" * 40)
    bad = 0
    for name, ok, detail in checks:
        bad += 0 if ok else 1
        click.echo(f"[{'PASS' if ok else 'WARN'}] {name:<10} {detail}")


if __name__ == "__main__":
    cli()



