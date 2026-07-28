# Deployment: Lightspeed (Dell G7)

Lightspeed runs the brain runtime: Ollama, ChromaDB, agent loop, and the inbox watcher.
All other devices push new files to Lightspeed over SSH/Tailscale.

## 1. Prerequisites on Lightspeed

```bash
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
tailscale ip -4 lightspeed

python3 --version  # >= 3.10
pip install -e /path/to/second_brain
ollama serve &
ollama pull qwen2.5:7b-instruct-q4_K_M
```

## 2. Directory layout on Lightspeed

```
/data/second-brain/
├── chroma/            # ChromaDB vectors
├── meta.db            # SQLite metadata
├── inbox/             # Push target for remote devices
│   └── <device_id>/  # Per-device staging
├── logs/              # Service logs
├── processed.json     # Inbox dedup tracker
├── service.log
├── consolidation.log  # Tiered memory summaries
└── backups/           # Encrypted backups
```

## 3. SSH key setup

From every data device:
```bash
ssh-copy-id user@lightspeed
ssh user@lightspeed "echo ok"
```

## 4. Install service on Lightspeed (macOS)

```bash
mkdir -p ~/Library/LaunchAgents
cp second_brain/service/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.second-brain.plist
tail -f /data/second-brain/logs/service.log
```

## 5. Install cron jobs on Lightspeed

```bash
mkdir -p /data/second-brain/scripts
chmod +x /data/second-brain/scripts/*.sh
crontab -e
# Paste contents of scripts/crontab.txt
```

All jobs are idle-aware: 1am–6am, and only when system idle ≥30 minutes.

Verify:
```bash
crontab -l
tail -f /data/second-brain/logs/consolidation.log
```

## 6. Environment variables

On every device that pushes:
- `LIGHTSPEED_HOST` = Tailscale hostname or IP of Lightspeed
- `LIGHTSPEED_USER` = username on Lightspeed

On Lightspeed:
- `PYTHONPATH` includes the project path
- Ollama is running and reachable
- `/data/second-brain/` is writable

Ollama remote access:
```bash
launchctl setenv OLLAMA_HOST 0.0.0.0
launchctl stop com.ollama.ollama
launchctl start com.ollama.ollama
```

Backup:
- `BACKUP_NAS_HOST` = TrueNAS Tailscale hostname
- `BACKUP_NAS_PATH` = path on TrueNAS for backups
- `BACKUP_PASSPHRASE` = encryption passphrase

## 7. Sunday Deep Sync

Run from Lightspeed:
```bash
python -m brain.cli sync
```

Sync sources:
- `all` — everything below
- `files` — watched file changes
- `browser` — Chrome/Safari/Firefox history
- `calendar` — macOS Calendar events
- `email` — macOS Mail metadata
- `photos` — photo metadata via exiftool
- `bookmarks` — Chrome/Safari bookmarks
- `rss` — RSS/Atom/OPML files
- `system` — installed apps via system_profiler
- `deep` — deep recursive scan of Documents/Downloads/Desktop
- `git` — git commit history in ~/code, ~/Projects, ~/repos, ~/projects
- `kilo` — local Kilo/OpenClaw session transcripts
- `gemini` — Google Takeout zip in Downloads

 ## 8. Backup to TrueNAS

Weekly encrypted backup runs Saturday at 11pm (idle-aware). Restore:
```bash
age -d -o backup.tar.gz < backup-YYYY-MM-DD.tar.gz.enc
```

## 9. Data flow

```
Any device (Mac / laptop)
  │
  ├── watchdog observes ~/Documents, ~/obsidian, ~/notes
  │
  └── If Tailscale reachable:
        scp file → lightspeed:/data/second-brain/inbox/<this_device_id>/
        
Lightspeed (brain runtime)
  │
  ├── Inbox watcher detects new file
  ├── Exact content match → merge device tags (no duplicate entry)
  ├── No match → chunk + embed via Ollama + extract tags/entities
  ├── Stores in ChromaDB + SQLite with tiered weights
  │
  ├── Cron:
  │     daily  04:00 → session summaries (if >=20 raw memories)
  │     Sunday 06:00 → weekly reflections (if >=10 sessions)
  │     1st     06:00 → monthly arcs (if >=4 reflections)
  │     Saturday 23:00 → encrypted backup to TrueNAS
  │
  ├── Consolidated memories expire: session=7d, reflection=30d
  ├── Corrections mark original superseded, never delete
  └── Agent loop reads from local store only
```