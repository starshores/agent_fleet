# sync_host_config.ps1 — 刷新宿主全局配置【快照】到 agent_fleet\host_config\
# 配置类(settings.json)改动少,用快照(非硬链)即可——用户定调 2026-06-30。
# 改过 settings.json 后跑一次,让迁移母本保持新鲜。
# 注:全局 CLAUDE.md 已【物理迁入】host_config\CLAUDE.global.md(2026-07-27 用户令),
#     ~/.claude/CLAUDE.md 是指向它的文件软链接——不再快照、改动直接编辑物理原件即时生效。
# 用法:powershell -NoProfile -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\sync_host_config.ps1

$dst = "C:\apphome\agent_fleet\host_config"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

# 守卫:确认 ~/.claude/CLAUDE.md 仍是软链(原子写会把它打回普通文件=分叉)
$link = "$env:USERPROFILE\.claude\CLAUDE.md"
$item = Get-Item $link -ErrorAction SilentlyContinue
if ($null -eq $item -or $item.LinkType -ne "SymbolicLink") {
  Write-Output ("⚠ 警告: " + $link + " 不是软链接(可能被原子写打回普通文件)。请核对内容并重建:")
  Write-Output ('   cmd /c mklink "' + $link + '" "' + $dst + '\CLAUDE.global.md"  (需管理员)')
}

$pairs = @(
  @{ src = "$env:USERPROFILE\.claude\settings.json"; out = "settings.snapshot.json" }
)

# 复制前白名单:只快照非敏感键;env 中命中 API_KEY/TOKEN/SECRET 的键跳过。
# 白名单外(含 skipDangerousModePermissionPrompt 等)不写入快照。
$allowedKeys = @('env','model','hooks','enabledPlugins','feedbackSurveyRate','autoUpdatesChannel','tui','_env_note_reap')
$sensitiveEnv = '(?i)(API[_-]?KEY|TOKEN|SECRET)'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$n = 0
foreach ($p in $pairs) {
  if (-not (Test-Path -LiteralPath $p.src)) {
    Write-Output ("跳过(源不存在): " + $p.src)
    continue
  }
  try {
    $srcObj = Get-Content -LiteralPath $p.src -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $srcObj) { throw "settings.json 解析为空" }
    $snap = [ordered]@{}
    foreach ($k in $allowedKeys) {
      if ($srcObj.PSObject.Properties.Name -contains $k) {
        $snap[$k] = $srcObj.$k
      }
    }
    if ($snap.Contains('env') -and $null -ne $snap['env']) {
      $cleanEnv = [ordered]@{}
      foreach ($prop in $snap['env'].PSObject.Properties) {
        if ($prop.Name -match $sensitiveEnv) {
          Write-Output ("⚠ 警告: env 键 " + $prop.Name + " 命中敏感关键字,已跳过(不写入快照)")
        } else {
          $cleanEnv[$prop.Name] = $prop.Value
        }
      }
      $snap['env'] = $cleanEnv
    }
    $json = $snap | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText((Join-Path $dst $p.out), ($json + "`r`n"), $utf8NoBom)
    $n++
  } catch {
    Write-Output ("⚠ 警告: 解析/写入失败,跳过本次快照: " + $_.Exception.Message)
  }
}
Write-Output ("宿主配置快照刷新完成: " + $n + " 个 -> " + $dst)
