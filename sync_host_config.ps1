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
$n = 0
foreach ($p in $pairs) {
  if (Test-Path $p.src) {
    Copy-Item -Path $p.src -Destination (Join-Path $dst $p.out) -Force
    $n++
  } else {
    Write-Output ("跳过(源不存在): " + $p.src)
  }
}
Write-Output ("宿主配置快照刷新完成: " + $n + " 个 -> " + $dst)
