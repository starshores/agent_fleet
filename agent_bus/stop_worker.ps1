# stop_worker.ps1 - stop a role's background worker daemon (by presence daemon_pid).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File stop_worker.ps1 -Role <role>
# (ASCII-only on purpose: PowerShell 5.1 misreads UTF-8-without-BOM .ps1 and breaks.)
param([Parameter(Mandatory=$true)][string]$Role)
$bus = Join-Path $env:USERPROFILE ".claude\agent_bus"
$pres = Join-Path $bus "$Role\presence.json"
if (-not (Test-Path $pres)) { Write-Output "no presence: $Role"; exit 0 }
try {
  $p = Get-Content -Raw -Encoding UTF8 $pres | ConvertFrom-Json
  if ($p.mode -eq "daemon" -and $p.daemon_pid) {
    if (Get-Process -Id $p.daemon_pid -ErrorAction SilentlyContinue) {
      Stop-Process -Id $p.daemon_pid -Force
      Write-Output "STOPPED role=$Role daemon_pid=$($p.daemon_pid)"
    } else {
      Write-Output "daemon_pid $($p.daemon_pid) not running"
    }
  } else {
    Write-Output "$Role not in daemon mode, untouched"
  }
} catch {
  Write-Output "read/stop failed"
}
