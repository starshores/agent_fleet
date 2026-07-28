# -*- coding: utf-8 -*-
"""bus.refs —— BUS 2.0 P2-2b:三 store 跨引用【契约化】(使用方签 B=分层保留+契约化 ref,否 A 归一)。

三 store 各留各的(不合并),跨 store 只准通过下面【声明的 ref 字段】关联;
ref 存【标识符】、不存对方 payload 副本(禁内联=防偷偷 A 归一)。本模块=契约的单一真相源 + 只读校验器。
纯 additive:不改任何现有解析代码,故"解析结果不变"天然满足(验收铁律·使用方 2026-07-17 口径)。

三 store 边界:
- todos(todos.json)        = ask-user 待办池(用户面待拍板·非阻塞)
- tasklines(role 目录)     = agent 间任务(inbox/processing/done/archive 生命周期)
- longrange(_longrange/…)  = 长程多根 DAG 生长网(拆解/执行/跟踪 + 里程碑闸 + 决策合批)

依赖:config(BUS)· roster(_list_roles)。只读·best-effort·绝不改 store。无反向依赖=无环。
"""
import json, os

from bus.config import BUS
from bus.io import _write_json, json_lock
from bus.roster import _list_roles, _retired_roles

# 每条 ref 契约锁 4 要素:id 空间 / 基数 / 悬挂处理 / 解析目标(契约化只显式化·禁改现解析)。
# owner=校验归属(bus=本模块可 disk 校验 / longrange_net=引擎侧深校验,本模块只声明)。
REF_CONTRACTS = [
    # ── todos store ──
    {"n": 1, "store": "todos", "ref": "todo.from", "id_space": "bus role", "card": "1:1",
     "dangling": "非 bus 成员 ab ask-user 已硬拒(_is_bus_member)", "means": "哪个 agent 在等这个决策/答案回填给谁", "owner": "bus"},
    {"n": 2, "store": "todos", "ref": "todo.answer/resolution", "id_space": "用户回填(WebUI POST)", "card": "1:1",
     "dangling": "未答默认不阻塞(非阻塞池)", "means": "用户对该待办的答复", "owner": "bus"},
    # ── tasklines store ──
    {"n": 3, "store": "tasklines", "ref": "task.from / task.to", "id_space": "bus role", "card": "N:1",
     "dangling": "from=unknown 时 done --to 兜底", "means": "任务路由:谁派给谁", "owner": "bus"},
    {"n": 4, "store": "tasklines", "ref": "notice.notice_of", "id_space": "task-id", "card": "1:1",
     "dangling": "原任务已归档时读 <sender>/done/<id> 兜底", "means": "完成通知指向它通告的任务·读完即弃防环", "owner": "bus"},
    {"n": 5, "store": "tasklines", "ref": "done -> archive/<同名>", "id_space": "task-id", "card": "1:1",
     "dangling": "归档不改 id", "means": "完成任务归档留痕·id 保持", "owner": "bus"},
    {"n": 6, "store": "tasklines", "ref": "presence.current_task", "id_space": "task-id", "card": "1:1",
     "dangling": "完成后清 None", "means": "该 role 当前在做的任务", "owner": "bus"},
    # ── longrange store(引擎侧深校验·本模块只声明契约)──
    {"n": 7, "store": "longrange", "ref": "node -> taskline task", "id_space": "task-id(跨 store!)", "card": "1:1",
     "dangling": "任务归档→节点标记", "means": "生长网节点挂到一个真实任务", "owner": "longrange_net"},
    {"n": 8, "store": "longrange", "ref": "edge -> node<->node", "id_space": "node-id", "card": "N:N",
     "dangling": "含分叉合并+跨任务边", "means": "边遍历边长·不动点收敛", "owner": "longrange_net"},
    {"n": 9, "store": "longrange", "ref": "milestone -> 覆盖节点", "id_space": "node-id", "card": "1:N",
     "dangling": "里程碑通过才放行", "means": "验收闸", "owner": "longrange_net"},
    {"n": 10, "store": "longrange", "ref": "resolve_batch -> 一批 task/milestone", "id_space": "task/milestone-id", "card": "1:N",
     "dangling": "approve->ready/reject->failed/未答默认 reject", "means": "决策合批·一次批完", "owner": "longrange_net"},
]


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def _is_smoke_from(frm):
    """测试污染 from(__smoke__/__smoke2__/__smokegrp__…):bus_smoke 残留·零业务价值·可 auto-purge。"""
    return bool(frm) and frm.startswith("__smoke")


def check_refs():
    """只读校验 bus-native ref(悬挂检测):todo.from / task.from-to / notice.notice_of / presence.current_task
    的目标是否可解析。longrange 内部 ref(node/edge/milestone/batch)归 longrange_net 引擎深校验,此处不展开。
    返回 {checked, dangling, accepted, purgeable_smoke, review, notes}。best-effort·纯读·不改任何 store。

    使用方 3 类口径(2026-07-17):①smoke 测试污染=可 auto-purge;②退役 role 历史条目=契约可接受(补 retired_roster
    即 accepted·保留留痕);③真·未知 from=需人核(退役档案缺口 or 脏数据·salvage before delete)。只 auto-purge ①。"""
    roles = set(_list_roles())
    retired = set(_retired_roles())
    dangling = []            # 汇总(=purgeable_smoke + review·向后兼容旧字段)
    review = []              # ③需人核:目标既非活跃 role 也非退役档案(退役档案缺口 or 脏数据)
    purgeable_smoke = 0      # ①测试污染:__smoke* 残留·可 auto-purge
    accepted = 0             # ②契约内可接受悬挂:退役 role 历史条目(契约#1 悬挂处理明确允许)
    checked = 0

    # #1 todo.from → bus role(活跃在册=OK;退役=契约可接受②;smoke=可清①;其余=需人核③)
    todos = _load_json(BUS / "todos.json", [])
    for t in (todos if isinstance(todos, list) else []):
        frm = t.get("from") or t.get("confirm_from")
        if frm and frm not in ("bus健康", "unknown", "用户"):
            checked += 1
            if _is_smoke_from(frm):
                purgeable_smoke += 1
            elif frm in roles:
                pass
            elif frm in retired:
                accepted += 1
            else:
                review.append("todo %s: from=%r 既不在活跃 role 也不在退役档案(退役档案缺口 or 脏数据·需人核)" % (t.get("id", "?"), frm))

    # #3 task.from/to + #4 notice.notice_of + #6 presence.current_task(遍历 role 目录)
    for r in roles:
        rd = BUS / r
        if not rd.is_dir():
            continue
        # presence.current_task → 该 role processing 里应有同 id(或已完成清空)
        pres = _load_json(rd / "presence.json", {})
        ct = pres.get("current_task")
        if ct:
            checked += 1
            if not (rd / "processing" / (ct + ".json")).exists():
                review.append("presence %s: current_task=%s 不在 processing(可能刚完成未清·瞬时)" % (r, ct))
        # notice.notice_of → 目标 done 应可读(inbox 里的 notice)
        inb = rd / "inbox"
        if inb.is_dir():
            for f in inb.glob("notice-*.json"):
                d = _load_json(f, {})
                nof = d.get("notice_of")
                if nof:
                    checked += 1
                    # 兜底契约:原任务归档时读 <sender>/done/<id>;sender=notice.to(收件人派过的)
                    sender = d.get("to")
                    if sender and not (BUS / sender / "done" / (nof + ".json")).exists():
                        review.append("notice %s: notice_of=%s 的 done 不在 %s/done(可能已 prune·兜底允许)" % (f.name, nof, sender))
    dangling = list(review)  # 向后兼容旧字段(=需人核项;smoke 已单列 purgeable_smoke·不进 dangling)
    return {"checked": checked, "dangling": dangling, "review": review,
            "accepted": accepted, "purgeable_smoke": purgeable_smoke,
            "notes": ["longrange 内部 ref(node/edge/milestone/resolve_batch)由 longrange_net 引擎深校验,本 check 不展开",
                      "①purgeable_smoke=__smoke* 测试污染(可 ab refs --purge-smoke 清);②accepted=退役 role 历史条目(契约可接受·补 retired_roster 消误报);③review=需人核(退役档案缺口 or 脏数据·salvage before delete)"]}


def purge_smoke_todos():
    """①类 auto-purge:清 todos.json 里 __smoke* 测试污染(使用方批准的唯一可 auto-purge 类)。
    锁跨 RMW + 原子写 + 备份(P0·2026-07-28)。返回 (removed, before, after)。仅动 todos·不碰 ②③(退役历史/需人核)。"""
    p = BUS / "todos.json"
    with json_lock(p):
        d = _load_json(p, [])
        if not isinstance(d, list):
            return (0, 0, 0)
        kept = [t for t in d if not _is_smoke_from(t.get("from") or t.get("confirm_from"))]
        removed = len(d) - len(kept)
        if removed:
            (BUS / "todos.json.pre-smoke-purge.bak").write_text(
                json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            _write_json(p, kept)
    return (removed, len(d), len(kept))


def cmd_refs(args):
    """P2-2b:显示三 store 跨引用契约(单一真相源)+ 只读校验 bus-native ref 悬挂。
    --check 跑校验;默认列契约。契约化=把隐式 ref 显式声明·禁改现解析(使用方签 B)。"""
    if getattr(args, "purge_smoke", False):
        removed, before, after = purge_smoke_todos()
        print("①类 smoke 测试污染 purge:removed=%d(%d→%d)· 备份 todos.json.pre-smoke-purge.bak" % (
            removed, before, after))
        print("(仅清 __smoke*·不碰 ②退役历史 / ③需人核)")
        return
    if getattr(args, "check", False):
        r = check_refs()
        print("bus-native ref 校验:检查 %d 条 → ①smoke可清 %d · ②退役可接受 %d · ③需人核 %d" % (
            r["checked"], r.get("purgeable_smoke", 0), r.get("accepted", 0), len(r.get("review", []))))
        for d in r.get("review", [])[:20]:
            print("  ⚠ " + d)
        if len(r.get("review", [])) > 20:
            print("  …另 %d 条(全量见代码)" % (len(r["review"]) - 20))
        if r.get("purgeable_smoke", 0):
            print("  · ①smoke 可 `ab refs --purge-smoke` 清")
        for n in r["notes"]:
            print("  · " + n)
        return
    print("BUS 2.0 三 store 跨引用契约(P2-2b·B=分层保留+契约化 ref·使用方签)")
    print("=" * 78)
    cur = None
    for c in REF_CONTRACTS:
        if c["store"] != cur:
            cur = c["store"]; print("\n【%s store】" % cur)
        print("  #%-2d %-26s id空间=%s · 基数=%s · owner=%s" % (c["n"], c["ref"], c["id_space"], c["card"], c["owner"]))
        print("      悬挂:%s" % c["dangling"])
        print("      语义:%s" % c["means"])
    print("\n硬约束:跨 store 只经上述声明 ref 关联;ref 存标识符、禁内联对方 payload(防偷偷 A 归一)。")
    print("校验悬挂:ab refs --check")
