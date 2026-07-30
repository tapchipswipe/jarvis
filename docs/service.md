# Background Service

Two launchd agents manage the jarvis:

- **Watcher** (`com.user.jarvis-watcher`): keeps file/shell/browser collectors alive, restarts on crash.
- **Sync** (`com.user.jarvis-sync`): runs every Sunday at 23:00 for deep sync.

## Install

```bash
cp ~/jarvis/service/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.jarvis-watcher.plist
launchctl load ~/Library/LaunchAgents/com.user.jarvis-sync.plist
```

## Manage

```bash
# Status
launchctl list | grep jarvis

# Logs
tail -f ~/jarvis/logs/watcher.log
tail -f ~/jarvis/logs/sync.log

# Restart watcher after config changes
launchctl kickstart -k gui/$(id -u)/com.user.jarvis-watcher

# Manual sync now
python -m jarvis.cli sync

# Stop
launchctl unload ~/Library/LaunchAgents/com.user.jarvis-sync.plist
launchctl unload ~/Library/LaunchAgents/com.user.jarvis-watcher.plist
```
