# -*- coding: utf-8 -*-
"""bus.roles —— 角色生命周期命令层（退役/复用/花名册）。register/route/caps/roster 后续增量并入。

退役 agent 档案馆：retired_roster.json（list 归档，与 agents.json 活跃在册分开）。
退役=从 agents.json 摘番号（selfwake/who/roster 不再显示、不占活跃名额）+ mailbox 移 _retired/ 留痕 + 记录进档案馆。
可复用：revive 从档案馆调回活跃在册。

依赖：config(BUS/_now)· io(_write_json/_audit)· roster(_load_agents/_save_agents)。无反向依赖=无环。
"""
import json, os

from bus.config import BUS, _now
from bus.io import _write_json, _audit, _fail
from bus.roster import _load_agents, _save_agents, update_agents, _retired_roles, _is_test_role
from bus.selfwake import _self_wake_state, _wait_proc_roles


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
    # 番号下掉:从 agents.json 移除(锁跨 RMW·P0:防与并发 hello/register 互相覆盖)
    update_agents(lambda ag: ag.pop(role, None))
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
    from bus.fleetlog import fleet_log
    fleet_log("retire", "%s 退役归档" % role, "接替=%s;原因:%s" % (entry["succeeded_by"] or "-", entry["reason"] or "-"))
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
        _fail("档案馆无 %s 的 archived 记录" % role)
    rec = roster[idx]
    def _mut(ag):
        ag[role] = {"registered": _now(), "desc": rec.get("desc", ""), "caps": rec.get("caps", []),
                    "revived_from_retired": rec.get("retired")}
    update_agents(_mut)   # 锁跨 RMW(P0)
    # mailbox 移回
    src = BUS / "_retired" / role
    if src.is_dir():
        try: os.replace(str(src), str(BUS / role))
        except Exception as e: print("  ⚠ mailbox 移回失败:%s" % e)
    rec["status"] = "revived"; rec["revived_at"] = _now()
    _write_json(_retired_roster_path(), roster)
    _audit("revive", role=role)
    from bus.fleetlog import fleet_log
    fleet_log("revive", "%s 复活回册" % role, "自档案馆调回(退役于 %s)" % rec.get("retired", "?"))
    print(json.dumps({"revived": role, "回活跃在册": role in _load_agents(), "状态": "revived"}, ensure_ascii=False, indent=1))

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


def cmd_route(args):
    """对口路由助手(强化专一性·防全科自接):按关键词在花名册 desc/caps 找【最对口】角色,
    推荐派给谁——命中多优先、在岗优先、退役/测试排除。派活前先 route 找对口,别自接跨领域活/别乱广播。"""
    terms = [t.lower() for t in (args.keywords or []) if t.strip()]
    if not terms:
        print("用法: ab route <关键词...>(按专长/能力找对口角色)"); return
    agents = _load_agents()
    retired = _retired_roles()
    wait_roles = _wait_proc_roles()
    scored = []
    for role, v in agents.items():
        if role in retired or _is_test_role(role) or not isinstance(v, dict):
            continue
        hay = (role + " " + (v.get("desc") or "") + " " + " ".join(v.get("caps", []))).lower()
        score = sum(hay.count(t) for t in terms)
        if score > 0:
            st = _self_wake_state(role, wait_roles=wait_roles)[0]
            scored.append((score, 0 if st == "armed" else 1, role, st, (v.get("desc") or "")[:58]))
    if not scored:
        print("无对口专责命中「%s」——考虑广播,或 ab register 新专家。" % " ".join(terms)); return
    scored.sort(key=lambda x: (-x[0], x[1]))   # 命中多优先、在岗优先
    mark = {"armed": "✓在岗", "recent": "◐待确认", "stale": "⚠掉线", "off": "—未醒", "parked": "🅿️休眠"}
    print("对口路由推荐(命中「%s」):" % " ".join(terms))
    for i, (sc, _o, role, st, desc) in enumerate(scored[:5]):
        tag = " ← 推荐(对口+在岗)" if i == 0 and st == "armed" else (" ← 最对口但掉线,先让它重挂" if i == 0 else "")
        print("  %-12s %s 命中%d  %s%s" % (role, mark.get(st, "?"), sc, desc, tag))
    print("→ 对口直派:ab send --to <角色> --from <你> ...(别自接跨领域、别乱广播)")


def _load_caps_pool():
    f = BUS / "capabilities.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("capabilities", {})
        except Exception:
            return {}
    return {}


def cmd_roster(args):
    """花名册:列每个 agent 的 在岗状态 / 能力(caps) / 专长(desc) / 注册时间。
    agents.json 是花名册真相源;在岗按自醒检测(armed=在岗 / stale=掉线 / off=未醒)。"""
    agents = _load_agents()
    kw = (getattr(args, "grep", None) or "").strip().lower()
    if kw:  # 路由助手:按关键词在 role/专长(desc)/caps 里筛"谁管这事"→ 直接派给它,不广播
        agents = {r: v for r, v in agents.items()
                  if not (isinstance(v, dict) and v.get("retired"))   # 退役不进路由(别把活派给退役的)
                  and (kw in r.lower() or kw in (v.get("desc", "") or "").lower()
                       or kw in " ".join(v.get("caps", [])).lower())}
    wait_roles = _wait_proc_roles()
    mark = {"armed": "✓在岗", "recent": "◐待确认", "stale": "⚠掉线", "off": "—未醒", "parked": "🅿️休眠"}
    hdr = ("花名册·匹配「%s」(%d):" % (kw, len(agents))) if kw else ("花名册(agents.json · %d 个角色):" % len(agents))
    print(hdr)
    print("-" * 78)
    if not agents:
        print("(无匹配——可能无此专责;考虑广播,或 register 新专家)"); return
    for role, v in agents.items():
        if isinstance(v, dict) and v.get("retired"):   # 退役:标注、不显示在岗状态(它本就不该在岗)
            sb = v.get("succeeded_by")
            print("● %-12s ⊘退役  retired=%s%s" % (role, v.get("retired"), ("  接替=" + sb if sb else "")))
            print("   专长(存档): %s" % ((v.get("desc", "") or "").strip()[:100]))
            continue
        st = _self_wake_state(role, wait_roles=wait_roles)[0]
        caps = "/".join(v.get("caps", [])) or "(无)"
        desc = (v.get("desc", "") or "(专长未填)").strip()
        print("● %-12s %s  reg=%s" % (role, mark.get(st, "?"), (v.get("registered", "") or "")[:10]))
        print("   能力: %s" % caps)
        print("   专长: %s" % desc[:100])
    print("-" * 78)
    print("把会话里训出的专家入册:ab register --role <名> --desc \"<专长/分管>\"(再按需 ab caps 配能力)")


def cmd_caps(args):
    """能力池查询:`caps` 看全池+谁持有;`caps --role X` 看某角色被分配的能力。"""
    pool = _load_caps_pool()
    agents = _load_agents()
    if args.role:
        caps = (agents.get(args.role) or {}).get("caps") or []
        print("【%s】的能力(caps,最小授权):" % args.role)
        if not caps:
            print("  (未分配 caps,或角色不存在)"); return
        for c in caps:
            print("  - %-16s %s" % (c, pool.get(c, {}).get("desc", "(未在能力池登记)")))
        return
    print("能力池(capabilities.json)+ 现持有者:")
    for name, info in pool.items():
        holders = [r for r, v in agents.items() if name in ((v or {}).get("caps") or [])]
        print("  %-16s %s" % (name, info.get("desc", "")))
        print("       默认给: %s" % info.get("default_for", "-"))
        print("       现持有(%d): %s" % (len(holders), ", ".join(holders) if holders else "无"))
