# Deployment: Lightspeed (Dell G7 — Windows)

Lightspeed runs the brain runtime: Ollama, ChromaDB, agent loop, and the inbox watcher.
All other devices push new files to Lightspeed over SSH/Tailscale.

## 1. Prerequisites on Lightspeed

1. Install **Python 3.10+** from https://www.python.org/downloads/windows/
   - Check "Add Python to PATH" during install
2. Install **Git for Windows** from https://git-scm.com/download/win
3. Install **Ollama** from https://ollama.ai/download/windows
4. Install **NSSM** (Non-Sucking Service Manager) from https://nssm.cc/download
   - Extract `nssm.exe` to `C:\Windows\System32\` or a folder in your PATH
5. Open PowerShell **as Administrator**

## 2. Directory layout on Lightspeed

```
C:\data\second-brain\
├── chroma\            # ChromaDB vectors
├── meta.db            # SQLite metadata
├── inbox\             # Push target for remote devices
│   └── <device_id>\  # Per-device staging
├── logs\              # Service logs
├── processed.json     # Inbox dedup tracker
├── service.log
└── consolidation.log  # Tiered memory summaries
```

## 3. Clone the repo

```powershell
git clone https://github.com/tapchipswipe/despotbrain.git C:\data\second-brain
cd C:\data\second-brain
```

## 4. Python setup

```powershell
python -m venv C:\data\second-brain\.venv
C:\data\second-brain\.venv\Scripts\Activate.ps1
pip install -e .
```

Note: If you get execution policy errors, run:
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
```

## 5. Ollama setup

```powershell
# Start Ollama service (it should auto-start after install)
ollama pull qwen2.5:7b-instruct-q4_K_M
```

Verify Ollama is running:
```powershell
curl http://localhost:11434/api/tags
```

## 6. Install background service (NSSM)

Create a service that keeps the inbox watcher + file watcher running:

```powershell
nssm install SecondBrain "C:\data\second-brain\.venv\Scripts\python.exe" "C:\data\second-brain\brain\service.py"
nssm set SecondBrain DisplayName "Second Brain Service"
nssm set SecondBrain Start SERVICE_AUTO_START
nssm set SecondBrain AppDirectory "C:\data\second-brain"
nssm start SecondBrain
```

## 7. Install scheduled tasks (Task Scheduler)

Run PowerShell as Administrator and execute:

```powershell
# Create scheduled task for consolidation and backup
$action = New-ScheduledTaskAction -Execute "C:\data\second-brain\.venv\Scripts\python.exe" -Argument "C:\data\second-brain\brain\consolidation.py daily"
$trigger = New-ScheduledTaskTrigger -Daily -At 04:00AM
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "SecondBrain-Daily" -Action $action -Trigger $trigger -Settings $settings -Description "Daily session consolidation"
```

This is simplified. For full scheduled tasks, see Section 9 below.

## 8. Environment variables

On Lightspeed, set these in System Properties → Advanced → Environment Variables:

- `PYTHONPATH` = `C:\data\second-brain`
- `BACKUP_NAS_HOST` = `truenas` (or TrueNAS Tailscale IP)
- `BACKUP_NAS_PATH` = `/mnt/indiana/folders/second-brain` (on TrueNAS)
- `BACKUP_PASSPHRASE` = your encryption passphrase

Alternative: Set them in the service/task environment.

## 9. Full Task Scheduler setup

Run these commands in **Administrator PowerShell**:

```powershell
# Helper function
function New-BrainTask {
    param($Name, $Script, $Argument, $At)
    $action = New-ScheduledTaskAction -Execute "C:\data\second-brain\.venv\Scripts\python.exe" -Argument "$Script $Argument"
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Description "Second Brain: $Name" -Force
}

# Daily consolidation at 04:00
New-BrainTask -Name "SecondBrain-Daily" -Script "C:\data\second-brain\brain\consolidation.py" -Argument "daily" -At "04:00AM"

# Weekly reflection at 06:00 on Sundays
$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "06:00AM"
$weeklyAction = New-ScheduledTaskAction -Execute "C:\data\second-brain\.venv\Scripts\python.exe" -Argument "C:\data\second-brain\brain\consolidation.py weekly"
$weeklySettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "SecondBrain-Weekly" -Action $weeklyAction -Trigger $weeklyTrigger -Settings $weeklySettings -Force

# Monthly arc at 06:00 on 1st of month
$monthlyTrigger = New-ScheduledTaskTrigger -Once -At "06:00AM" -RepetitionInterval (New-TimeSpan -Days 30)
$monthlyAction = New-ScheduledTaskAction -Execute "C:\data\second-brain\.venv\Scripts\python.exe" -Argument "C:\data\second-brain\brain\consolidation.py monthly"
$monthlySettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "SecondBrain-Monthly" -Action $monthlyAction -Trigger $monthlyTrigger -Settings $monthlySettings -Force

# Model update check at 03:00
New-BrainTask -Name "SecondBrain-ModelUpdate" -Script "C:\data\second-brain\brain\model_update.py" -Argument "" -At "03:00AM"

# Backup to TrueNAS at 23:00 on Saturday
$backupAction = New-ScheduledTaskAction -Execute "C:\data\second-brain\.venv\Scripts\python.exe" -Argument "C:\data\second-brain\brain\backup.py"
$backupTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "11:00PM"
$backupSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "SecondBrain-Backup" -Action $backupAction -Trigger $backupTrigger -Settings $backupSettings -Force
```

## 10. Ollama Tailscale access

To allow remote access to Ollama API from your devices, set Ollama to listen on all interfaces:

```powershell
# Set environment variable for Ollama service
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "Machine")
# Restart Ollama service
Restart-Service -Name "ollama" -ErrorAction SilentlyContinue
```

Or from any device, test:
```powershell
curl http://lightspeed:11434/api/tags
```

## 11. Data flow

```
Any device (Mac / laptop)
  │
  ├── watchdog observes ~/Documents, ~/obsidian, ~/notes
  │
  └── If Tailscale reachable:
        scp file → lightspeed:/data/second-brain/inbox/<this_device_id>/
        (Windows accepts forward slashes in paths for scp)
        
Lightspeed (Dell G7 — Windows)
  │
  ├── NSSM service runs background watchers
  ├── Inbox watcher detects new file
  ├── Exact content match → merge device tags (no duplicate entry)
  ├── No match → chunk + embed via Ollama + extract tags/entities
  ├── Stores in ChromaDB + SQLite with tiered weights
  │
  ├── Task Scheduler:
  │     daily  04:00 → session summaries (if >=20 raw memories)
  │     Sunday 06:00 → weekly reflections (if >=10 sessions)
  │     1st     06:00 → monthly arcs (if >=4 reflections)
  │     Saturday 23:00 → encrypted backup to TrueNAS
  │
  ├── Consolidated memories expire: session=7d, reflection=30d
  ├── Corrections mark original superseded, never delete
  └── Agent loop reads from local store only
```

## 12. Verify installation

From Lightspeed PowerShell:
```powershell
# Test Python
python -m brain.cli status

# Test Ollama
ollama list

# Test service
nssm status SecondBrain

# Test scheduled tasks
Get-ScheduledTask | Where-Object {$_.TaskName -like "SecondBrain*"}
```

From your Mac:
```bash
scp ~/notes/test.md lightspeed:/data/second-brain/inbox/<mac_device_id>/
ssh lightspeed "python -m brain.cli search test"
```
