# -*- coding: utf-8 -*-
"""bus.status —— 状态视图:结构化 who 数据源(_who_data)+ 花名册 who + 队列 list。

_who_data = 权威状态源(核对 presence 心跳 vs 实际 claude 进程 + 自醒/park 判定），
cmd_who 表格化、WebUI 健康模块另吃(role-verdict 散点收敛的候选单一源）。

依赖：config(BUS/_now)· proc(_age_seconds/_alive_claude_pids)· roster(_list_roles/_retired_roles/
_pseudo_role_label/_load_agents)· presence(_load_pidtags)· edge(_edge_holder)· selfwake(_is_parked/_park_expired)· psutil。无反向依赖=无环。
"""
import json

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, _now
from bus.proc import _age_seconds, _alive_claude_pids
from bus.roster import _list_roles, _retired_roles, _pseudo_role_label, _load_agents
from bus.presence import _load_pidtags
from bus.edge import _edge_holder
from bus.selfwake import _is_parked, _park_expired


def _who_data():
    """结构化版 who:权威核对 presence 心跳 与 实际 claude 进程,给 WebUI 健康模块吃。
    返回 {as_of, psutil, roles:[...], alive_claude, claimed_pids, tagged, unclaimed_pids, edge_lock}。
    每个 role 记 registered/caps(花名册真相)+ present/mode/claude_pid/daemon_pid/alive/age_s/verdict/cwd/current_*。
    注:presence 只存 claude_pid+cwd,不存 session-jsonl id —— 会话↔角色的对齐由 WebUI 侧按 cwd 关联(同 cwd 多 agent 时标注歧义,不猜)。"""
    alive = _alive_claude_pids()
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
            "claude_pid": None, "daemon_pid": None, "alive": None, "age_s": None,
            "verdict": None, "cwd": None, "current_task": None, "current_title": None,
            "session_id": None,
        }
        plabel = _pseudo_role_label(r)
        if plabel:                    # 伪角色/系统桶:非执行体,不判在线/掉线,单列标注
            rec["pseudo"] = True; rec["verdict"] = "◇ " + plabel; out_roles.append(rec); continue
        if r in retired_set:
            rec["verdict"] = "⊘ 退役"; out_roles.append(rec); continue
        pk = _is_parked(r)
        if pk:   # 故意暂离(park/休眠)优先短路:与 selfwake 单一源一致,别把故意退出的会话当"崩溃残留"
            rec["parked"] = True
            ext = "·已到期待unpark" if _park_expired(pk) else ((("·%s天" % pk.get("days")) if pk.get("days") else ""))
            note = ("·" + pk["note"]) if pk.get("note") else ""
            rec["verdict"] = "🅿️ 休眠(用户故意暂离%s%s)" % (ext, note)
            out_roles.append(rec); continue
        pf = BUS / r / "presence.json"
        if not pf.exists():
            rec["verdict"] = "未声明(无 presence)"; out_roles.append(rec); continue
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            rec["verdict"] = "presence 损坏"; out_roles.append(rec); continue
        rec["present"] = True
        pid = pr.get("claude_pid"); rec["claude_pid"] = pid
        age = _age_seconds(pr.get("last_seen", "")); rec["age_s"] = age
        mode = pr.get("mode", "?"); rec["mode"] = mode
        rec["cwd"] = pr.get("cwd")
        rec["session_id"] = pr.get("session_id")   # 权威 session→role(取代 regex 猜)
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
        proc_alive = (pid in alive) if (pid and psutil is not None) else None
        rec["alive"] = proc_alive
        if pid:
            claimed.add(pid)
        if psutil is None:
            rec["verdict"] = "心跳%ss前(mode=%s)" % (age if age is not None else "?", mode)
        elif pid and not proc_alive:
            rec["verdict"] = "✗ 进程已退(残留)"
        elif mode == "listening" and proc_alive and listen_fresh:
            rec["verdict"] = "✓ worker在听"
        elif mode == "listening" and proc_alive:
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
    h = _edge_holder()
    return {
        "as_of": _now(), "psutil": psutil is not None, "roles": out_roles,
        "alive_claude": sorted(alive) if psutil is not None else [],
        "claimed_pids": sorted(claimed), "tagged": tagged, "unclaimed_pids": unknown,
        "edge_lock": ({"who": h.get("who"), "age_s": _age_seconds(h.get("acquired_at", ""))} if h else None),
    }


def cmd_who(args):
    """谁在跑 + 是谁:交叉核对 presence 心跳 与 实际 claude 进程。"""
    if getattr(args, "json", False):
        print(json.dumps(_who_data(), ensure_ascii=False)); return
    fresh = args.max_age
    alive = _alive_claude_pids()
    roles = _list_roles()
    retired_set = _retired_roles()
    claimed = set()
    print("%-14s %-22s %-9s %8s  %s" % ("role", "verdict", "claude_pid", "心跳age", "cwd"))
    print("-" * 88)
    for r in roles:
        plabel = _pseudo_role_label(r)
        if plabel:                  # 伪角色/系统桶:非执行体,不当掉线/未醒,单列标注
            print("%-14s %-22s  %s" % (r, "◇ 伪角色", plabel)); continue
        if r in retired_set:        # 退役:标注、不当残留/异常(进程退是应该的)
            print("%-14s %-22s" % (r, "⊘ 退役")); continue
        pk = _is_parked(r)          # 故意暂离(park/休眠):进程死是目的非崩溃,别显"残留"(与 _who_data/selfwake 单一源一致)
        if pk:
            ext = "·已到期待unpark" if _park_expired(pk) else ((("·%s天" % pk.get("days")) if pk.get("days") else ""))
            note = ("·" + pk["note"]) if pk.get("note") else ""
            print("%-14s %-22s %s" % (r, "🅿️ 休眠", "用户故意暂离%s%s" % (ext, note))); continue
        pf = BUS / r / "presence.json"
        if not pf.exists():
            print("%-14s %-22s" % (r, "未声明(无 presence)"))
            continue
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            print("%-14s %-22s" % (r, "presence 损坏")); continue
        pid = pr.get("claude_pid")
        age = _age_seconds(pr.get("last_seen", ""))
        mode = pr.get("mode", "?")
        wt = pr.get("wait_timeout") or 110
        listen_fresh = (age is not None and age <= wt + 60)
        if mode == "daemon":
            # 无可见 tab 的后台守护进程:按 daemon_pid 判活(不算进 claude 认领集)
            dpid = pr.get("daemon_pid")
            d_alive = psutil.pid_exists(dpid) if (dpid and psutil is not None) else None
            verdict = ("✓ daemon在听" if d_alive else ("✗ daemon已退" if dpid else "daemon?"))
            cur = pr.get("current_task")
            last = ("▶ 在做:%s" % (pr.get("current_title") or cur)) if cur else (pr.get("cwd") or "-")
            print("%-14s %-22s %-9s %7ss  %s" % (
                r, verdict, "d:%s" % (dpid or "?"), str(age if age is not None else "?"), last))
            continue
        proc_alive = (pid in alive) if (pid and psutil is not None) else None
        if pid:
            claimed.add(pid)
        if psutil is None:
            verdict = "心跳%ss前(mode=%s)" % (age if age is not None else "?", mode)
        elif pid and not proc_alive:
            verdict = "✗ 进程已退(残留)"
        elif mode == "listening" and proc_alive and listen_fresh:
            verdict = "✓ worker在听"
        elif mode == "listening" and proc_alive:
            verdict = "⚠ worker静默(超心跳%ss)" % (age if age is not None else "?")
        elif proc_alive:
            verdict = "已声明(hello),未起循环"
        else:
            verdict = "心跳%ss前(无pid)" % (age if age is not None else "?")
        cur = pr.get("current_task")
        last = (pr.get("cwd") or "-") + (("  ▶ 在做:%s" % (pr.get("current_title") or cur)) if cur else "")
        print("%-14s %-22s %-9s %7ss  %s" % (
            r, verdict, str(pid or "-"), str(age if age is not None else "?"), last))
    # 在跑但没认领的 claude 进程:区分"已标注(非fleet)"和"真未声明"
    if psutil is not None:
        tags = _load_pidtags()
        rest = sorted(alive - claimed)
        tagged = [(pid, tags[str(pid)]) for pid in rest if str(pid) in tags]
        unknown = [pid for pid in rest if str(pid) not in tags]
        print("-" * 88)
        if tagged:
            print("已标注(非fleet):", ", ".join(
                "%s=%s%s" % (pid, t.get("label", "?"), "🔒" if t.get("private") else "")
                for pid, t in tagged))
        print("在跑的 claude 进程: %d 个;fleet角色: %d;已标注: %d;未声明: %d %s" % (
            len(alive), len(claimed), len(tagged), len(unknown), unknown if unknown else ""))
    else:
        print("(psutil 不可用,无法核对进程;仅凭心跳)")
    h = _edge_holder()
    if h:
        age = _age_seconds(h.get("acquired_at", ""))
        print("Edge 锁: %s 持有(%ss%s)" % (h.get("who"), age, "·疑似陈旧" if (age and age > 300) else ""))


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
