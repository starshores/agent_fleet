# sync_code.ps1 — 把 agent_bus 总线【源代码+配置】硬链镜像到 agent_fleet\agent_bus\
# 目的:让 agent_fleet 自包含全部源码(只迁移本目录即带走总线本体),可自举/换机。
# 只镜像源码+配置,绝不碰运行态(各 role 队列/presence/events.jsonl/fleet_status.json/pid_tags)。
# 幂等:每次重建硬链(应对原子保存断链)。同卷(C:)NTFS,不占额外空间。
# 用法:powershell -NoProfile -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\sync_code.ps1

$src = Join-Path $env:USERPROFILE ".claude\agent_bus"
$dst = "C:\apphome\agent_fleet\agent_bus"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$files = @("agentbus.py","worker_daemon.py","dispatch_agent.py","claude_run.py",
           "start_worker.ps1","stop_worker.ps1","config.json","fleet.json","agents.json",
           "ab.cmd","README.md")
$n = 0
foreach ($f in $files) {
  $s = Join-Path $src $f
  if (-not (Test-Path $s)) { continue }
  $lnk = Join-Path $dst $f
  if (Test-Path $lnk) { Remove-Item $lnk -Force }
  New-Item -ItemType HardLink -Path $lnk -Value $s | Out-Null
  $n++
}
Write-Output ("总线源代码硬链镜像完成: " + $n + " 个 -> " + $dst)
