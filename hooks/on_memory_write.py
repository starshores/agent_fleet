#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PostToolUse hook:当 Write/Edit 改到 ① 项目记忆 .md 或 ② agent_bus 总线源码 时,自动重链硬链备份。
让 agent_fleet\\memory\\(记忆)和 agent_fleet\\agent_bus\\(总线源码)永远是最新完整快照——
只迁移 agent_fleet 即带走全部记忆+代码,可自举。Claude Code 经 stdin 传 JSON;不匹配则秒退、不干扰。"""
import sys, json, os, subprocess
from pathlib import Path

SYNC_CODE = r"C:\apphome\agent_fleet\sync_code.ps1"
_CODE_EXT = (".py", ".ps1", ".json", ".cmd")

# sync 脚本路径从 agent_bus/config.json 读(集中配置);读不到回退到默认。
def _sync_script():
    try:
        abdir = Path(os.path.expandvars(os.path.expanduser(os.environ.get("AGENTBUS_DIR") or str(Path.home() / ".claude" / "agent_bus"))))
        v = json.loads((abdir / "config.json").read_text(encoding="utf-8")).get("sync_memory_script")
        if v:
            return os.path.expandvars(os.path.expanduser(v))
    except Exception:
        pass
    return r"C:\apphome\agent_fleet\sync_memory.ps1"

SYNC = _sync_script()

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    ti = data.get("tool_input") or {}
    fp = (ti.get("file_path") or "").replace("/", "\\").lower()

    def _run(script):
        try:
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                           capture_output=True, timeout=30)
        except Exception:
            pass

    # ① 项目记忆 .md(agent_fleet\memory 不含 .claude,避免自触发/递归)→ 重链记忆镜像
    if "\\.claude\\" in fp and "\\memory\\" in fp and fp.endswith(".md"):
        _run(SYNC)
    # ② agent_bus 总线源码(.py/.ps1/.json/.cmd)→ 重链代码镜像,保持 agent_fleet 自举
    elif "\\.claude\\agent_bus\\" in fp and fp.endswith(_CODE_EXT):
        _run(SYNC_CODE)

if __name__ == "__main__":
    main()
