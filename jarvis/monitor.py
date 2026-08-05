import argparse
import time
import platform
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.text import Text

from jarvis.monitor_client import MonitorClient, MonitorClientError

console = Console()


def _time_ago(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - t
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs}h ago"
        return f"{hrs // 24}d ago"
    except Exception:
        return ts or "never"


def _uptime(started_at: str | None) -> str:
    if not started_at:
        return "unknown"
    try:
        t = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - t
        secs = int(delta.total_seconds())
        hrs, rem = divmod(secs, 3600)
        mins, _ = divmod(rem, 60)
        return f"{hrs}h {mins}m"
    except Exception:
        return "unknown"


def _dashboard_layout(status, devices, queue, conflicts):
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1),
    )
    layout["left"].split(
        Layout(name="devices", ratio=2),
        Layout(name="queue", ratio=1),
    )
    layout["right"].split(
        Layout(name="conflicts", ratio=1),
        Layout(name="activity", ratio=2),
    )

    header_text = (
        f"Jarvis Monitor   "
        f"Daemon: PID {status.get('pid', '?')} up {_uptime(status.get('started_at'))}   "
        f"[Ctrl+C to exit]"
    )
    layout["header"].update(Panel(header_text, style="bold cyan"))

    dev_table = Table(show_header=True, header_style="bold magenta")
    dev_table.add_column("Device", style="dim")
    dev_table.add_column("Last Seen")
    dev_table.add_column("Last Push")
    dev_table.add_column("Status")
    for name, info in sorted(devices.items()):
        seen = _time_ago(info.get("last_seen"))
        push = _time_ago(info.get("last_push"))
        status_text = "connected" if info.get("last_seen") else "offline"
        style = "green" if info.get("last_seen") else "grey50"
        dev_table.add_row(name, seen, push, Text(status_text, style=style))
    layout["devices"].update(Panel(dev_table, title="Devices", border_style="blue"))

    pending = queue.get("pending", [])
    retry = queue.get("retry", [])
    q_table = Table(show_header=True, header_style="bold magenta")
    q_table.add_column("Queue", style="dim")
    q_table.add_column("Count")
    q_table.add_row("Pending", str(len(pending)))
    q_table.add_row("Retry", str(len(retry)))
    layout["queue"].update(Panel(q_table, title="Sync Queue", border_style="blue"))

    c_table = Table(show_header=True, header_style="bold magenta")
    c_table.add_column("Hash", style="dim")
    c_table.add_column("Resolved")
    for c in conflicts[:20]:
        c_table.add_row(c.get("hash", "")[:16], "yes" if c.get("resolved") else "no")
    if not conflicts:
        c_table.add_row("—", "—")
    layout["conflicts"].update(Panel(c_table, title="Conflicts", border_style="red"))

    act_text = Text()
    for a in status.get("activity_log", [])[-15:]:
        act_text.append(f"{a.get('ts','?')} ", style="dim")
        act_text.append(f"{a.get('msg','')}\n")
    layout["activity"].update(Panel(act_text, title="Recent Activity", border_style="green"))

    layout["footer"].update(Panel(f"Last sync: {_time_ago(status.get('last_ingest_ts'))}", style="dim"))

    return layout


def _devices_view(client: MonitorClient):
    try:
        devices = client.get_devices()
    except MonitorClientError as e:
        console.print(f"[red]Error: {e}[/red]")
        return
    table = Table(title="Devices")
    table.add_column("Name")
    table.add_column("Last Seen")
    table.add_column("Last Push")
    table.add_column("Status")
    for name, info in sorted(devices.items()):
        table.add_row(name, _time_ago(info.get("last_seen")), _time_ago(info.get("last_push")), "connected" if info.get("last_seen") else "offline")
    console.print(table)


def _queue_view(client: MonitorClient):
    try:
        queue = client.get_queue()
    except MonitorClientError as e:
        console.print(f"[red]Error: {e}[/red]")
        return
    pending = queue.get("pending", [])
    retry = queue.get("retry", [])
    if pending:
        table = Table(title="Pending Queue")
        table.add_column("Path")
        table.add_column("Added At")
        for item in pending:
            table.add_row(item.get("path", ""), item.get("added_at", ""))
        console.print(table)
    else:
        console.print("[green]Pending queue empty[/green]")
    if retry:
        table = Table(title="Retry Queue")
        table.add_column("Path")
        table.add_column("Reason")
        table.add_column("Attempts")
        table.add_column("Next Retry")
        for item in retry:
            table.add_row(item.get("path", ""), item.get("reason", ""), str(item.get("attempts", 0)), item.get("next_retry", ""))
        console.print(table)
    else:
        console.print("[green]Retry queue empty[/green]")


def _conflicts_view(client: MonitorClient):
    try:
        conflicts = client.get_conflicts()
    except MonitorClientError as e:
        console.print(f"[red]Error: {e}[/red]")
        return
    if not conflicts:
        console.print("[green]No conflicts[/green]")
        return
    table = Table(title="Conflicts")
    table.add_column("ID")
    table.add_column("Hash")
    table.add_column("Local ID")
    table.add_column("Remote ID")
    table.add_column("Resolved")
    for c in conflicts:
        table.add_row(c.get("id", ""), c.get("hash", "")[:16], c.get("local_id", ""), c.get("remote_id", ""), "yes" if c.get("resolved") else "no")
    console.print(table)


def _logs_view():
    log_path = Path("/data/jarvis/logs/daemon.log")
    if platform.system() == "Windows":
        log_path = Path("C:/data/jarvis/logs/daemon.log")
    elif not log_path.exists():
        # macOS/local deployment keeps logs under the data root
        from jarvis.paths import logs_dir
        log_path = logs_dir("daemon.log")
    if not log_path.exists():
        console.print(f"[yellow]Log not found at {log_path}[/yellow]")
        return
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-100:]
        for line in lines:
            console.print(line.rstrip())
    except Exception as e:
        console.print(f"[red]Error reading log: {e}[/red]")


def main():
    parser = argparse.ArgumentParser(description="Jarvis Monitor")
    parser.add_argument("--devices", action="store_true", help="Show devices")
    parser.add_argument("--queue", action="store_true", help="Show queues")
    parser.add_argument("--conflicts", action="store_true", help="Show conflicts")
    parser.add_argument("--logs", action="store_true", help="Tail daemon log")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Daemon base URL")
    args = parser.parse_args()

    client = MonitorClient(base_url=args.url)

    if args.devices:
        _devices_view(client)
        return
    if args.queue:
        _queue_view(client)
        return
    if args.conflicts:
        _conflicts_view(client)
        return
    if args.logs:
        _logs_view()
        return

    try:
        status = client.get_status()
    except MonitorClientError as e:
        console.print(f"[red]Cannot connect to daemon: {e}[/red]")
        return

    def refresh():
        try:
            s = client.get_status()
            d = client.get_devices()
            q = client.get_queue()
            c = client.get_conflicts()
            return _dashboard_layout(s, d, q, c)
        except MonitorClientError:
            return Panel("Daemon unreachable", style="red")

    with Live(console=console, screen=True, refresh_per_second=0.5) as live:
        try:
            while True:
                live.update(refresh())
                time.sleep(2)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
