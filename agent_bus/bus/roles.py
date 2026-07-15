# -*- coding: utf-8 -*-
"""bus.roles —— 角色生命周期命令层（退役/复用/花名册）。register/route/caps/roster 后续增量并入。

退役 agent 档案馆：retired_roster.json（list 归档，与 agents.json 活跃在册分开）。
退役=从 agents.json 摘番号（selfwake/who/roster 不再显示、不占活跃名额）+ mailbox 移 _retired/ 留痕 + 记录进档案馆。
可复用：revive 从档案馆调回活跃在册。

依赖：config(BUS/_now)· io(_write_json/_audit)· roster(_load_agents/_save_agents)。无反向依赖=无环。
"""
import json, os

from bus.config import BUS, _now
from bus.io import _write_json, _audit
from bus.roster import _load_agents, _save_agents


def _retired_roster_path():
    return BUS / "retired_roster.json"

def _load_retired_roster():
    p = _retired_roster_path()
    try:
        r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        return r if isinstance(r, list) else []
    except Exception:
        return []

def cmd_retire(args):
    """把一个活跃 role 归档进退役档案馆:① 记录存 retired_roster.json(role/会话ids/起退时间/贡献记忆指针/
    退役原因/接替者/状态)② 从 agents.json 摘除番号(下掉、不占活跃名额、可复用)③ mailbox 移 _retired/ 留痕。
    会话 jsonl 本身不动(只记 id)。"""
    role = args.role
    a = _load_agents()
    rec_src = a.get(role, {}) if isinstance(a.get(role), dict) else {}
    entry = {
        "role": role,
        "session_ids": [s.strip() for s in (args.session_ids or "").split(",") if s.strip()],
        "registered": rec_src.get("registered", ""),
        "retired": rec_src.get("retired") or _now()[:10],
        "succeeded_by": args.succeeded_by or rec_src.get("succeeded_by"),
        "reason": args.reason or "",
        "contribution_memory": args.memory or "",
        "desc": rec_src.get("desc", ""),
        "caps": rec_src.get("caps", []),
        "status": "archived",
        "archived_at": _now(),
    }
    roster = _load_retired_roster()
    roster.append(entry)
    _write_json(_retired_roster_path(), roster)
    # 番号下掉:从 agents.json 移除
    if role in a:
        del a[role]; _save_agents(a)
    # mailbox 移 _retired/ 留痕(_retired 前缀→_list_roles 不扫,从活跃视图消失)
    src = BUS / role
    moved = False
    if src.is_dir():
        dst_parent = BUS / "_retired"; dst_parent.mkdir(exist_ok=True)
        dst = dst_parent / role
        try:
            if dst.exists():
                import shutil as _sh; _sh.rmtree(dst, ignore_errors=True)
            os.replace(str(src), str(dst)); moved = True
        except Exception as e:
            print("  ⚠ mailbox 移动失败(已记档+下掉番号):%s" % e)
    # 从 fleet 移除(若在)
    try:
        f = json.loads((BUS / "fleet.json").read_text(encoding="utf-8"))
        n0 = len(f.get("daemons", []))
        f["daemons"] = [d for d in f.get("daemons", []) if d.get("role") != role]
        if len(f["daemons"]) != n0:
            _write_json(BUS / "fleet.json", f)
    except Exception:
        pass
    _audit("retire", role=role, succeeded_by=entry["succeeded_by"], reason=entry["reason"][:60])
    print(json.dumps({"retired": role, "番号下掉": role not in _load_agents(),
                      "mailbox移_retired": moved, "档案馆": str(_retired_roster_path()),
                      "session_ids": entry["session_ids"], "status": "archived·可复用"}, ensure_ascii=False, indent=1))

def cmd_revive(args):
    """可复用回路:从退役档案馆把番号调回 agents.json 活跃在册(恢复 desc/caps)+ mailbox 移回 + 标记 revived。"""
    role = args.role
    roster = _load_retired_roster()
    idx = None
    for i in range(len(roster) - 1, -1, -1):   # 取最近一条 archived
        if roster[i].get("role") == role and roster[i].get("status") == "archived":
            idx = i; break
    if idx is None:
        print(json.dumps({"error": "档案馆无 %s 的 archived 记录" % role}, ensure_ascii=False)); return
    rec = roster[idx]
    a = _load_agents()
    a[role] = {"registered": _now(), "desc": rec.get("desc", ""), "caps": rec.get("caps", []),
               "revived_from_retired": rec.get("retired")}
    _save_agents(a)
    # mailbox 移回
    src = BUS / "_retired" / role
    if src.is_dir():
        try: os.replace(str(src), str(BUS / role))
        except Exception as e: print("  ⚠ mailbox 移回失败:%s" % e)
    rec["status"] = "revived"; rec["revived_at"] = _now()
    _write_json(_retired_roster_path(), roster)
    _audit("revive", role=role)
    print(json.dumps({"revived": role, "回活跃在册": role in a, "状态": "revived"}, ensure_ascii=False, indent=1))

def cmd_retired(args):
    """列退役档案馆(花名册):番号/退役时间/接替者/原因/会话ids/贡献记忆/状态。"""
    roster = _load_retired_roster()
    if not roster:
        print("退役档案馆为空。"); return
    print("退役 agent 档案馆(%d 条):" % len(roster))
    print("-" * 78)
    for r in roster:
        print("⊘ %-14s 退役%s  接替=%s  状态=%s" % (
            r.get("role"), r.get("retired", ""), r.get("succeeded_by") or "-", r.get("status", "")))
        print("   原因: %s" % (r.get("reason", "") or "-")[:90])
        print("   会话ids: %s" % (", ".join(r.get("session_ids", [])) or "-"))
        print("   贡献记忆: %s | 专长: %s" % (r.get("contribution_memory", "") or "-", (r.get("desc", "") or "-")[:50]))
    print("-" * 78)
    print("复用某番号:ab revive --role <名>(从档案馆调回 agents.json 活跃在册)")
