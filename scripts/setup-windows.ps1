# Jarvis — One-Time Windows Setup Script
# Run this as Administrator on Lightspeed (Dell G7)

param(
    [string]$RepoUrl = "https://github.com/tapchipswipe/despotbrain.git",
    [string]$InstallPath = "C:\data\jarvis",
    [string]$NasHost = "truenas",
    [string]$NasPath = "/mnt/indiana/folders/jarvis"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Jarvis Windows Setup ===" -ForegroundColor Cyan

# 1. Check prerequisites
Write-Host "`n[1/6] Checking prerequisites..."
$python = Get-Command python -ErrorAction SilentlyContinue
$git = Get-Command git -ErrorAction SilentlyContinue
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
$nssm = Get-Command nssm -ErrorAction SilentlyContinue

if (-not $python) { Write-Host "ERROR: Python not found. Install from python.org" -ForegroundColor Red; exit 1 }
if (-not $git) { Write-Host "ERROR: Git not found. Install from git-scm.com" -ForegroundColor Red; exit 1 }
if (-not $ollama) { Write-Host "ERROR: Ollama not found. Install from ollama.ai" -ForegroundColor Red; exit 1 }
if (-not $nssm) { Write-Host "ERROR: NSSM not found. Download from nssm.cc" -ForegroundColor Red; exit 1 }

Write-Host "  Python: $($python.Source)"
Write-Host "  Git: $($git.Source)"
Write-Host "  Ollama: $($ollama.Source)"
Write-Host "  NSSM: $($nssm.Source)"

# 2. Create directories
Write-Host "`n[2/6] Creating directories..."
New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
New-Item -ItemType Directory -Path "$InstallPath\data\chroma" -Force | Out-Null
New-Item -ItemType Directory -Path "$InstallPath\logs" -Force | Out-Null
New-Item -ItemType Directory -Path "$InstallPath\inbox" -Force | Out-Null
New-Item -ItemType Directory -Path "$InstallPath\backups" -Force | Out-Null
Write-Host "  Created: $InstallPath"

# 3. Clone repo
Write-Host "`n[3/6] Cloning repo..."
if (Test-Path "$InstallPath\.git") {
    Write-Host "  Repo already cloned, pulling latest..."
    Set-Location $InstallPath
    git pull origin main
} else {
    git clone $RepoUrl $InstallPath
}
Set-Location $InstallPath
Write-Host "  Repo ready at $InstallPath"

# 4. Python venv
Write-Host "`n[4/6] Setting up Python venv..."
if (-not (Test-Path "$InstallPath\.venv")) {
    python -m venv "$InstallPath\.venv"
}
$venvPython = Join-Path $InstallPath ".venv\Scripts\python.exe"
& $venvPython -m pip install -e . --quiet
Write-Host "  Venv ready"

# 5. Pull Ollama model
Write-Host "`n[5/6] Pulling Ollama model..."
& ollama pull qwen2.5:7b-instruct-q4_K_M
Write-Host "  Model ready"

# 6. Install service
Write-Host "`n[6/6] Installing Windows service..."
$serviceName = "Jarvis"
if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
    Write-Host "  Service already exists, stopping..."
    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    nssm remove $serviceName confirm
}
nssm install $serviceName $venvPython (Join-Path $InstallPath "jarvis\service_windows.py")
nssm set $serviceName DisplayName "Jarvis Service"
nssm set $serviceName Start SERVICE_AUTO_START
nssm set $serviceName AppDirectory $InstallPath
nssm set $serviceName AppStdout (Join-Path $InstallPath "logs\service.log")
nssm set $serviceName AppStderr (Join-Path $InstallPath "logs\service.log")
nssm start $serviceName
Write-Host "  Service installed and started"

# 7. Environment variables
Write-Host "`n[7/7] Setting environment variables..."
[System.Environment]::SetEnvironmentVariable("PYTHONPATH", $InstallPath, "Machine")
[System.Environment]::SetEnvironmentVariable("BACKUP_NAS_HOST", $NasHost, "Machine")
[System.Environment]::SetEnvironmentVariable("BACKUP_NAS_PATH", $NasPath, "Machine")
Write-Host "  PYTHONPATH=$InstallPath"
Write-Host "  BACKUP_NAS_HOST=$NasHost"
Write-Host "  BACKUP_NAS_PATH=$NasPath"
Write-Host "  (Set BACKUP_PASSPHRASE manually in System Properties)"

Write-Host "`n=== Setup Complete ===" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Set BACKUP_PASSPHRASE in System Properties"
Write-Host "  2. Run: python -m jarvis.cli status"
Write-Host "  3. Check service: nssm status Jarvis"
Write-Host "  4. From Mac: scp file lightspeed:/data/jarvis/inbox/<device_id>/"
