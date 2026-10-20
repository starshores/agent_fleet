[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [string]$Python = 'C:/path/to/python.exe',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$taskProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\','/')
$taskSourceRoot = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\','/')
$taskManifestPath = [IO.Path]::GetFullPath($Manifest)
$taskArchivePrefix = (Join-Path $taskProjectRoot 'releases') + [IO.Path]::DirectorySeparatorChar
if (-not $taskManifestPath.StartsWith($taskArchivePrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest must be inside project releases.' }
if ($taskSourceRoot -eq [IO.Path]::GetPathRoot($taskSourceRoot).TrimEnd('\') -or $taskSourceRoot -eq $env:USERPROFILE -or $taskSourceRoot -eq $taskProjectRoot -or $taskProjectRoot.StartsWith($taskSourceRoot + '\',[StringComparison]::OrdinalIgnoreCase) -or $taskSourceRoot.StartsWith($taskProjectRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe source root.' }
function Assert-TaskCandidatePath {
    param([string]$Path, [ValidateSet('File','Directory')][string]$Kind, [switch]$AllowMissing)
    $candidatePath = [IO.Path]::GetFullPath($Path)
    if (-not $candidatePath.StartsWith($taskSourceRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Candidate escapes source root.' }
    # Inspect ancestors first. Never inspect a leaf through an unchecked junction.
    $ancestors = [System.Collections.Generic.List[string]]::new()
    $cursor = $candidatePath
    while ($cursor) {
        $ancestors.Add($cursor)
        $parent = [IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName
    }
    for ($index = $ancestors.Count - 1; $index -ge 0; $index--) {
        try { $item = Get-Item -LiteralPath $ancestors[$index] -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] {
            if ($AllowMissing) { return $false }
            throw
        }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Linked path cannot be removed.' }
        if ($index -gt 0 -and -not $item.PSIsContainer) { throw 'Candidate ancestor is not a directory.' }
        if ($index -eq 0 -and (($Kind -eq 'Directory') -ne [bool]$item.PSIsContainer)) { throw 'Candidate type changed.' }
    }
    return $true
}
$taskManifest = Get-Content -LiteralPath $taskManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not [string]::Equals([IO.Path]::GetFullPath($taskManifest.source_root).TrimEnd('\','/'), $taskSourceRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Source root does not match the archived root.' }
& $Python -B (Join-Path $PSScriptRoot 'archive_external_development.py') verify --manifest $taskManifestPath
if ($LASTEXITCODE -ne 0) { throw 'Archive/source verification failed.' }
$taskCandidates = @()
foreach ($taskRow in $taskManifest.files) {
    $taskPath = [IO.Path]::GetFullPath((Join-Path $taskSourceRoot $taskRow.path))
    [void](Assert-TaskCandidatePath -Path $taskPath -Kind File)
    if ((Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash -ne $taskRow.sha256) { throw 'Source changed after archive.' }
    $taskCandidates += [PSCustomObject]@{ Path=$taskPath; Sha256=$taskRow.sha256; Bytes=$taskRow.bytes }
}
# Only task-specific Python/Node/Codex processes are considered; no other command line is returned.
$taskActive = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(pythonw?|node|codex)\.exe$' -and $_.CommandLine -and ($_.CommandLine.IndexOf($taskSourceRoot,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or $_.CommandLine.IndexOf($taskSourceRoot.Replace('\','/'),[StringComparison]::OrdinalIgnoreCase) -ge 0) })
if ($taskActive.Count) { throw 'A task-specific preview or runtime is still using this source root; stop it before cleanup.' }
if (-not $Apply) {
    [PSCustomObject]@{Apply=$false;SourceRoot=$taskSourceRoot;VerifiedFiles=$taskCandidates.Count;Excluded=@($taskManifest.excluded).Count} | ConvertTo-Json
    exit 0
}
$taskRemoved = [System.Collections.Generic.List[string]]::new()
$taskRemovedDirectories = [System.Collections.Generic.List[string]]::new()
$taskSkippedWorktree = [System.Collections.Generic.List[string]]::new()
$taskReportPath = Join-Path ([IO.Path]::GetDirectoryName($taskManifestPath)) 'cleanup-result.json'
$taskOutcome = 'running'
$taskCompleted = $false
$taskFailure = $null
$taskWorktreeRoot = [IO.Path]::GetFullPath((Join-Path $taskSourceRoot 'work/master-agent-v3'))
function Write-TaskCleanupReport {
    $unremovedPaths = @($taskCandidates | Where-Object { -not $taskRemoved.Contains($_.Path) } | ForEach-Object { $_.Path })
    [PSCustomObject]@{
        Outcome=$taskOutcome; Completed=$taskCompleted; Error=$taskFailure
        SourceRoot=$taskSourceRoot; Archive=$taskManifest.archive; ArchiveSha256=$taskManifest.archive_sha256
        PlannedFiles=$taskCandidates.Count; RemovedFiles=$taskRemoved.Count; RemovedPaths=$taskRemoved.ToArray()
        RemovedDirectories=$taskRemovedDirectories.ToArray()
        UnremovedArchivedFiles=$unremovedPaths.Count; UnremovedArchivedPaths=$unremovedPaths
        SkippedWorktreePaths=$taskSkippedWorktree.ToArray(); Excluded=$taskManifest.excluded; WorktreeRemovedByGit=$false
        RemainingMeaning='Not confirmed removed by this run; not a fresh filesystem census. Excluded scopes and the Git worktree remain separate.'
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $taskReportPath -Encoding UTF8
}
# Leave a non-completion marker if the host is terminated before finally runs.
Write-TaskCleanupReport
try {
    foreach ($taskCandidate in $taskCandidates) {
        # The Git worktree is archived but is removed through Git, separately.
        if ($taskCandidate.Path.StartsWith($taskWorktreeRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { $taskSkippedWorktree.Add($taskCandidate.Path); continue }
        [void](Assert-TaskCandidatePath -Path $taskCandidate.Path -Kind File)
        if ((Get-FileHash -LiteralPath $taskCandidate.Path -Algorithm SHA256).Hash -ne $taskCandidate.Sha256) { throw 'Candidate changed immediately before removal.' }
        [void](Assert-TaskCandidatePath -Path $taskCandidate.Path -Kind File)
        Remove-Item -LiteralPath $taskCandidate.Path -Force
        $taskRemoved.Add($taskCandidate.Path)
    }
    # Only remove proven empty directories, never recurse into excluded homes.
    foreach ($taskDirectory in @($taskManifest.directories | Sort-Object Length -Descending)) {
        $taskDirectoryPath = [IO.Path]::GetFullPath((Join-Path $taskSourceRoot $taskDirectory))
        if ($taskDirectoryPath -eq $taskWorktreeRoot -or $taskDirectoryPath.StartsWith($taskWorktreeRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { continue }
        if (Assert-TaskCandidatePath -Path $taskDirectoryPath -Kind Directory -AllowMissing) {
            if (-not (Get-ChildItem -LiteralPath $taskDirectoryPath -Force | Select-Object -First 1)) {
                [void](Assert-TaskCandidatePath -Path $taskDirectoryPath -Kind Directory)
                Remove-Item -LiteralPath $taskDirectoryPath
                $taskRemovedDirectories.Add($taskDirectoryPath)
            }
        }
    }
    $taskCompleted = $true
    $taskOutcome = 'completed'
} catch {
    $taskOutcome = if ($taskRemoved.Count -or $taskRemovedDirectories.Count) { 'partial' } else { 'failed' }
    $taskFailure = [PSCustomObject]@{Type=$_.Exception.GetType().FullName; Message=$_.Exception.Message}
    throw
} finally {
    Write-TaskCleanupReport
}
[PSCustomObject]@{Outcome=$taskOutcome;Completed=$taskCompleted;RemovedFiles=$taskRemoved.Count;Report=$taskReportPath;Remaining='Git worktree and excluded native homes require separate handling.'} | ConvertTo-Json
