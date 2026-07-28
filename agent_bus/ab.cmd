@echo off
REM ab.cmd - bus 短命令入口(cmd/PowerShell)。固化正确 env python + 真实项目根路径 + UTF-8。
REM 换机(P2 归一·2026-07-28):优先设 AGENTBUS_PYTHON_BIN / AGENTBUS_AGENT_FLEET_HOME 环境变量
REM (与 config.json 的 python_bin / agent_fleet_home 同义);不设用默认。启动器读不了 config
REM (鸡生蛋),env 覆盖即归一机制——五处入口同认这两个 env。
set PYTHONUTF8=1
set "AB_PY=C:\path\to\python.exe"
set "AB_AF=C:\apphome\agent_fleet"
if not "%AGENTBUS_PYTHON_BIN%"=="" set "AB_PY=%AGENTBUS_PYTHON_BIN%"
if not "%AGENTBUS_AGENT_FLEET_HOME%"=="" set "AB_AF=%AGENTBUS_AGENT_FLEET_HOME%"
"%AB_PY%" "%AB_AF%\agent_bus\agentbus.py" %*
