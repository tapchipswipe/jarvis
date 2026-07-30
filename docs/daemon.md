# Jarvis Sync Daemon

## Architecture

The daemon is a background service that watches the local `inbox/` directory for new files arriving from any device. On arrival, each candidate file is deduplicated by content hash, chunked, embedded, and classified before being stored in ChromaDB + SQLite. A lightweight HTTP API exposes status, device info, queue depth, and conflict state.

## Components

- `jarvis/sync/daemon.py` — Core daemon, watchdog watcher, ingest pipeline, retry queue, and HTTP API.
- `jarvis/sync/service.py` — macOS/Linux launcher that starts the daemon as a subprocess.
- `jarvis/sync/service_windows.py` — Windows launcher that starts the daemon as a subprocess.
- `jarvis/monitor.py` — Rich-based TUI dashboard.
- `jarvis/monitor_client.py` — HTTP client for the daemon API.
- `jarvis/collectors/inbox.py` — Inbox handler with sidecar fast-path support.

## Configuration

Config is loaded from `~/.config/jarvis/config.toml` when present, with environment variable overrides:

- `SB_DAEMON_PORT` — HTTP API port
- `SB_DAEMON_BIND` — bind address
- `OLLAMA_HOST`, `OLLAMA_PORT`, `OLLAMA_MODEL`

Defaults:
- Port: `8765`
- Bind: `127.0.0.1`
- Max retries: `5`
- Retry backoffs: `60s`, `5m`, `30m`, `2h`
- Classifier model: `qwen2.5:7b-instruct-q4_K_M`

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/status` | Daemon state and queue depths |
| GET | `/devices` | Known devices and last-seen timestamps |
| GET | `/queue` | Pending and retry queue contents |
| GET | `/conflicts` | Hash conflicts and resolution status |
| POST | `/sync` | Trigger immediate inbox scan |
| POST | `/classify` | Re-run classifier on retry queue |

## Ingest contract

1. File detected in inbox
2. Skip `.json` sidecars
3. Dedup by content hash
4. Chunk + embed
5. If `.json` sidecar exists: use `route`, `tag_seeds`, `confidence`, and related fields directly
6. Otherwise: classify via `jarvis.classifier.classify()`
7. Validate envelope via `jarvis.routes.validate_envelope()`
8. Store with populated `route`
9. On classification failure: enqueue for retry with backoff
10. Log decision to `decision_log`

## Monitoring

```bash
jarvis monitor          # live dashboard
jarvis monitor --devices
jarvis monitor --queue
jarvis monitor --conflicts
jarvis monitor --logs
```

## Deployment

### macOS / Linux

```bash
python -m jarvis.sync.service
```

Install via launchd with `~/Library/LaunchAgents/com.jarvis.daemon.plist` pointing at `jarvis.sync.service`.

### Windows

```powershell
python -m jarvis.sync.service_windows
```

Install via NSSM or Task Scheduler pointing at `jarvis.sync.service_windows`.
