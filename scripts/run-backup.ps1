# Jarvis — Windows Idle-Aware Backup Runner
param()
$ErrorActionPreference = "Stop"

function Test-NASReachable {
    param([string]$Host)
    try {
        $result = Test-NetConnection -ComputerName $Host -Port 22 -WarningAction SilentlyContinue
        return $result.TcpTestSucceeded
    } catch {
        return $false
    }
}

$nasHost = $env:BACKUP_NAS_HOST
if (-not [string]::IsNullOrEmpty($nasHost) -and -not (Test-NASReachable -Host $nasHost)) {
    Write-Host "NAS unreachable, skipping backup"
    exit 0
}

$scriptPath = Join-Path $PSScriptRoot "backup.ps1"
& $scriptPath -Action backup
