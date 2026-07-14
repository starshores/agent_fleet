# sync_code.ps1 - hardlink-mirror agent_bus source+config into agent_fleet\agent_bus\
# Purpose: make agent_fleet self-contained (copy this dir = take the whole bus), bootstrappable.
# Mirrors source+config only; never touches runtime state (per-role queues/presence/events/fleet_status/pid_tags).
# Idempotent: rebuilds hardlinks each run (heals atomic-save link breaks). Same volume (C:) NTFS, no extra space.
# ASCII-ONLY (PS 5.1 mis-parses non-BOM UTF-8 Chinese -> corrupts adjacent lines). Do not add non-ASCII.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\sync_code.ps1

$src = Join-Path $env:USERPROFILE ".claude\agent_bus"
$dst = "C:\apphome\agent_fleet\agent_bus"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$files = @("agentbus.py","worker_daemon.py","dispatch_agent.py","claude_run.py","external_msg.py",
           "pool_dispatcher.py","longrange_net.py","complexity_snapshot.py",
           "start_worker.ps1","stop_worker.ps1","config.json","fleet.json","agents.json",
           "capabilities.json","retired_roster.json","ab.cmd","README.md")
$n = 0
foreach ($f in $files) {
  $s = Join-Path $src $f
  if (-not (Test-Path $s)) { continue }
  $lnk = Join-Path $dst $f
  if (Test-Path $lnk) { Remove-Item $lnk -Force }
  New-Item -ItemType HardLink -Path $lnk -Value $s | Out-Null
  $n++
}

# BUS 2.0 package: recursively mirror every .py under bus/ (incl future submodules bus/core, bus/health...),
# preserving relative layout. *.py only (auto-excludes __pycache__/*.pyc). Same-volume NTFS hardlink.
$busSrc = Join-Path $src "bus"
if (Test-Path $busSrc) {
  Get-ChildItem -Path $busSrc -Recurse -File -Filter *.py | ForEach-Object {
    $rel = $_.FullName.Substring($src.Length).TrimStart('\')
    $lnk = Join-Path $dst $rel
    $lnkDir = Split-Path $lnk -Parent
    New-Item -ItemType Directory -Force -Path $lnkDir | Out-Null
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
    New-Item -ItemType HardLink -Path $lnk -Value $_.FullName | Out-Null
    $n++
  }
}
Write-Output ("bus source hardlink mirror done: " + $n + " files -> " + $dst)
