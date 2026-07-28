# -*- coding: utf-8 -*-
"""bus.fleetlog —— 舰队日志 + 维修模式(用户 2026-07-28:「档案馆那块一直没弄好,想做个舰队日志」)。

## 舰队日志(fleet_log.jsonl·append-only)
bus 的【编年史】:大事件 / 维修窗口 / 退役·复活 / 结业 / 里程碑,一条一行、只追加不改写。
与三个既有 store 的分工:
  events.jsonl   = 机器审计(send/claim/done 高频流水,会轮转);
  retired_roster = 退役者【档案卡】(who/caps/贡献,按人索引);
  fleet_log      = 【时间线叙事】(按事件索引·人读的史册·永不轮转)——档案馆的"编年史"补面。
自动记录点:retire / revive / train-done / maint on·off 挂钩自动落一条;人工大事用 `ab fleetlog --add`。

## 维修模式(maintenance.json)
用户定性(bus 史第三次大事件·2026-07-28):维修态=全 bus 停机、依次开启 agent 维护+状态修正。
`ab maint --on` 期间:selfwake-guard【休眠】(停机 agent 大片"掉线"是预期,不报警不 toast 不投提醒);
who/selfwake 显 🔧 横幅。`ab maint --off` 结束,自动记舰队日志。
"""
import json, os, sys, time

from bus.config import BUS, _now

FLEET_LOG = BUS / "fleet_log.jsonl"
MAINT = BUS / "maintenance.json"

_KINDS = ("milestone", "incident", "maintenance", "retire", "revive", "train", "note")


def fleet_log(kind, title, detail="", actor=None, refs=None, ts=None):
    """追加一条舰队日志(O_APPEND 单写,best-effort 绝不影响主流程)。kind 见 _KINDS。"""
    try:
        rec = {"ts": ts or _now(), "kind": kind if kind in _KINDS else "note",
               "title": (title or "")[:120], "detail": (detail or "")[:2000],
               "actor": actor or os.environ.get("AGENTBUS_ROLE") or "unknown"}
        if refs:
            rec["refs"] = refs
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(FLEET_LOG), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        pass


def _read_log(limit=0, kind=None):
    if not FLEET_LOG.exists():
        return []
    out = []
    try:
        for ln in FLEET_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if kind and r.get("kind") != kind:
                continue
            out.append(r)
    except Exception:
        pass
    return out[-limit:] if limit else out


def maint_active():
    """维修模式是否开启。读不出/文件坏 → False(fail-open 不误休眠守卫)。"""
    try:
        return bool(json.loads(MAINT.read_text(encoding="utf-8")).get("active"))
    except Exception:
        return False


def maint_info():
    try:
        return json.loads(MAINT.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------- 舰队状态判定(用户令 2026-07-28:UI 顶部显示·预设条件可配置)----------------
_RULES = BUS / "fleet_state_rules.json"
_DEFAULT_STATES = [
    {"state": "维修中", "icon": "🔧", "when": "maintenance"},
    {"state": "严重掉线", "icon": "🔴", "when": "stale_count>=3"},
    {"state": "部分掉线", "icon": "🟠", "when": "stale_count>=1"},
    {"state": "正常", "icon": "🟢", "when": "default"},
]


def fleet_state():
    """舰队总体状态(单一源·CLI 与 WebUI 顶栏共用):按 fleet_state_rules.json 预设条件
    顺序评估,首条命中即状态。返回 {state, icon, desc, guard, counts, maint, rule}。
    counts 口径与 selfwake 一致(排除 伪角色/退役/parked/测试);guard=自醒守卫状态
    (维修模式=休眠,否则=值守)。规则文件坏/缺 → 内置默认(降级仍可判)。"""
    from bus.roster import _list_roles, _retired_roles, _pseudo_role_label, _is_test_role
    from bus.selfwake import _self_wake_state, _wait_proc_roles, _is_parked
    try:
        states = json.loads(_RULES.read_text(encoding="utf-8")).get("states") or _DEFAULT_STATES
    except Exception:
        states = _DEFAULT_STATES
    wait_roles = _wait_proc_roles()
    retired = _retired_roles()
    counts = {"armed": 0, "recent": 0, "stale": 0, "off": 0, "parked": 0, "expected": 0}
    stale_roles = []
    for role in _list_roles():
        if _pseudo_role_label(role) or role in retired or _is_test_role(role):
            continue
        if _is_parked(role):
            counts["parked"] += 1
            continue
        counts["expected"] += 1
        st = _self_wake_state(role, wait_roles=wait_roles)[0]
        counts[st] = counts.get(st, 0) + 1
        if st == "stale":
            stale_roles.append(role)
    stale = counts["stale"]
    ratio = (stale / counts["expected"]) if counts["expected"] else 0.0
    m_on = maint_active()

    def _hit(when):
        if when == "maintenance":
            return m_on
        if when == "default":
            return True
        if when.startswith("stale_count>="):
            try:
                return stale >= int(when.split(">=", 1)[1])
            except Exception:
                return False
        if when.startswith("stale_ratio>="):
            try:
                return ratio >= float(when.split(">=", 1)[1])
            except Exception:
                return False
        return False

    chosen = next((r for r in states if _hit(r.get("when", ""))), _DEFAULT_STATES[-1])
    out = {"state": chosen.get("state"), "icon": chosen.get("icon", ""), "desc": chosen.get("desc", ""),
           "rule": chosen.get("when"), "guard": "休眠(维修模式)" if m_on else "值守中",
           "counts": counts, "stale_roles": stale_roles}
    if m_on:
        out["maint"] = maint_info()
    return out


def cmd_fleet_state(args):
    """舰队状态一行判定(预设条件见 fleet_state_rules.json;--json 给 WebUI/脚本)。"""
    st = fleet_state()
    if getattr(args, "json", False):
        print(json.dumps(st, ensure_ascii=False)); return
    c = st["counts"]
    print("%s 舰队状态:%s(规则 %s)· 守卫:%s" % (st["icon"], st["state"], st["rule"], st["guard"]))
    print("   在岗%d/应在岗%d · 待确认%d · 掉线%d%s · 休眠%d" % (
        c["armed"], c["expected"], c.get("recent", 0), c["stale"],
        ("(" + "、".join(st["stale_roles"]) + ")") if st["stale_roles"] else "", c["parked"]))
    if st.get("maint"):
        print("   🔧 维修:since %s·%s" % (st["maint"].get("since"), st["maint"].get("note") or "-"))


_MARK = {"milestone": "🏁", "incident": "🔥", "maintenance": "🔧", "retire": "⊘",
         "revive": "♻", "train": "🎓", "note": "·"}


def cmd_fleetlog(args):
    """舰队日志:默认列最近 N 条;--add 追加一条(--kind/--title/--detail/--ts 补记历史)。"""
    if getattr(args, "add", False):
        if not args.title:
            print("--add 需要 --title"); sys.exit(1)
        fleet_log(args.kind or "note", args.title, args.detail or "",
                  actor=args.by, ts=args.ts or None)
        print("✓ 舰队日志 +1(%s)%s" % (args.kind or "note", args.title))
        return
    rows = _read_log(limit=args.limit or 20, kind=args.kind or None)
    if not rows:
        print("(舰队日志为空 —— ab fleetlog --add --kind milestone --title ... 记第一条)"); return
    print("═" * 78)
    print("舰队日志(最近 %d 条%s)· fleet_log.jsonl append-only 编年史" % (len(rows), "·kind=" + args.kind if args.kind else ""))
    print("═" * 78)
    for r in rows:
        print("%s %s [%s] %s" % (r.get("ts", "?")[:16], _MARK.get(r.get("kind"), "·"), r.get("kind"), r.get("title")))
        if r.get("detail"):
            for dl in r["detail"].splitlines():
                print("     %s" % dl)
        print("     —— %s" % r.get("actor", "?"))
    print("═" * 78)


def cmd_maint(args):
    """维修模式开关/状态。on 期间 selfwake-guard 休眠(停机 agent 大片掉线=预期不报警)。"""
    by = args.by or os.environ.get("AGENTBUS_ROLE") or "user"
    if getattr(args, "on", False):
        rec = {"active": True, "since": _now(), "by": by, "note": args.note or ""}
        MAINT.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        fleet_log("maintenance", "维修模式开启", args.note or "", actor=by)
        print("🔧 维修模式【开】(by %s%s)。selfwake-guard 已休眠——停机 agent 不再被报警/投提醒;"
              "who/selfwake 显 🔧 横幅。结束跑 `ab maint --off`。" % (by, ("·" + args.note) if args.note else ""))
        return
    if getattr(args, "off", False):
        info = maint_info()
        try:
            MAINT.unlink()
        except FileNotFoundError:
            print("(维修模式本就未开启)"); return
        except Exception as e:
            print("关闭失败:%s" % e); return
        fleet_log("maintenance", "维修模式结束", "自 %s(%s)" % (info.get("since", "?"), info.get("note", "")), actor=by)
        print("✓ 维修模式【关】(开启于 %s)。selfwake-guard 恢复巡检。" % info.get("since", "?"))
        return
    if maint_active():
        i = maint_info()
        print("🔧 维修模式【进行中】 since=%s by=%s note=%s" % (i.get("since"), i.get("by"), i.get("note") or "-"))
    else:
        print("维修模式:未开启(ab maint --on [--note ...] 开)")
