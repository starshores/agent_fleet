<#
  stop_ui.ps1 — 停止 Agent Bus WebUI 常驻服务。

  默认:停掉正在跑的实例(结束计划任务 + 杀掉占用 8799 端口的进程),
        但保留登录自启的计划任务(下次登录仍会拉起)。
  -Uninstall:连同卸载计划任务,彻底关闭自启。

  用法:  powershell -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\ui\stop_ui.ps1 [-Uninstall]
#>
param([int]$Port = 8799, [switch]$Uninstall)

$TaskName = 'ClaudeAgentBusUI'

# 1) 结束计划任务当前运行实例
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop; Write-Host "[stop] 已结束计划任务实例" } catch {}

# 2) 杀掉占用端口的进程(兼容降级/手动拉起的实例)
$owners = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
foreach ($procId in $owners) {
  try { Stop-Process -Id $procId -Force -ErrorAction Stop; Write-Host "[stop] 已杀进程 pid $procId" } catch {}
}

# 3) 可选:卸载自启任务
if ($Uninstall) {
  try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop; Write-Host "[stop] 已卸载自启任务 $TaskName" } catch {}
}

Write-Host "[stop] done"
