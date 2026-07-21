#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop hook —— 强制 bus agent 回合结束前保有 armed 自醒 wait(防前台CLI掉自醒 · 方案A-min)。

Master 2026-07-08 拍板(fleet运维域):A-min+B+D 根治"前台CLI干完活空闲期没重挂wait→掉自醒
→事件不可达"(曾有 agent 这样掉线 18h 致积压+重复分流)。关键约束:外部无法替agent重挂(wait须在agent
自己harness里),所以在【agent自己回合结束时】强制它重挂=唯一物理可行的机制兜底。

逻辑:回合结束 → 按 session_id 反查 role → 若是 bus agent 且当前【无 armed wait 进程】→ block
      + 指示它立刻重挂;否则放行。
放行(不block)的情形:① stop_hook_active(harness若提供·防循环)② subagent(agent_type存在,
一次性不自醒)③ 个人会话/无presence(反查不到role)④ 伪角色(用户/unknown)⑤ 已 armed
⑥ 同 role 在 GUARD_WINDOW 秒内已 block 过(防 agent 无法重挂时死循环)。
fail-open:任何异常 / 无 psutil → 放行,绝不卡死回合。UTF-8 强制(Win GBK 遇 CJK 崩)。
"""
import sys, json, os, time, re
from pathlib import Path

try:
    import psutil
except Exception:
    psutil = None

BUS = Path(os.environ.get("AGENTBUS_DIR", str(Path.home() / ".claude" / "agent_bus")))
GUARD_WINDOW = 120  # 秒:同 role 此窗口内已 block 过 → 放行(防死循环)


def _resolve_role(sid):
    if not sid:
        return None
    try:
        for pf in BUS.glob("*/presence.json"):
            try:
                d = json.loads(pf.read_text(encoding="utf-8"))
                if d.get("session_id") == sid:
                    return d.get("role")
            except Exception:
                continue
    except Exception:
        pass
    return None


def _has_armed_wait(role):
    """该 role 是否有活的独立 agentbus wait python 进程(精确匹 --role,同 wait-proc-scan-python-only)。"""
    if psutil is None:
        return True  # 判不了 → 保守当有(放行,不误 block)
    for p in psutil.process_iter(["name"]):
        try:
            if "python" not in (p.info.get("name") or "").lower():
                continue
            cl = p.cmdline()
            if "wait" not in cl:
                continue
            s = " ".join(cl)
            if "agentbus" not in s:
                continue
            m = re.search(r"--role\s+(\S+)", s)
            if m and m.group(1) == role:
                return True
        except Exception:
            continue
    return False


def _guard_path(role):
    return BUS / role / ".stop_rearm_guard"


def _recently_blocked(role):
    p = _guard_path(role)
    try:
        if p.exists():
            return (time.time() - float(p.read_text().strip())) < GUARD_WINDOW
    except Exception:
        pass
    return False


def _mark_blocked(role):
    try:
        _guard_path(role).write_text(str(time.time()))
    except Exception:
        pass


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return  # fail-open
    if data.get("stop_hook_active"):
        return
    if data.get("agent_type"):
        return  # subagent 不自醒
    role = _resolve_role(data.get("session_id"))
    if not role or role in ("用户", "unknown"):
        return  # 个人会话/伪角色
    if _has_armed_wait(role):
        return  # 已 armed
    if _recently_blocked(role):
        return  # 防死循环:刚 block 过还没 armed 上,放行别卡
    _mark_blocked(role)
    reason = (
        "⚠ 你(%s)本回合结束但【没有 armed 的自醒 wait 进程】= 掉自醒,新消息将事件不可达"
        "(曾有 agent 这样掉线 18h 致积压+重复分流)。请【立刻用后台 run_in_background】跑 "
        "`ab wait --role %s --timeout 0` 重挂自醒(0=自动日2h/夜6h idle·真消息仍即时唤醒)"
        "(绝不用 shell &),挂好再结束回合。若你确不需自醒(如一次性任务收尾),忽略本提示再次结束即可"
        "(%ds 内不再拦)。" % (role, role, GUARD_WINDOW)
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
