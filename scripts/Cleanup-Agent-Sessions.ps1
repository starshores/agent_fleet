[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Apply', 'Restore', 'Purge')]
    [string]$Action = 'Plan',
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string[]]$CodexHome = @(),
    [string[]]$ClaudeHome = @(),
    [string[]]$ClaudeProjectsRoot = @(),
    [string]$PlanPath,
    [string]$Archive,
    [string]$ConfirmArchiveId,
    [switch]$ClientsStopped,
    [switch]$AllowSupportedSubset,
    [string]$Python = 'C:/path/to/python.exe'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$arguments = @('-B', (Join-Path $PSScriptRoot 'session_cleanup.py'), $Action.ToLowerInvariant())
if ($Action -eq 'Plan') {
    $arguments += @('--scope', 'AllSessions', '--project-root', $ProjectRoot)
    foreach ($root in $CodexHome) { $arguments += @('--codex-home', $root) }
    foreach ($root in $ClaudeHome) { $arguments += @('--claude-home', $root) }
    foreach ($root in $ClaudeProjectsRoot) { $arguments += @('--claude-projects-root', $root) }
    if ($PlanPath) { $arguments += @('--output', $PlanPath) }
} elseif ($Action -eq 'Apply') {
    if (-not $PlanPath) { throw 'Apply requires -PlanPath.' }
    $arguments += @('--scope', 'AllSessions', '--plan', $PlanPath)
    if ($ClientsStopped) { $arguments += '--clients-stopped' }
    if ($AllowSupportedSubset) { $arguments += '--allow-supported-subset' }
} else {
    if (-not $Archive) { throw 'Restore/Purge requires -Archive.' }
    $arguments += @('--archive', $Archive)
    if ($ClientsStopped) { $arguments += '--clients-stopped' }
    if ($Action -eq 'Purge') {
        if (-not $ConfirmArchiveId) { throw 'Purge requires -ConfirmArchiveId.' }
        $arguments += @('--confirm-archive-id', $ConfirmArchiveId)
    }
}
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw "Session maintenance stopped (exit $LASTEXITCODE)." }
