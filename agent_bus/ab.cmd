@echo off
REM ab.cmd - bus 短命令入口(cmd/PowerShell)。BUS 2.0 使用体验:固化正确 env python + 真实项目根路径 + UTF-8。
REM 用法:ab wait --role X --timeout 3600 / ab who / ab done --id .. --role .. --result .. …  换机改下面两路径。
set PYTHONUTF8=1
"C:\path\to\python.exe" "C:\apphome\agent_fleet\agent_bus\agentbus.py" %*
