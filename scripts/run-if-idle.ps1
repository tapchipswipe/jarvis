# Jarvis — Windows Idle-Aware Runner
# Only runs if current time is in the allowed window AND system is idle

param(
    [Parameter(Mandatory=$true)]
    [string]$Command
)

$ErrorActionPreference = "Stop"

function Test-IdleTime {
    try {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Idle {
    [DllImport("user32.dll")]
    public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }
}
"@
        $idleInfo = New-Object Win32Idle::LASTINPUTINFO
        $idleInfo.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($idleInfo)
        [Win32Idle]::GetLastInputInfo([ref]$idleInfo) | Out-Null
        $idleMs = [Environment]::TickCount - $idleInfo.dwTime
        return ($idleMs -ge (30 * 60 * 1000))
    } catch {
        return $false
    }
}

$hour = [int](Get-Date -Format "HH")
if ($hour -lt 1 -or $hour -ge 6) {
    exit 0
}

if (-not (Test-IdleTime)) {
    exit 0
}

Invoke-Expression $Command
