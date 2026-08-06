$ErrorActionPreference = 'SilentlyContinue'
"TOTAL_RAM_KB=" + [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1KB)
"FREE_RAM_KB=" + (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory
"DISK_FREE_B=" + (Get-PSDrive C).Free
"DISK_USED_B=" + (Get-PSDrive C).Used
"OLLAMA_PROC=" + (Get-Process ollama* | Measure-Object).Count
try {
  $ps = Invoke-RestMethod -Uri http://127.0.0.1:11434/api/ps -TimeoutSec 5
  foreach ($m in $ps.models) {
    "MODEL=" + $m.name + " | vram=" + $m.size_vram + " | size=" + $m.size
  }
  "OLLAMA_RUNNING=yes"
} catch { "OLLAMA_RUNNING=no" }
