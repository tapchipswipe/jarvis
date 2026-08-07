import json
import os
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from jarvis.brain import Brain
from jarvis.classifier import classify
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
def memories(source, tag, tier, n):
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
def timeline(days, n):
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
def export(fmt, output, source, tier):
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
@click.option("--check", "do_check", is_flag=True, help="Validate server setup without starting it")
def server(port, daemon_url, do_check):
    """Start the Jarvis server (FastAPI + Mayor) — the thin-client back end.

    Runs the same app as the dashboard but is the canonical store + API for
    the thin-client Mac (mode=client). Set JARVIS_TOKEN to require a token
    (loopback is always allowed).
    """
    from jarvis import server
    if do_check:
        ok = server.app is not None
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
    server.run(port=port, daemon_url=daemon_url)


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
    click.echo(f"Jarvis doctor — {len(checks)} checks\n" + "-" * 40)
    bad = 0
    for name, ok, detail in checks:
        bad += 0 if ok else 1
        click.echo(f"[{'PASS' if ok else 'WARN'}] {name:<10} {detail}")


if __name__ == "__main__":
    cli()



