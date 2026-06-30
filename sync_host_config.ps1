# sync_host_config.ps1 — 刷新宿主全局配置【快照】到 agent_fleet\host_config\
# 配置类(全局 CLAUDE.md / settings.json)改动少,用快照(非硬链)即可——用户定调 2026-06-30。
# 改过全局配置后跑一次,让迁移母本保持新鲜。
# 用法:powershell -NoProfile -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\sync_host_config.ps1

$dst = "C:\apphome\agent_fleet\host_config"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$pairs = @(
  @{ src = "$env:USERPROFILE\.claude\CLAUDE.md";     out = "CLAUDE.global.snapshot.md" },
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
