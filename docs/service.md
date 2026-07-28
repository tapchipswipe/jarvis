# Background Service

Two launchd agents manage the brain:

- **Watcher** (`com.user.second-brain-watcher`): keeps file/shell/browser collectors alive, restarts on crash.
- **Sync** (`com.user.second-brain-sync`): runs every Sunday at 23:00 for deep sync.

## Install

```bash
cp ~/second_brain/service/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.second-brain-watcher.plist
launchctl load ~/Library/LaunchAgents/com.user.second-brain-sync.plist
```

## Manage

```bash
# Status
launchctl list | grep second-brain

# Logs
tail -f ~/second_brain/logs/watcher.log
tail -f ~/second_brain/logs/sync.log

# Restart watcher after config changes
launchctl kickstart -k gui/$(id -u)/com.user.second-brain-watcher

# Manual sync now
python -m brain.cli sync

# Stop
launchctl unload ~/Library/LaunchAgents/com.user.second-brain-sync.plist
launchctl unload ~/Library/LaunchAgents/com.user.second-brain-watcher.plist
```
