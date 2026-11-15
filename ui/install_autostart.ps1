<#
  install_autostart.ps1 — 登录自启(无需管理员)。

  在当前用户「启动」文件夹放一个隐藏启动器(.vbs),登录时静默运行 start_ui.ps1,
  由它幂等拉起 WebUI 常驻服务。无需提权。

  -Remove:移除自启。
  用法:  powershell -ExecutionPolicy Bypass -File <repo>\ui\install_autostart.ps1 [-Remove]
#>
param([switch]$Remove)

$startup = [Environment]::GetFolderPath('Startup')   # %APPDATA%\...\Start Menu\Programs\Startup
$vbs     = Join-Path $startup 'ClaudeAgentBusUI.vbs'
$start   = Join-Path $PSScriptRoot 'start_ui.ps1'

if ($Remove) {
  if (Test-Path -LiteralPath $vbs) { Remove-Item -LiteralPath $vbs -Force; Write-Host "[autostart] 已移除 $vbs" }
  else { Write-Host "[autostart] 无自启项可移除" }
  return
}
if (-not (Test-Path -LiteralPath $start)) { throw "找不到 start_ui.ps1:$start" }

# .vbs 用 WScript.Shell 隐藏运行 powershell(窗口样式 0 = 不弹窗)
$content = @"
' 自动生成 — Agent Bus WebUI 登录自启(隐藏)
CreateObject("WScript.Shell").Run "powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$start""", 0, False
"@
Set-Content -LiteralPath $vbs -Value $content -Encoding Unicode
Write-Host "[autostart] 已安装登录自启:$vbs"
Write-Host "[autostart] 登录时将静默运行:$start"
