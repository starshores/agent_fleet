#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""recover.py — MasterAgent(基础设施层)自恢复:探活体状态并打印。
冷启动新 CLI 跑这一条,立刻知道:谁在跑、有没有在飞的活、通道是否就绪、去哪取深度知识。"""
import sys, os, subprocess, json, urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 机器相关路径集中在 agent_bus/config.json(迁移/换机改那一处);env AGENTBUS_<KEY> 可覆盖。
_ABDIR = Path(os.path.expandvars(os.path.expanduser(os.environ.get("AGENTBUS_DIR") or str(Path.home() / ".claude" / "agent_bus"))))


def _cfg(key, default=None):
    v = os.environ.get("AGENTBUS_" + key.upper())
    if v:
        return os.path.expandvars(os.path.expanduser(v))
    try:
        v = json.loads((_ABDIR / "config.json").read_text(encoding="utf-8")).get(key, default)
    except Exception:
        v = default
    return os.path.expandvars(os.path.expanduser(v)) if isinstance(v, str) else v


AGENTBUS = str(_ABDIR / "agentbus.py")
MEMORY_INDEX = _cfg("memory_index", str(Path.home() / ".claude" / "projects" / "XXX" / "memory" / "MEMORY.md"))
REPO = _cfg("repo", r"C:\BaseSpace\XXX")
COORD = "MasterAgent"  # 本目录身份=基础设施层 MasterAgent;Master 是另一个业务总控 tab


def line(c="-"):
    print(c * 64)


def run_bus(*args):
    try:
        out = subprocess.run([sys.executable, AGENTBUS, *args],
                             capture_output=True, text=True, encoding="utf-8", timeout=20)
        return (out.stdout or "") + (out.stderr or "")
    except Exception as e:
        return "(agentbus 调用失败: %s)" % e


def check_edge_cdp():
    try:
        with urllib.request.urlopen("http://localhost:9222/json/version", timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
            return "✓ 在(%s)" % d.get("Browser", "?")
    except Exception:
        return ("✗ 不在(用桌面『启动Edge-可自动化.bat』开带 9222 的 Edge。"
                "★2026-07-20:Edge 136+ 已禁【默认档】CDP,必须用【专用档】"
                "C:\\apphome\\edge_cdp_profile;bat 已按此配。通用 web 自动化用,非仅云文档)")


def main():
    line("=")
    print("MASTER AGENT 自恢复 — 活体状态")
    line("=")

    print("\n[1] agent 管理:谁在跑 + 是谁")
    print(run_bus("who").rstrip())

    print("\n[2] 任务队列(inbox 待领 / processing 在做 / done 待读)")
    print(run_bus("list").rstrip())

    print("\n[2.5] 孤儿任务巡检(reap --dry-run:owner 已死/超期仍卡 processing 的)")
    orphan = run_bus("reap", "--dry-run").rstrip()
    print(orphan if orphan else "(无)")

    print("\n[2.6] ⭐ Bus 健康总检(ab health,只读:自醒/队列/消息丢失/daemon舰队/WebUI/edge锁 + 总判定)")
    hl = run_bus("health").rstrip()
    print(hl if hl else "(无)")

    print("\n[3] 回填给 MasterAgent 的结果(replies)")
    rep = run_bus("replies", "--role", COORD).rstrip()
    print(rep if rep else "(无)")

    print("\n[4] 通道健康")
    print("  Edge CDP(9222):", check_edge_cdp())
    print("  sql_agent:", "走 %s\\sql_agent\\ inbox/shared 协议(见 CAPABILITIES.md #1)" % REPO)
    print("  调度系统:", "按部署环境配置(不入仓)")

    print("\n[5] 深度知识 / 坐标")
    print("  记忆索引(Read 它):", MEMORY_INDEX)
    print("  通道说明书:", r"C:\apphome\agent_fleet\CAPABILITIES.md")
    print("  agent 管理:", r"C:\apphome\agent_fleet\AGENTS.md")
    print("  能力池(谁有啥能力):ab caps [--role X];新人 onboarding:ab send --to 培训师 --task '带一下 X'")
    print("  代码仓:", REPO)

    line("=")
    print("下一步:① 你是 MasterAgent(基础设施层)tab → ab hello --role MasterAgent")
    print("        ② 看上面 [2]/[3] 有没有在飞的活(派来的基建升级任务 / 待读回填)")
    print("        ③ 每升级一次基建,主动 ab send --to Master --from MasterAgent 简报怎么用")
    line("=")


if __name__ == "__main__":
    main()
