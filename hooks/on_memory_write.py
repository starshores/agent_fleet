#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PostToolUse hook:当 Write/Edit 改到 ① 项目记忆 .md 或 ② agent_bus 总线源码 时,自动重链硬链备份。
让 agent_fleet\\memory\\(记忆)和 agent_fleet\\agent_bus\\(总线源码)永远是最新完整快照——
只迁移 agent_fleet 即带走全部记忆+代码,可自举。Claude Code 经 stdin 传 JSON;不匹配则秒退、不干扰。"""
import sys, json, os, subprocess
from pathlib import Path

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
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))   # 显式 UTF-8:裸 json.load 走 GBK,payload 含 CJK 即崩=hook 静默失效(全局铁律)
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
        # ①b 漂移提醒(2.3 P1·2026-07-29):写了记忆 .md 却没进任何 *MEMORY.md 索引=孤儿
        # (下次谁也想不起)。PostToolUse stdout 会回给模型 → 确定性提醒当场补索引。
        # 只提醒不拦;索引文件自身/索引已含其名则静默。
        try:
            import glob
            from pathlib import Path as _P
            w = _P(ti.get("file_path"))
            if not w.name.endswith("MEMORY.md"):
                idx_text = ""
                for g in glob.glob(str(w.parent / "*MEMORY.md")):
                    try:
                        idx_text += open(g, encoding="utf-8", errors="replace").read()
                    except Exception:
                        pass
                if idx_text and w.name not in idx_text and w.stem not in idx_text:
                    print("⚠ 记忆漂移提醒:%s 尚未登入任何 *MEMORY.md 索引——现在就把一行指针补进"
                          "你的角色索引(<role>.MEMORY.md;全角色公共纪律才进根 MEMORY.md),否则=孤儿记忆。" % w.name)
        except Exception:
            pass
    # ②(已卸·2026-07-28 doc-blind 审查):agent_bus 源码硬链镜像已被 junction 取代(07-15),
    # sync_code.ps1 检测到 reparse 即 no-op——之前每次改总线源码都白起一个 powershell(30s 预算)烧在空转上。

if __name__ == "__main__":
    main()
