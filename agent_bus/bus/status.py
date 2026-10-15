# -*- coding: utf-8 -*-
"""bus.status —— 状态视图:结构化 who 数据源(_who_data)+ 花名册 who + 队列 list。

_who_data = 权威状态源(核对 presence 心跳 vs 实际 claude 进程 + 自醒/park 判定），
cmd_who 表格化、WebUI 健康模块另吃(role-verdict 散点收敛的候选单一源）。

依赖：config(BUS/_now)· proc(_age_seconds/_alive_claude_pids)· roster(_list_roles/_retired_roles/
_pseudo_role_label/_load_agents)· presence(_load_pidtags)· lock(holders)· selfwake(_is_parked/_park_expired)· psutil。无反向依赖=无环。
"""
import json

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, _now
from bus.proc import _age_seconds, _alive_claude_pids, _host_pid_alive
from bus.roster import _list_roles, _retired_roles, _pseudo_role_label, _load_agents
from bus.presence import _load_pidtags
from bus.lock import holders as _lock_holders   # 分离 A4:通用 locks[](edge_lock 旧字段已摘·MasterUI 1ffac70 切 d.locks)
from bus.selfwake import _self_wake_state, _wait_proc_roles, _turn_busy, _inbox_backlog, _orphan_claims


def _who_data():
    """结构化版 who:权威核对 presence 心跳 与 实际 claude 进程,给 WebUI 健康模块吃。
    返回 {as_of, psutil, roles:[...], alive_claude, claimed_pids, tagged, unclaimed_pids, locks}。
    每个 role 记 registered/caps(花名册真相)+ present/mode/claude_pid/daemon_pid/alive/age_s/verdict/cwd/current_*。
    注:presence 只存 claude_pid+cwd,不存 session-jsonl id —— 会话↔角色的对齐由 WebUI 侧按 cwd 关联(同 cwd 多 agent 时标注歧义,不猜)。"""
    alive = _alive_claude_pids()
    wait_roles = _wait_proc_roles()   # 2a 步骤2:预扫一次·供 _self_wake_state 判自醒态(免每 role 全扫)
    roles = _list_roles()
    retired_set = _retired_roles()
    try:
        agents = _load_agents()
    except Exception:
        agents = {}
    claimed = set()
    out_roles = []
    for r in roles:
        ent = agents.get(r) if isinstance(agents, dict) else None
        rec = {
            "role": r,
            "registered": ent.get("registered") if isinstance(ent, dict) else None,
            "caps": (ent.get("caps") if isinstance(ent, dict) else None) or [],
            "retired": r in retired_set, "present": False, "mode": None,
            "claude_pid": None, "codex_pid": None, "host_pid": None, "host_kind": None,
            "daemon_pid": None, "alive": None, "age_s": None,
            "verdict": None, "cwd": None, "current_task": None, "current_title": None,
            "session_id": None, "host_session_id": None,
        }
        plabel = _pseudo_role_label(r)
        if plabel:                    # 伪角色/系统桶:非执行体,不判在线/掉线,单列标注
            rec["pseudo"] = True; rec["verdict"] = "◇ " + plabel; out_roles.append(rec); continue
        if r in retired_set:
            rec["verdict"] = "⊘ 退役"; out_roles.append(rec); continue
        sw_state, sw_detail = _self_wake_state(r, wait_roles=wait_roles)
        rec["self_wake"] = sw_state    # 2a 步骤2:自醒态单一源=_self_wake_state(消 _who_data 自查 parked 重复;polling 等态供消费者读此字段)
        if sw_state == "parked":       # 故意暂离:短路(与 selfwake 同串·别当崩溃残留;parked pid 已死·不影响 claimed 计数)
            rec["parked"] = True; rec["verdict"] = sw_detail; out_roles.append(rec); continue
        # polling(guard_exempt)【不短路】:进程活着·照常走 presence 流程被 claimed(否则从 fleet 挤成"未声明"=count 漂移)
        # →presence verdict 保原样(✓worker在听 等),polling 区分只放 self_wake 字段,消费者按需显 ⏱
        pf = BUS / r / "presence.json"
        if not pf.exists():
            rec["verdict"] = "未声明(无 presence)"; out_roles.append(rec); continue
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            rec["verdict"] = "presence 损坏"; out_roles.append(rec); continue
        rec["present"] = True
        pid = pr.get("host_pid") or pr.get("claude_pid") or pr.get("codex_pid")
        host_kind = pr.get("host_kind") or ("claude" if pr.get("claude_pid") else ("codex" if pr.get("codex_pid") else None))
        rec["claude_pid"] = pr.get("claude_pid")
        rec["codex_pid"] = pr.get("codex_pid")
        rec["host_pid"] = pid
        rec["host_kind"] = host_kind
        age = _age_seconds(pr.get("last_seen", "")); rec["age_s"] = age
        mode = pr.get("mode", "?"); rec["mode"] = mode
        rec["cwd"] = pr.get("cwd")
        rec["session_id"] = pr.get("session_id")   # 权威 session→role(取代 regex 猜)
        rec["host_session_id"] = pr.get("host_session_id") if host_kind == "codex" else None
        rec["current_task"] = pr.get("current_task")
        rec["current_title"] = pr.get("current_title")
        wt = pr.get("wait_timeout") or 110
        listen_fresh = (age is not None and age <= wt + 60)
        if mode == "daemon":
            dpid = pr.get("daemon_pid"); rec["daemon_pid"] = dpid
            d_alive = psutil.pid_exists(dpid) if (dpid and psutil is not None) else None
            rec["alive"] = d_alive
            rec["verdict"] = ("✓ daemon在听" if d_alive else ("✗ daemon已退" if dpid else "daemon?"))
            out_roles.append(rec); continue
        proc_alive = _host_pid_alive(pid, host_kind) if (pid and psutil is not None) else None
        rec["alive"] = proc_alive
        if pid and host_kind == "claude":
            claimed.add(pid)
        if psutil is None:
            rec["verdict"] = "心跳%ss前(mode=%s)" % (age if age is not None else "?", mode)
        elif pid and not proc_alive:
            rec["verdict"] = "✗ 进程已退(残留)"
        elif mode == "listening" and proc_alive and listen_fresh:
            rec["verdict"] = "✓ worker在听"
        elif mode == "listening" and proc_alive:
            # 交叉 turn_state(2026-09-21 用户令'数字记录有问题'):turn 活跃(tool/thinking·ts<15min)
            # =在忙非静默(长任务期心跳晚是正常态·同 selfwake busy 修法);误记静默会虚报 tray 静默数
            # (实证:培训师 turn=thinking 在做任务·被记'worker静默'→球上静默1=假)。
            busy = _turn_busy(r)
            if busy:
                dn, dh = _inbox_backlog(r)      # 忙而聋(2026-09-24):turn 活跃但 inbox 积压=协作断线·球/面板要看得见
                do = len(_orphan_claims(r))     # ④悬单:processing 里 claim 者已亡
                tag = ("·⚠聋(inbox积压%d·最老%sh·无wait)" % (dn, dh) if dn else "") + ("·⚠悬单%d(claimed后无turn)" % do if do else "")
                rec["verdict"] = ("🔧 干活中" + tag) if tag else ("🔧 干活中(%s·长任务心跳晚=正常)" % busy)
            else:
                rec["verdict"] = "⚠ worker静默(超心跳%ss)" % (age if age is not None else "?")
        elif proc_alive:
            rec["verdict"] = "已声明(hello),未起循环"
        else:
            rec["verdict"] = "心跳%ss前(无pid)" % (age if age is not None else "?")
        out_roles.append(rec)
    tags = _load_pidtags() if psutil is not None else {}
    rest = sorted(alive - claimed) if psutil is not None else []
    tagged = [{"pid": pid, "label": tags[str(pid)].get("label"), "private": bool(tags[str(pid)].get("private"))}
              for pid in rest if str(pid) in tags]
    unknown = [pid for pid in rest if str(pid) not in tags]
    return {
        "as_of": _now(), "psutil": psutil is not None, "roles": out_roles,
        "alive_claude": sorted(alive) if psutil is not None else [],
        "claimed_pids": sorted(claimed), "tagged": tagged, "unclaimed_pids": unknown,
        "locks": _lock_holders(),                     # A4:[{resource, who, acquired_at, age_s}](含 edge·通用资源互斥)
    }


def cmd_who(args):
    """谁在跑 + 是谁:交叉核对 presence 心跳 与 实际 claude 进程。
    表格与 --json 同源 `_who_data()`(2a 收敛:原表格自算 verdict=#2 分叉,已并入 _who_data 单一源)。"""
    d = _who_data()
    if getattr(args, "json", False):
        print(json.dumps(d, ensure_ascii=False)); return
    from bus.fleetlog import maint_active, maint_info
    if maint_active():
        _mi = maint_info()
        print("🔧 维修模式进行中(since %s·%s)——下表大片『进程已退』为预期停机态,非故障。" % (_mi.get("since", "?"), _mi.get("note") or "-"))
    # 自醒轴符号(Master数分 P2b 验收建议 2026-08-10:who 表格 surface self_wake,免使用者把 verdict"在听"
    # 误读成"已武装"——verdict 是【worker 健康轴】,self_wake 是【自醒轴】,两轴正交、都要可见)。
    _SW = {"armed": "✓", "recent": "◐", "stale": "⚠", "off": "–", "parked": "🅿", "polling": "⏱"}
    # 自醒列【前置到 verdict 前】(Master数分 P2b 验收揪出:verdict 非固定宽,伪角色/parked 的长描述会撑破
    # 列宽把自醒符号挤到行尾错位)。自醒符号短固定 → 放 verdict 前永不被撑歪;verdict 放最后可变长
    # 列(present 行后接 pid/age/cwd 用 %-22s 对齐;短行 verdict 用 %s 收尾长文本不挤任何列)。
    print("%-14s %-4s %-22s %-14s %8s  %s" % ("role", "自醒", "verdict(健康)", "host(kind:pid)", "心跳age", "cwd"))
    print("-" * 90)
    for rec in d["roles"]:
        r = rec["role"]; verdict = rec.get("verdict") or "?"
        sw = _SW.get(rec.get("self_wake") or "", "·")
        age = rec.get("age_s"); age_s = str(age if age is not None else "?")
        cur = rec.get("current_task")
        if not rec.get("present"):          # 伪角色/退役/休眠/未声明/损坏:无进程·短行(verdict 收尾·长文本不挤列)
            print("%-14s %-4s %s" % (r, sw, verdict)); continue
        piddisp = ("d:%s" % (rec.get("daemon_pid") or "?")) if rec.get("mode") == "daemon" else (
            "%s:%s" % (rec.get("host_kind"), rec.get("host_pid")) if rec.get("host_pid") else "-")
        last = (rec.get("cwd") or "-") + (("  ▶ 在做:%s" % (rec.get("current_title") or cur)) if cur else "")
        print("%-14s %-4s %-22s %-14s %7ss  %s" % (r, sw, verdict, piddisp, age_s, last))
    print("-" * 96)
    if d["psutil"]:
        tagged = d.get("tagged", [])
        if tagged:
            print("已标注(非fleet):", ", ".join(
                "%s=%s%s" % (t["pid"], t.get("label", "?"), "🔒" if t.get("private") else "")
                for t in tagged))
        uc = d.get("unclaimed_pids", [])
        print("在跑的 claude 进程: %d 个;fleet角色: %d;已标注: %d;未声明: %d %s" % (
            len(d["alive_claude"]), len(d["claimed_pids"]), len(tagged), len(uc), uc if uc else ""))
    else:
        print("(psutil 不可用,无法核对进程;仅凭心跳)")
    for lk in (d.get("locks") or []):
        a = lk.get("age_s")
        print("%s 锁: %s 持有(%ss%s)" % ("Edge" if lk.get("resource") == "edge" else "资源 " + str(lk.get("resource")), lk.get("who"), a, "·疑似陈旧" if (a and a > 300) else ""))


def cmd_list(args):
    a = _load_agents()
    roles = sorted(set(list(a.keys()) + _list_roles()))
    if not roles:
        print("(no roles registered)"); return
    print("%-16s %6s %10s %5s" % ("role", "inbox", "processing", "done"))
    for r in roles:
        d = BUS / r
        ib = len(list((d / "inbox").glob("*.json"))) if (d / "inbox").exists() else 0
        pr = len(list((d / "processing").glob("*.json"))) if (d / "processing").exists() else 0
        dn = len(list((d / "done").glob("*.json"))) if (d / "done").exists() else 0
        print("%-16s %6d %10d %5d" % (r, ib, pr, dn))
