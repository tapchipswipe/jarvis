#!/usr/bin/env python3
"""scripts/monitor-ingest.py — live progress bar for the box inbox-backlog ingester.

Polls the box ``/api/ingest/status`` and renders a progress bar (rich if installed,
plain-ASCII otherwise). When stdout is not a TTY it prints one compact line per poll
instead of redrawing, so it works under nohup/log files too.

Usage:
    python scripts/monitor-ingest.py [--url https://100.102.0.99:8766] [--interval 10]
                                     [--once] [--max-wait 3600]

    * --url defaults to $JARVIS_REMOTE or https://100.102.0.99:8766
    * exits 0 when the drain is done (remaining == 0), 2 if the endpoint is absent
      (pre-deploy server), 1 on repeated unreachable.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request


def _ssl_ctx():
    """Accept the box's self-signed cert (transport is still TLS-encrypted)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_status(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url + "/api/ingest/status", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def fmt_dur(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def render_bar(pct: float, width: int = 30) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = round(width * pct / 100.0)
    return "[" + "#" * filled + "-" * (width - filled) + f"] {pct:5.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("JARVIS_REMOTE", "https://100.102.0.99:8766").rstrip("/"))
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between polls")
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    ap.add_argument("--max-wait", type=float, default=0.0, help="stop after N seconds (0 = until done)")
    ap.add_argument("--total", type=int, default=0,
                    help="true backlog size (override the first-poll estimate)")
    args = ap.parse_args()

    is_tty = sys.stdout.isatty()

    start = time.monotonic()
    total = None
    prev_remaining = None
    prev_t = None

    def emit(st, line):
        if is_tty:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()
        else:
            print(line, flush=True)
        if st.get("done"):
            print(flush=True)

    try:
        while True:
            try:
                st = fetch_status(args.url)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    print(f"[monitor] /api/ingest/status not present on this server ({args.url}) "
                          "- it predates the deploy. Deploy then re-run.", file=sys.stderr)
                    return 2
                print(f"[monitor] HTTP {e.code} from {args.url}", file=sys.stderr)
                return 1
            except Exception as e:  # noqa: BLE001
                print(f"[monitor] cannot reach {args.url}: {e}", file=sys.stderr)
                return 1

            remaining = int(st.get("remaining", 0))
            processed = int(st.get("processed", 0))
            added = int(st.get("added", 0))
            errors = int(st.get("errors", 0))
            done = bool(st.get("done", False))
            if total is None:
                # prefer the server's authoritative backlog size if present, else a
                # first-poll estimate (remaining + the batch in flight).
                total = (args.total or (st.get("total") or 0)
                         or (remaining + processed))

            # rate + eta from consecutive polls
            eta = ""
            now = time.monotonic()
            if prev_remaining is not None and prev_t is not None:
                dt = now - prev_t
                dr = prev_remaining - remaining
                if dt > 0 and dr > 0:
                    rate = dr / dt  # files/sec
                    eta = f" eta={fmt_dur(remaining / rate)}"
            prev_remaining, prev_t = remaining, now

            pct = (total - remaining) / total * 100.0 if total else 0.0
            bar = render_bar(pct)
            status = "DONE ✓" if done else "draining"
            line = (f"{status} {bar}  remaining={remaining} added(now)={added} "
                    f"errors={errors}{eta}  {args.url}")
            emit(st, line)

            if done or remaining == 0:
                print(f"[monitor] inbox backlog drained. total≈{total} remaining=0 "
                      f"(elapsed {fmt_dur(time.monotonic() - start)})", file=sys.stderr)
                return 0
            if args.once:
                return 0
            if args.max_wait and (time.monotonic() - start) >= args.max_wait:
                print(f"[monitor] max-wait reached ({fmt_dur(args.max_wait)}); "
                      f"remaining={remaining}", file=sys.stderr)
                return 0
            time.sleep(max(0.5, args.interval))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
