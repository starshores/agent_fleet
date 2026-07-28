# start_worker.ps1 - launch a hidden background worker daemon (no visible tab). Idempotent.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File start_worker.ps1 -Role <role> [-Cwd <dir>]
# (ASCII-only on purpose: PowerShell 5.1 misreads UTF-8-without-BOM .ps1 and breaks.)
param(
  [Parameter(Mandatory=$true)][string]$Role,
  [string]$Cwd = "C:\BaseSpace\XXX",
  [double]$WaitTimeout = 300
)
$bus = Join-Path $env:USERPROFILE ".claude\agent_bus"
$daemon = Join-Path $bus "worker_daemon.py"
$pres = Join-Path $bus "$Role\presence.json"

if (Test-Path $pres) {
  try {
    $p = Get-Content -Raw -Encoding UTF8 $pres | ConvertFrom-Json
    if ($p.mode -eq "daemon" -and $p.daemon_pid) {
      if (Get-Process -Id $p.daemon_pid -ErrorAction SilentlyContinue) {
        Write-Output "ALREADY RUNNING: role=$Role daemon_pid=$($p.daemon_pid), skip"; exit 0
      }
    }
    # Mutual-exclusion: refuse if a LIVE visible tab-loop already serves this role.
    # tab /loop and daemon must be one-or-the-other (else they race the same inbox + flip presence).
    if ($p.mode -eq "listening" -and $p.claude_pid) {
      if (Get-Process -Id $p.claude_pid -ErrorAction SilentlyContinue) {
        Write-Output "REFUSE: role=$Role already has a live tab-loop (claude_pid=$($p.claude_pid)). Stop that loop first, or do not run a daemon for this role."
        exit 1
      }
    }
  } catch {}
}

# P1 (2026-07-28): use config.json python_bin (env with watchdog/psutil), not bare PATH "python".
# Bare python may lack watchdog -> daemon dies silently at import, looks "started" here but never serves.
$python = "python"
$cfgPath = Join-Path $bus "config.json"
if (Test-Path $cfgPath) {
  try {
    $cfg = Get-Content -Raw -Encoding UTF8 $cfgPath | ConvertFrom-Json
    if ($cfg.python_bin) { $python = $cfg.python_bin }
  } catch {}
}
if ($env:AGENTBUS_PYTHON_BIN) { $python = $env:AGENTBUS_PYTHON_BIN }

$argList = @($daemon, "--role", $Role, "--cwd", $Cwd, "--wait-timeout", $WaitTimeout)
$proc = Start-Process -FilePath $python -ArgumentList $argList -WindowStyle Hidden -PassThru
Write-Output "STARTED worker daemon: role=$Role pid=$($proc.Id) (hidden; log: $bus\$Role\daemon.log)"
