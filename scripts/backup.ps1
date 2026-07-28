# Second Brain — Windows Backup Script
param(
    [ValidateSet("backup","model-update")]
    [string]$Action = "backup"
)

$ErrorActionPreference = "Stop"
$nasHost = $env:BACKUP_NAS_HOST
$nasPath = $env:BACKUP_NAS_PATH
$passphrase = $env:BACKUP_PASSPHRASE
$dataRoot = "C:\data\second-brain"

function Backup-Data {
    Write-Host "[$(Get-Date)] Starting backup..."
    $tmpDir = Join-Path $env:TEMP "sb-backup-$(Get-Random)"
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    $archive = Join-Path $tmpDir "second-brain-backup.tar.gz"
    $encrypted = Join-Path $tmpDir "second-brain-backup.tar.gz.enc"

    if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: tar not found. Install Git Bash or WSL."
        exit 1
    }
    & tar -czf $archive -C $dataRoot chroma meta.db inbox logs processed.json service.log consolidation.log backups 2>&1 | Out-Null

    if (Get-Command age -ErrorAction SilentlyContinue) {
        if (-not [string]::IsNullOrEmpty($passphrase)) {
            echo $passphrase | age -p -o $encrypted $archive
        } else {
            Write-Host "WARNING: No passphrase set, backing up unencrypted"
            Copy-Item $archive $encrypted
        }
    } elseif (Get-Command gpg -ErrorAction SilentlyContinue) {
        if (-not [string]::IsNullOrEmpty($passphrase)) {
            echo $passphrase | gpg --batch --yes --passphrase-fd 0 -c -o $encrypted $archive
        } else {
            Write-Host "WARNING: No passphrase set, backing up unencrypted"
            Copy-Item $archive $encrypted
        }
    } else {
        Write-Host "WARNING: No encryption tool found, backing up unencrypted"
        Copy-Item $archive $encrypted
    }

    if (-not [string]::IsNullOrEmpty($nasHost)) {
        if (-not (Test-NASReachable -Host $nasHost)) {
            Write-Host "NAS unreachable"
            Remove-Item $tmpDir -Recurse -Force
            exit 1
        }
        $remotePath = if ($nasPath) { "$nasHost:$nasPath" } else { "$nasHost:/mnt/indiana/folders/second-brain" }
        & scp -o StrictHostKeyChecking=no $encrypted $remotePath 2>&1 | Out-Null
    }

    Remove-Item $tmpDir -Recurse -Force
    Write-Host "[$(Get-Date)] Backup complete"
}

function Update-Models {
    Write-Host "[$(Get-Date)] Checking model updates..."
    & ollama pull qwen2.5:7b-instruct-q4_K_M
    Write-Host "[$(Get-Date)] Model update check complete"
}

switch ($Action) {
    "backup" { Backup-Data }
    "model-update" { Update-Models }
}
