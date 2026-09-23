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


# ── C′ 复活改造(2026-09-16·用户批案"支持你的建议"·Master数分 L级A 签字 1b8992)────────
# 当年 SHELVED 根因=psutil 扫进程有启动竞态+无法区分刚fire → 误拦活跃 agent(commit 483e820 摘除)。
# 复活判据改【读 wait 注册表双文件】(Master数分实测核过字段·先写先可见=竞态消除):
#   armed = wait.reg.json present + status==listening + wait_pid 活;
#   兜底 = wait_trace.json exit==null 且 pid 活(reg/trace 瞬时不同步时防误拦);
#   grace = trace.exited 距今 <FIRE_GRACE 秒(wait 刚 fire 退出·harness 唤醒窗口内)→放行。
# 灰度(L级B 前置·Master数分条件1):白名单增量灰度 ①MasterAgent(安全先行)→②+Master(Master数分 APPROVE
# 2026-09-17 b01e26:40/42掉线=忘重挂型·user-facing=代表性case;误拦率>5%或连环block→摘回)
# →③+Master数分(42次/3月最高·rapid churn)→④全员(白名单置 None)。一次只加一个 busy role=可归因。
GRAY_ROLES = {"MasterAgent", "Master"}  # 灰度②·L级B 验收(误拦率<5%/掉线趋0/无连环block)MasterAgent+Master两段合验
FIRE_GRACE = 15                    # 秒:wait 刚 fire 退出的唤醒窗口·放行防竞态误拦


def _read_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _pid_alive(pid):
    try:
        return bool(pid) and psutil is not None and psutil.pid_exists(int(pid))
    except Exception:
        return False


def _armed_state(role):
    """返回 (armed:bool, state:str)——按注册表判 armed(Master数分核过字段·非扫进程)。
    psutil 缺→保守当 armed(放行·fail-open)。"""
    if psutil is None:
        return True, "no-psutil-failopen"
    reg = _read_json(BUS / role / "wait.reg.json")
    if reg and reg.get("status") == "listening" and _pid_alive(reg.get("wait_pid")):
        return True, "reg-listening"
    trace = _read_json(BUS / role / "wait_trace.json")
    if trace:
        if trace.get("exit") is None and _pid_alive(trace.get("pid")):
            return True, "trace-running"          # reg 瞬时缺但 wait 真在跑=防误拦兜底
        exited = trace.get("exited")
        try:
            t = time.mktime(time.strptime(str(exited)[:19], "%Y-%m-%dT%H:%M:%S")) if exited else None
        except Exception:
            t = None
        if t and (time.time() - t) < FIRE_GRACE:
            return True, "fire-grace"             # 刚 fire 的唤醒窗口·放行
    return False, "no-armed(reg=%s)" % ("absent" if not reg else reg.get("status"))


def _audit_block(role, state):
    """留痕(Master数分实证方要求②·非静默):append 一行到 events.jsonl(同 _audit 格式·不 import bus 保轻量)。"""
    try:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "enforce_selfwake_block",
               "role": role, "state": state}
        with open(str(BUS / "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
    if os.environ.get("AGENTBUS_DISPATCH"):
        return  # dispatch/daemon 一次性 claude -p 子会话(claude_run 标 env)·不自醒·不拦(Master数分 708a33 闸①(2))
    role = _resolve_role(data.get("session_id"))
    if not role or role in ("用户", "unknown"):
        return  # 个人会话/伪角色
    if GRAY_ROLES is not None and role not in GRAY_ROLES:
        return  # 灰度白名单外·放行(二段灰度=Master数分 L级B 条件1)
    armed, state = _armed_state(role)
    if not armed:
        # ★宽限重查(灰度首日误拦实证 2026-09-17 09:51:22:agent 回合末刚 run_in_background 挂 wait·
        # bash壳→python→写reg 需 ~1-2s·hook 查早了=启动窗口竞态残余)。sleep 2s 二查·仍 absent 才 block。
        time.sleep(2.0)
        armed, state = _armed_state(role)
    if armed:
        return  # 已 armed(注册表判据)
    if _recently_blocked(role):
        return  # 防死循环:刚 block 过还没 armed 上,放行别卡
    _mark_blocked(role)
    _audit_block(role, state)
    reason = (
        "⚠ Stop-hook(C′·掉线82%%=忘重挂的机制化根治):你(%s)本回合结束但【没有 armed 的自醒 wait】"
        "(判据=%s)= 掉自醒·新消息将事件不可达。请【立刻用后台 run_in_background】跑 "
        "`ab wait --role %s --timeout 0` 重挂(绝不用 shell &·already_armed 幂等不叠),挂好再结束回合。"
        "确不需自醒(一次性收尾)则忽略本提示再次结束即可(%ds 内不再拦)。" % (role, state, role, GUARD_WINDOW)
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
