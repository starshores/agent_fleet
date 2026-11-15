<#
  start_ui.ps1 — 幂等启动 Agent Bus WebUI 控制台为「独立常驻服务」。

  常驻机制(优先级):
    1) Windows 计划任务 ClaudeAgentBusUI:登录自启 + 崩溃自动重启 + 脱离任何 tab
       (Task Scheduler 持有 pythonw 进程,关掉拉起它的 shell 不受影响)。
    2) 降级:若计划任务注册失败(如策略限制),用 pythonw 以隐藏窗口后台拉起
       —— 仍脱离当前 shell 常驻,但无登录自启/崩溃重启。

  幂等:已在跑(8799 端口已监听)就不重复起,杜绝多实例。
  Python 解析:AGENTBUS_PYTHONW_BIN > AGENTBUS_PYTHON_BIN > agent_bus\config.json 的 python_bin > PATH 的 pythonw/python。
  用法:  powershell -ExecutionPolicy Bypass -File <repo>\ui\start_ui.ps1
#>
param([int]$Port = 8799)
$ErrorActionPreference = 'Stop'

$TaskName = 'ClaudeAgentBusUI'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Server   = Join-Path $PSScriptRoot 'server.py'
$CfgPath  = Join-Path $RepoRoot 'agent_bus\config.json'

function Resolve-Pythonw {
  $cands = @()
  if ($env:AGENTBUS_PYTHONW_BIN) { $cands += $env:AGENTBUS_PYTHONW_BIN }
  if ($env:AGENTBUS_PYTHON_BIN)  { $cands += $env:AGENTBUS_PYTHON_BIN }
  if (Test-Path -LiteralPath $CfgPath) {
    try {
      $cfg = Get-Content -LiteralPath $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
      if ($cfg.python_bin) { $cands += [string]$cfg.python_bin }
    } catch { }
  }
  foreach ($p in $cands) {
    if (-not $p -or $p -match '^C:[/\\]path[/\\]to[/\\]python(\.exe)?$') { continue }
    $p = [Environment]::ExpandEnvironmentVariables($p)
    if (Test-Path -LiteralPath $p) {
      $dir = Split-Path -Parent $p
      $w = Join-Path $dir 'pythonw.exe'
      if (Test-Path -LiteralPath $w) { return $w }
      return $p
    }
  }
  $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) {
    $w = Join-Path (Split-Path -Parent $cmd.Source) 'pythonw.exe'
    if (Test-Path -LiteralPath $w) { return $w }
    return $cmd.Source
  }
  throw "找不到 pythonw.exe/python.exe。请设置 AGENTBUS_PYTHON_BIN(或 AGENTBUS_PYTHONW_BIN),或在 agent_bus\config.json 填 python_bin。"
}
$Pythonw = Resolve-Pythonw

function Test-Up {
  try { return (Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 }
  catch { return $false }
}

# 幂等:已在跑就退出
if (Test-Up) { Write-Host "[start] 已在运行 http://127.0.0.1:$Port/ — 跳过(幂等)"; exit 0 }

# 是否管理员:计划任务(崩溃重启)注册需要提权;非提权直接走 pythonw 后台
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)

# 注册/确保计划任务存在(登录自启 + 崩溃重启)—— 仅在提权时尝试
$useTask = $isAdmin
try {
  if (-not $isAdmin) { throw "未提权,跳过计划任务(改用 pythonw 后台 + Startup 自启)" }
  if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    Write-Host "[start] 注册计划任务 $TaskName(登录自启+崩溃重启)..."
    $action    = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Server`" --port $Port"
    $trigger   = New-ScheduledTaskTrigger -AtLogOn
    $settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
                   -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                   -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
                   -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                   -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                   -Settings $settings -Principal $principal `
                   -Description "Agent Bus WebUI 控制台 (127.0.0.1:$Port) — 登录自启+崩溃重启" | Out-Null
  }
  Write-Host "[start] 通过计划任务启动 ..."
  Start-ScheduledTask -TaskName $TaskName
} catch {
  $useTask = $false
  Write-Host "[start] 计划任务方式失败/跳过($($_.Exception.Message));用 WMI 后台拉起(脱离任何 shell job)..."
  # Win32_Process.Create:新进程归属 WmiPrvSE,完全脱离调用方的 job object → 关掉拉起它的 shell 不受影响
  $cmd = "`"$Pythonw`" `"$Server`" --port $Port"
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
  if ($r.ReturnValue -ne 0) { Write-Host "[start] WMI Create 失败 returnValue=$($r.ReturnValue),回退 Start-Process"; Start-Process -FilePath $Pythonw -ArgumentList @("`"$Server`"", "--port", "$Port") -WindowStyle Hidden | Out-Null }
}

# 启动就绪检查(短时有界,非长轮询:最多 ~8s)
for ($i = 0; $i -lt 20; $i++) { if (Test-Up) { break }; Start-Sleep -Milliseconds 400 }

if (Test-Up) {
  $mode = if ($useTask) { "计划任务(登录自启+崩溃重启)" } else { "pythonw 后台(仅脱离 tab)" }
  $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -First 1
  Write-Host "[start] OK — http://127.0.0.1:$Port/  mode=$mode  pid=$owner"
  exit 0
} else {
  Write-Host "[start] 失败:启动后端口 $Port 未就绪。"
  exit 1
}
