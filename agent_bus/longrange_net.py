#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""longrange_net.py — 长程任务【锚定生长网】生长引擎(P0 骨架 · 2026-07-13)。

用户 2026-07-13 令"优化 bus 长程任务";设计定稿见 agent_fleet/LONGRANGE_TASK_BRIEF_2026-07-13.md。
目标:把用户从"单步决策器"降负成"里程碑审阅者"——反复确认由机制吸收。

本文件 = MasterAgent 机制侧 P0:数据模型 + 用户锚点脊柱 + 单段线性生长→达锚收敛→塌缩归档 + append-only 变更日志 + 防环。
P1(fork/merge 接工作组池并行 · 自主分层 · 校验闸 · 决策合批)、P2(跨任务边 · 熔断 · 机器自设锚点)后续。

── 存储布局 ────────────────────────────────────────────────────────────
  权威态(引擎读写):   BUS/_longrange/<task_id>/net.json        每任务一份完整网(脊柱+活跃段+归档段)
  变更日志(append):   BUS/_longrange/<task_id>/changelog.jsonl  每次图变动一行(可回放网怎么长成今天)
  看板视图(MasterUI 读):    BUS/longrange_tasks.json                 全任务聚合·每次变动重建·WebUI 单次 fetch
「权威态」是唯一真相源;「看板视图」是从所有 net.json 聚合出的只读投影(MasterUI render 用)。

── 节点状态枚举(MasterUI 色板对照)──────────────────────────────────────────
  pending        待     父未全完成·未就绪            灰
  ready          就绪   父全完成·可领                浅蓝
  running        进行   已领执行中                   蓝
  blocked        阻塞   卡住(依赖失败/外部)          红
  await_decision 待拍   攒到决策闸·等用户批(合批)     黄
  done           完成   过验收                        绿
  failed         失败   验收不过/执行炸               深红
锚点(spine)状态:pending 待展开 / active 当前正长向它 / reached 已达并塌缩。
"""
import argparse, json, os, sys
from collections import deque
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.environ.setdefault("PYTHONUTF8", "1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentbus as ab   # 复用 _now / _write_json / _gen_id / BUS
try:
    # 使用方 owns:全网 shape 复杂度(读 events 重建)+ 成长曲线 + 反复确认降幅(★验收口径)
    from complexity_snapshot import (complexity_from_events, complexity_series_from_events,
                                     confirmation_savings_from_events)
except Exception:
    complexity_from_events = complexity_series_from_events = confirmation_savings_from_events = None

BUS = ab.BUS
LR_ROOT = BUS / "_longrange"
BOARD = BUS / "longrange_tasks.json"
EVENTS = BUS / "longrange_events.jsonl"   # 全局成长史(append-only·同 metrics.jsonl 范式·使用方 算成长速率/审计回放)

_NODE_ACTIVE = {"pending", "ready", "running", "blocked", "await_decision"}   # 仍占活工作集
_NODE_TERMINAL = {"done", "failed"}


# ── 底层读写 ───────────────────────────────────────────────────────────
def _task_dir(task_id):
    d = LR_ROOT / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _net_path(task_id):
    return _task_dir(task_id) / "net.json"


def _load_net(task_id):
    p = _net_path(task_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _save_net(net):
    net["updated"] = ab._now()
    ab._write_json(_net_path(net["id"]), net)
    _rebuild_board()


def _emit_event(task_id, event, **detail):
    """全局 append-only 成长史:每次图变动一行到 BUS/longrange_events.jsonl(使用方 算成长速率时间序列+审计回放,
    同 metrics.jsonl 范式·O_APPEND·带 task 字段·按 task 过滤即得单任务回放)。state json=当前快照,本文件=成长史,两者分开。
    best-effort,绝不影响主流程。"""
    try:
        rec = {"ts": ab._now(), "task": task_id, "event": event}
        rec.update(detail)
        with open(EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── 图算法:防环 ───────────────────────────────────────────────────────
def _reachable(nodes, src, dst):
    """src 能否顺 父→子 边到达 dst(children 方向遍历)。用于运行时加边守无环(§2.4-3)。"""
    if src == dst:
        return True
    seen, q = {src}, deque([src])
    children = {}
    for nid, n in nodes.items():
        for p in n.get("parents", []):
            children.setdefault(p, []).append(nid)
    while q:
        cur = q.popleft()
        for c in children.get(cur, []):
            if c == dst:
                return True
            if c not in seen:
                seen.add(c); q.append(c)
    return False


def _would_cycle(nodes, parent, child):
    """加边 parent→child 是否成环:当且仅当 child 已能到达 parent(child 是 parent 的上游)。"""
    return _reachable(nodes, child, parent)


# ── 段视图 / 进度 ──────────────────────────────────────────────────────
def _seg_nodes(net, seg_index):
    return {nid: n for nid, n in net["nodes"].items() if n.get("segment") == seg_index and not n.get("archived")}


def _node_status(net, nid):
    """派生就绪态,含【自主分层 §2.3-3】+【校验闸】:
    - running/done/failed 三终结/执行态 → 保持;
    - 父未全完成 → pending;
    - 父全完成后按自主等级 + 校验闸分流:
        · precheck=fail → blocked(校验闸拦下·炸在提审批前,不去烦用户);
        · autonomy=ask 且 precheck=pass → await_decision(进决策合批·等用户);
        · 其余(auto/notify,或 ask 待 precheck)→ ready(可执行/可先跑校验)。"""
    n = net["nodes"][nid]
    st = n["status"]
    if st in ("running", "done", "failed", "skipped"):
        return st
    parents = n.get("parents", [])
    if not all(net["nodes"].get(p, {}).get("status") == "done" for p in parents):
        return "pending"
    pc = (n.get("precheck") or {}).get("status", "pending")
    if pc == "fail":
        return "blocked"
    if n.get("autonomy", "auto") == "ask" and pc == "pass":
        return "await_decision"
    return "ready"


def _refresh_statuses(net):
    """按父完成度 + 自主分层 + 校验闸重刷可变态(pending/ready/blocked/await_decision 互转)。
    不动 running/done/failed 三态(执行/终结态,只由 complete_node / run 显式改)。"""
    for nid, n in net["nodes"].items():
        if n.get("archived"):
            continue
        if n["status"] not in ("running", "done", "failed", "skipped"):
            n["status"] = _node_status(net, nid)


def _blocked_on(net, nid):
    return [p for p in net["nodes"][nid].get("parents", []) if net["nodes"].get(p, {}).get("status") != "done"]


# ── 收敛判据(不动点)─────────────────────────────────────────────────
def segment_converged(net, seg_index):
    """段收敛=不动点:①前沿不再长(拆解者已声明 frontier_closed)②该段所有节点 done ③到达锚点节点。
    P0 线性:只要该段无 active 节点(全 done)且拆解者标了 frontier_closed 即收敛。"""
    seg = _seg_nodes(net, seg_index)
    if not seg:
        return False
    if not net.get("segment_meta", {}).get(str(seg_index), {}).get("frontier_closed"):
        return False
    # done 或 skipped(被跳过/驳回后显式跳)都算"已了结"·不挡收敛;failed 未 skip 仍挡(逼显式处置)
    return all(n["status"] in ("done", "skipped") for n in seg.values())


# ── 核心操作 ───────────────────────────────────────────────────────────
def init_net(task_id, title, spine):
    """播种:建脊柱(锚点序列)。spine=[{anchor,author,type,acceptance?,decision_gate?}]。
    current_segment=0 表示正长向 spine[1](第一个 mid/end);spine[0]=起点。"""
    if _load_net(task_id):
        raise SystemExit("net 已存在: %s" % task_id)
    for i, a in enumerate(spine):
        a.setdefault("author", "user")
        a.setdefault("type", "start" if i == 0 else ("end" if i == len(spine) - 1 else "mid"))
        a.setdefault("acceptance", None)
        a.setdefault("decision_gate", False)
        a["status"] = "reached" if i == 0 else ("active" if i == 1 else "upcoming")
    net = {
        "id": task_id, "title": title, "created": ab._now(), "updated": ab._now(),
        "status": "growing",
        "spine": spine,
        "current_segment": 1 if len(spine) > 1 else 0,   # 正长向的锚点在 spine 里的下标
        "nodes": {}, "segment_meta": {}, "archived_segments": [],
    }
    _save_net(net)
    _emit_event(task_id, "task_created", title=title, anchors=[a["anchor"] for a in spine])
    return net


def add_node(task_id, label, parent=None, owner=None, acceptance=None, author="machine",
             autonomy="auto"):
    """拆解者在当前段续接一个节点。P0 线性:parent 默认=当前段最后一个节点(无则接段起点=空父)。
    加边守无环。autonomy=auto(只读/可逆·自动推进)|notify(做了报一声)|ask(不可逆/对客/DDL·须合批请示)。
    返回 node_id。"""
    net = _load_net(task_id)
    if not net:
        raise SystemExit("net 不存在: %s" % task_id)
    _ensure_growing(net)   # 需求4:往 done 网加活 → 自动重开最后段·status 回 growing
    seg = net["current_segment"]
    nid = ab._gen_id("lrn").replace("-", "_")   # 稳作节点 id
    if parent is None:
        seg_ns = _seg_nodes(net, seg)
        # 线性:接在"最后创建的未归档段内节点"之后
        parent = max(seg_ns, key=lambda k: seg_ns[k]["created"], default=None) if seg_ns else None
    parents = [parent] if parent else []
    for p in parents:
        if p not in net["nodes"]:
            raise SystemExit("父节点不存在: %s" % p)
        if _would_cycle(net["nodes"], p, nid):
            raise SystemExit("加边 %s→%s 会成环,拒绝" % (p, nid))
    net["nodes"][nid] = {
        "id": nid, "label": label, "status": "pending", "owner": owner,
        "acceptance": acceptance, "verdict": None, "parents": parents,
        "author": author, "segment": seg, "created": ab._now(),
        "autonomy": autonomy,                                   # §2.3-3 自主分层
        "precheck": {"status": "pending", "findings": []},       # 执行前校验闸(炸在提审批前)
    }
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "node_added", node=nid, label=label, segment=seg, author=author, autonomy=autonomy)
    for p in parents:
        _emit_event(task_id, "edge_added", **{"from": p, "to": nid})
    return nid


def fork(task_id, parent, labels, owner=None, author="machine", autonomy="auto"):
    """分叉(§2.1 fork):一父节点撒 N 并行子。P1 只建【结构】——并行执行接工作组池是后补独立战线
    (用户令 P1 并行度暂 ~1.1 不攻),故子节点先落网,由执行者取 ready 跑(池化后自然并行)。返回 [child_id]。"""
    return [add_node(task_id, lbl, parent=parent, owner=owner, author=author, autonomy=autonomy)
            for lbl in labels]


def merge(task_id, label, parents, owner=None, acceptance=None, author="machine", autonomy="auto"):
    """合并(§2.1 merge):建【多父】节点,gate on 所有父 done 才解锁(=fanout-collect barrier 语义)。
    多父 gate 由 _node_status 天然实现(全父 done 前恒 pending)。加边守无环。返回 node_id。"""
    net = _load_net(task_id)
    if not net:
        raise SystemExit("net 不存在: %s" % task_id)
    if not parents:
        raise SystemExit("merge 需至少一个父")
    _ensure_growing(net)   # 需求4:往 done 网加活 → 自动重开
    seg = net["current_segment"]
    nid = ab._gen_id("lrn").replace("-", "_")
    for p in parents:
        if p not in net["nodes"]:
            raise SystemExit("父节点不存在: %s" % p)
        if _would_cycle(net["nodes"], p, nid):
            raise SystemExit("加边 %s→%s 会成环,拒绝" % (p, nid))
    net["nodes"][nid] = {
        "id": nid, "label": label, "status": "pending", "owner": owner,
        "acceptance": acceptance, "verdict": None, "parents": list(parents),
        "author": author, "segment": seg, "created": ab._now(),
        "autonomy": autonomy, "precheck": {"status": "pending", "findings": []},
        "kind": "merge",
    }
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "node_added", node=nid, label=label, segment=seg, author=author,
                autonomy=autonomy, kind="merge")
    for p in parents:
        _emit_event(task_id, "edge_added", **{"from": p, "to": nid})
    return nid


def complete_node(task_id, nid, verdict="ok"):
    """标节点完成过验收 → done。之后由拆解者重判是否催生新节点(P0 手动 add_node / close_frontier)。"""
    net = _load_net(task_id)
    n = net["nodes"].get(nid)
    if not n:
        raise SystemExit("节点不存在: %s" % nid)
    n["status"] = "done"; n["verdict"] = verdict
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "node_done", node=nid, verdict=verdict)


def precheck_node(task_id, nid, ok, findings=None):
    """执行前校验闸(§2.3 执行者·owner 预检/引用扫描):在【请示/执行前】跑,让"炸"发生在提审批前。
    ok=False → 节点 blocked(不去烦用户);ok=True + autonomy=ask → 转 await_decision 进合批。"""
    net = _load_net(task_id)
    n = net["nodes"].get(nid)
    if not n:
        raise SystemExit("节点不存在: %s" % nid)
    n["precheck"] = {"status": "pass" if ok else "fail", "findings": findings or []}
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "precheck", node=nid, result=("pass" if ok else "fail"), findings=findings or [])
    return n["status"]


def decide_batch(task_id):
    """决策合批(§2.4·反复确认头号杀手之二):把当前段所有 await_decision 节点【攒一张单】,
    一次给用户批、不零售(直杀单表去留反复5来回 / 一批 DDL 拆 4 审批点)。
    只建【一张】待批单存进 net(MasterUI 渲染成"待批决策单·一处点批");用户答后 resolve_batch 落。"""
    net = _load_net(task_id)
    seg = net["current_segment"]
    pend = [n for n in _seg_nodes(net, seg).values() if n["status"] == "await_decision"]
    if not pend:
        return None
    batch_id = ab._gen_id("lrb").replace("-", "_")
    batch = {
        "batch_id": batch_id, "segment": seg, "created": ab._now(),
        "decisions": [{"node": n["id"], "label": n["label"], "acceptance": n.get("acceptance"),
                       "autonomy": n.get("autonomy"), "precheck": n.get("precheck")}
                      for n in pend],
    }
    net["pending_decision_batch"] = batch
    _save_net(net)
    _emit_event(task_id, "decision_batch_raised", batch=batch_id, count=len(pend),
                nodes=[n["id"] for n in pend])
    return batch


def resolve_batch(task_id, approvals):
    """用户在一处批完 → 落合批结果。approvals={node_id:"approve"|"reject"}。
    approve → 节点解闸(precheck 已 pass·转 ready 交执行);reject → blocked。清空待批单。"""
    net = _load_net(task_id)
    batch = net.get("pending_decision_batch")
    if not batch:
        raise SystemExit("无待批决策单: %s" % task_id)
    applied = {}
    for d in batch["decisions"]:
        nid = d["node"]
        verdict = approvals.get(nid, "reject")   # 未答默认保守 reject(宁卡不误做不可逆)
        n = net["nodes"].get(nid)
        if not n:
            continue
        if verdict == "approve":
            n["autonomy"] = "notify"              # 已获批→降级为 notify,不再 await_decision→转 ready
        else:
            n["status"] = "failed"; n["verdict"] = "user_rejected"
        applied[nid] = verdict
    net["pending_decision_batch"] = None
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "decision_batch_resolved", batch=batch["batch_id"], applied=applied)
    return applied


def close_frontier(task_id, seg_index=None):
    """拆解者声明:当前段前沿不再长出新节点(不动点收敛的必要条件)。"""
    net = _load_net(task_id)
    seg = net["current_segment"] if seg_index is None else seg_index
    net.setdefault("segment_meta", {}).setdefault(str(seg), {})["frontier_closed"] = True
    _save_net(net)
    _emit_event(task_id, "frontier_closed", segment=seg)


def collapse_segment(task_id, summary=None):
    """达锚收敛 → 该段塌缩成单归档节点·细节归档·不再占活内存;推进到下一锚点(§2.2 有界生长核心)。"""
    net = _load_net(task_id)
    seg = net["current_segment"]
    if not segment_converged(net, seg):
        raise SystemExit("段 %d 未收敛(需 frontier_closed + 全节点 done),不能塌缩" % seg)
    seg_ns = _seg_nodes(net, seg)
    anchor = net["spine"][seg]
    seg_edges = sum(len([p for p in n.get("parents", []) if p in seg_ns]) for n in seg_ns.values())
    collapsed = {
        "id": "seg%d_collapsed" % seg, "label": "[段%d归档] %s" % (seg, anchor["anchor"]),
        "summary": summary or ("%d 节点达锚'%s'收敛" % (len(seg_ns), anchor["anchor"])),
        "verdict": "ok",
        # collapsed{nodes,edges}:归档段复杂度仍计入总量、但不占活内存(§2.2 资源设计核心·使用方 契约)
        "collapsed": {"nodes": len(seg_ns), "edges": seg_edges},
        "node_count": len(seg_ns),
    }
    for n in seg_ns.values():
        n["archived"] = True
    net["archived_segments"].append({
        "seg_index": seg, "to_anchor": anchor["anchor"],
        "collapsed_node": collapsed, "node_count": len(seg_ns), "edge_count": seg_edges,
        "archived_at": ab._now(),
    })
    anchor["status"] = "reached"
    # 推进脊柱
    nxt = seg + 1
    if nxt < len(net["spine"]):
        net["spine"][nxt]["status"] = "active"
        net["current_segment"] = nxt
        net["status"] = "growing"
    else:
        net["status"] = "done"
    _save_net(net)
    _emit_event(task_id, "anchor_reached", anchor=anchor["anchor"], segment=seg)
    _emit_event(task_id, "segment_collapsed", segment=seg, node_count=len(seg_ns),
                edge_count=seg_edges, advanced_to=nxt)
    return collapsed


# ── 编辑/回退类(demo-feat-migrate 实战暴露·2026-07-13)──────────────────────
def update_anchor(task_id, index, anchor=None, acceptance=None, decision_gate=None):
    """编辑脊柱锚点(尤其【更新最终目标】)。index 支持负数(-1=终点)。长程网跑着改目标不用加占位节点绕。"""
    net = _load_net(task_id)
    sp = net["spine"]
    if index < 0:
        index += len(sp)
    if not (0 <= index < len(sp)):
        raise SystemExit("锚点下标越界: %d" % index)
    a = sp[index]
    before = dict(a)
    if anchor is not None:
        a["anchor"] = anchor
    if acceptance is not None:
        a["acceptance"] = acceptance
    if decision_gate is not None:
        a["decision_gate"] = bool(decision_gate)
    _save_net(net)
    _emit_event(task_id, "anchor_updated", index=index, before=before.get("anchor"), after=a["anchor"])
    return a


def set_goal(task_id, goal, acceptance=None):
    """更新【终点锚点】(最终目标)——update_anchor 的便捷式。"""
    net = _load_net(task_id)
    return update_anchor(task_id, len(net["spine"]) - 1, anchor=goal, acceptance=acceptance)


def _ensure_growing(net):
    """若网已 done、又要往里加活(add/fork/merge):自动把最后塌缩的段重开(un-archive)+status→growing
    (需求4:add 到 done 网状态机自动回退,不然加了节点 show 仍 done)。就地改 net,不落盘(调用方 save)。"""
    if net.get("status") != "done":
        return
    seg = net["current_segment"]
    for n in net["nodes"].values():
        if n.get("segment") == seg:
            n["archived"] = False
    net["archived_segments"] = [x for x in net["archived_segments"] if x["seg_index"] != seg]
    if seg < len(net["spine"]):
        net["spine"][seg]["status"] = "active"
    net.get("segment_meta", {}).get(str(seg), {}).pop("frontier_closed", None)
    net["status"] = "growing"


def reopen_segment(task_id, seg_index=None):
    """重开已塌缩/已达成的锚点段(un-collapse·需求2):误标达成后回退。默认重开最后归档的段。
    un-archive 该段节点 + 从 archived_segments 移除 + 锚点回 active + 清 frontier_closed + current_segment 指回 + status=growing。"""
    net = _load_net(task_id)
    arch = net["archived_segments"]
    if not arch:
        raise SystemExit("无已塌缩段可重开")
    if seg_index is None:
        seg_index = arch[-1]["seg_index"]
    if not any(x["seg_index"] == seg_index for x in arch):
        raise SystemExit("段 %d 未塌缩,无需重开" % seg_index)
    for n in net["nodes"].values():
        if n.get("segment") == seg_index:
            n["archived"] = False
    net["archived_segments"] = [x for x in arch if x["seg_index"] != seg_index]
    if seg_index < len(net["spine"]):
        net["spine"][seg_index]["status"] = "active"
    # 该段及其后的锚点状态回退(后续锚点若已 reached 也应回 upcoming)
    for i in range(seg_index + 1, len(net["spine"])):
        net["spine"][i]["status"] = "upcoming"
    net.get("segment_meta", {}).get(str(seg_index), {}).pop("frontier_closed", None)
    net["current_segment"] = seg_index
    net["status"] = "growing"
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "segment_reopened", segment=seg_index)
    return seg_index


def remove_node(task_id, nid):
    """删节点(需求3):从网移除 + 从所有子节点的 parents 摘除该边(免悬空依赖)。"""
    net = _load_net(task_id)
    if nid not in net["nodes"]:
        raise SystemExit("节点不存在: %s" % nid)
    for c in net["nodes"].values():
        if nid in c.get("parents", []):
            c["parents"].remove(nid)
    del net["nodes"][nid]
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "node_removed", node=nid)


def skip_node(task_id, nid, reason=None):
    """跳过节点(需求3):status=skipped·不挡收敛(被 reject/放弃的节点显式跳,免它卡住 collapse)。"""
    net = _load_net(task_id)
    n = net["nodes"].get(nid)
    if not n:
        raise SystemExit("节点不存在: %s" % nid)
    n["status"] = "skipped"; n["verdict"] = "skipped" + (("·" + reason) if reason else "")
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "node_skipped", node=nid, reason=reason)


def track(task_id, tracker=None, summary=None, watching=None, action=None, pending=None):
    """跟踪者督办可观测性(2026-07-14·用户要"看得到跟踪者在盯什么"):把跟踪状态写进网 → 看板可读。
    执行体无关(谁跟踪谁写·我/Master子agent/daemon 都行);不注册幻影 presence(方案②非①)。
    summary=一句话在盯啥;watching=当前关注项[str];action=本次督办动作(追进 recent_actions·留最近10);pending=待办督办[str]。"""
    net = _load_net(task_id)
    if not net:
        raise SystemExit("net 不存在: %s" % task_id)
    ts = net.get("tracker_status") or {"recent_actions": []}
    if tracker is not None:
        ts["tracker"] = tracker
    ts["last_scan"] = ab._now()
    if summary is not None:
        ts["summary"] = summary
    if watching is not None:
        ts["watching"] = watching
    if pending is not None:
        ts["pending"] = pending
    if action:
        ts.setdefault("recent_actions", []).append({"ts": ab._now(), "action": action})
        ts["recent_actions"] = ts["recent_actions"][-10:]   # 留最近 10
    net["tracker_status"] = ts
    _save_net(net)
    _emit_event(task_id, "tracker_scan", tracker=ts.get("tracker"), summary=summary, action=action)
    return ts


# ── 复杂度标尺(raw·口径使用方 P1 定准)───────────────────────────────────
def _complexity(net):
    """task 级复杂度 raw 量:归档段计入总量(collapsed{nodes,edges}·§2.2)、宽深按当前活图。
    使用方 P1 给准口径,这里先 emit raw 让它算(留 raw 优于留空)。"""
    live = [n for nid, n in net["nodes"].items() if not n.get("archived")]
    arch_nodes = sum(a.get("edge_count", 0) * 0 + a.get("node_count", 0) for a in net["archived_segments"])
    arch_edges = sum(a.get("edge_count", 0) for a in net["archived_segments"])
    live_edges = sum(len(n.get("parents", [])) for n in live)
    # 活图分层:level(n)=0 无父 else 1+max(level(父));depth=最长路,width_max=同层最多节点
    lvl, live_ids = {}, {n["id"] for n in live}
    def _lvl(nid, seen=None):
        seen = seen or set()
        if nid in lvl:
            return lvl[nid]
        if nid in seen:
            return 0
        seen = seen | {nid}
        ps = [p for p in net["nodes"][nid].get("parents", []) if p in live_ids]
        v = 0 if not ps else 1 + max(_lvl(p, seen) for p in ps)
        lvl[nid] = v
        return v
    for n in live:
        _lvl(n["id"])
    width = {}
    for v in lvl.values():
        width[v] = width.get(v, 0) + 1
    merges = sum(1 for n in live if len([p for p in n.get("parents", []) if p in live_ids]) >= 2)
    return {
        # size(累计含归档)=使用方 P0 验收"对·不动"
        "nodes_total": len(live) + arch_nodes,
        "edges_total": live_edges + arch_edges,
        "nodes_live": len(live), "edges_live": live_edges,
        # shape·活跃段口径(我算·供调度/资源视图·使用方 令"别丢")
        "depth_live": (max(lvl.values()) + 1) if lvl else 0,
        "width_live": max(width.values()) if width else 0,
        "merges_live": merges,
        # shape·全网口径(读 longrange_events.jsonl 重建·使用方 complexity_snapshot 工具填)——留 null 占位,
        # 别把活跃段 depth 误当全网复杂度(用户"网大小=复杂度"要全网 shape)
        "depth": None, "width_max": None, "merges": None,
        "cross_task_edges": 0,                               # P2 跨任务边
        "note": "size=全网累计;_live=活跃段(MasterAgent算);depth/width_max/merges=全网口径待使用方 complexity_snapshot 填",
    }


# ── 看板聚合投影(MasterUI 读 BUS/longrange_tasks.json)────────────────────────
def _board_task_view(net):
    seg = net["current_segment"]
    seg_ns = _seg_nodes(net, seg)
    nodes_view, edges = [], []
    for nid, n in seg_ns.items():
        nodes_view.append({
            "id": nid, "label": n["label"], "status": n["status"],
            "owner": n.get("owner"), "acceptance": n.get("acceptance"),
            "verdict": n.get("verdict"), "author": n.get("author"),
            "autonomy": n.get("autonomy", "auto"), "precheck": n.get("precheck"),
            "blocked_on": _blocked_on(net, nid),
        })
        for p in n.get("parents", []):
            if p in seg_ns:
                edges.append({"from": p, "to": nid})
    # 进度:分母=脊柱段数(粗)+当前段已完成/总(细);P0 用"已达锚点段/总段"做主进度
    total_seg = max(1, len(net["spine"]) - 1)
    done_seg = len(net["archived_segments"])
    seg_done = sum(1 for n in seg_ns.values() if n["status"] == "done")
    seg_total = max(1, len(seg_ns))
    pct = round(100.0 * (done_seg + (seg_done / seg_total)) / total_seg, 1)
    active_anchor = net["spine"][seg] if seg < len(net["spine"]) else None
    from_anchor = net["spine"][seg - 1]["anchor"] if seg >= 1 else net["spine"][0]["anchor"]
    return {
        "id": net["id"], "title": net["title"], "created": net["created"],
        "updated": net["updated"], "status": net["status"],
        "spine": [{"anchor": a["anchor"], "author": a["author"], "type": a["type"],
                   "acceptance": a.get("acceptance"), "decision_gate": a.get("decision_gate"),
                   "status": a["status"]} for a in net["spine"]],
        "current_segment": seg,
        "active_segment": {
            "from_anchor": from_anchor,
            "to_anchor": active_anchor["anchor"] if active_anchor else None,
            "seg_index": seg, "nodes": nodes_view, "edges": edges,
        },
        "archived_segments": [{"seg_index": a["seg_index"], "to_anchor": a["to_anchor"],
                               "node_count": a["node_count"], "summary": a["collapsed_node"]["summary"],
                               "collapsed": a["collapsed_node"].get("collapsed", {})}
                              for a in net["archived_segments"]],
        "progress": {"done_segments": done_seg, "total_segments": total_seg,
                     "seg_done_nodes": seg_done, "seg_total_nodes": seg_total,
                     "pct": pct, "growable": True},
        "complexity": _fill_fullnet_complexity(_complexity(net), net["id"]),
        # 成长曲线(使用方·MasterUI sparkline)+ 反复确认降幅(★验收·MasterUI 渲"已省N次反复确认"价值徽标)
        "complexity_series": _series(net["id"]),
        "efficiency": _efficiency(net["id"]),
        # 待批决策单(§2.4 合批·MasterUI 渲染成"一处点批"·null=当前无待批)
        "pending_decision_batch": net.get("pending_decision_batch"),
        # 跟踪者督办状态(2026-07-14·用户要可观测·MasterUI 渲"跟踪者"条:在盯啥/上次巡/督办动作/待办)
        "tracker_status": net.get("tracker_status"),
    }


def _series(task_id):
    """成长曲线 [{t,size,live}](使用方·MasterUI sparkline)。best-effort。"""
    if complexity_series_from_events is None:
        return []
    try:
        return complexity_series_from_events(str(EVENTS), task_id)
    except Exception:
        return []


def _efficiency(task_id):
    """反复确认降幅(★验收核·使用方):{confirmation_turns_saved,...}。MasterUI 渲"已省N次反复确认"。best-effort。"""
    if confirmation_savings_from_events is None:
        return None
    try:
        return confirmation_savings_from_events(str(EVENTS), task_id)
    except Exception:
        return None


def _fill_fullnet_complexity(comp, task_id):
    """用使用方 的 complexity_snapshot(读 events 重建全网)覆盖 depth/width_max/merges 的 null 占位;
    保留 MasterAgent 侧 *_live。best-effort:events 缺/工具不可用 → 保留 null(不崩 board emit)。"""
    if complexity_from_events is None:
        return comp
    try:
        shape = complexity_from_events(str(EVENTS), task_id)
        for k in ("nodes_total", "edges_total", "depth", "width_max", "merges"):
            if k in shape:
                comp[k] = shape[k]
        comp["per_segment"] = shape.get("per_segment")
        comp["note"] = shape.get("note", comp.get("note"))
    except Exception:
        pass
    return comp


def _rebuild_board():
    """从所有 _longrange/*/net.json 聚合出 MasterUI 看板视图。每次 _save_net 后调。"""
    tasks = []
    if LR_ROOT.exists():
        for d in sorted(LR_ROOT.iterdir()):
            p = d / "net.json"
            if p.is_dir() or not p.exists():
                continue
            try:
                tasks.append(_board_task_view(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
    ab._write_json(BOARD, {"updated": ab._now(), "tasks": tasks,
                           "schema": "longrange_v0",
                           "cross_edges": [],   # 全局跨任务边(P2·现预留空·避免 P1/P2 重塑破坏 MasterUI render)
                           "status_colors": {"pending": "灰", "ready": "浅蓝", "running": "蓝",
                                             "blocked": "红", "await_decision": "黄",
                                             "done": "绿", "failed": "深红"},
                           "anchor_status_colors": {"upcoming": "灰", "active": "蓝", "reached": "绿"}})


# ── CLI ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(prog="longrange_net", description="长程任务锚定生长网 P0 引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="播种:建脊柱")
    s.add_argument("--id", required=True); s.add_argument("--title", required=True)
    s.add_argument("--spine", required=True, help='JSON数组 [{"anchor","author","type","acceptance","decision_gate"}]')

    s = sub.add_parser("add", help="拆解者续接节点")
    s.add_argument("--id", required=True); s.add_argument("--label", required=True)
    s.add_argument("--parent"); s.add_argument("--owner"); s.add_argument("--acceptance")
    s.add_argument("--author", default="machine")
    s.add_argument("--autonomy", default="auto", choices=["auto", "notify", "ask"],
                   help="auto只读可逆自动|notify做了报一声|ask不可逆/对客/DDL须合批请示")

    s = sub.add_parser("fork", help="一父撒 N 并行子(结构·并行执行后补)")
    s.add_argument("--id", required=True); s.add_argument("--parent", required=True)
    s.add_argument("--labels", required=True, help="JSON数组 子节点label")
    s.add_argument("--autonomy", default="auto", choices=["auto", "notify", "ask"])

    s = sub.add_parser("merge", help="多父合并节点(gate on 所有父 done)")
    s.add_argument("--id", required=True); s.add_argument("--label", required=True)
    s.add_argument("--parents", required=True, help="JSON数组 父节点id")
    s.add_argument("--acceptance"); s.add_argument("--autonomy", default="auto", choices=["auto", "notify", "ask"])

    s = sub.add_parser("done-node", help="标节点完成")
    s.add_argument("--id", required=True); s.add_argument("--node", required=True)
    s.add_argument("--verdict", default="ok")

    s = sub.add_parser("precheck", help="执行前校验闸(炸在提审批前)")
    s.add_argument("--id", required=True); s.add_argument("--node", required=True)
    s.add_argument("--fail", action="store_true", help="校验失败(默认 pass)")
    s.add_argument("--findings", default="", help="发现(逗号分隔)")

    s = sub.add_parser("decide-batch", help="决策合批:攒当前段 await_decision 成一张单")
    s.add_argument("--id", required=True)

    s = sub.add_parser("resolve-batch", help="落合批结果")
    s.add_argument("--id", required=True)
    s.add_argument("--approvals", required=True, help='JSON {node_id:"approve"|"reject"}')

    s = sub.add_parser("close-frontier", help="声明当前段前沿封闭")
    s.add_argument("--id", required=True)

    s = sub.add_parser("collapse", help="达锚收敛塌缩推进")
    s.add_argument("--id", required=True); s.add_argument("--summary")

    s = sub.add_parser("update-anchor", help="编辑脊柱锚点(index 支持负数·-1=终点)")
    s.add_argument("--id", required=True); s.add_argument("--index", type=int, required=True)
    s.add_argument("--anchor"); s.add_argument("--acceptance")
    s.add_argument("--decision-gate", dest="decision_gate", choices=["true", "false"])

    s = sub.add_parser("set-goal", help="更新终点锚点(最终目标)")
    s.add_argument("--id", required=True); s.add_argument("--goal", required=True); s.add_argument("--acceptance")

    s = sub.add_parser("reopen", help="重开已塌缩段(un-collapse·默认最后一段)")
    s.add_argument("--id", required=True); s.add_argument("--segment", type=int)

    s = sub.add_parser("remove-node", help="删节点(摘除悬空边)")
    s.add_argument("--id", required=True); s.add_argument("--node", required=True)

    s = sub.add_parser("skip-node", help="跳过节点(status=skipped·不挡收敛)")
    s.add_argument("--id", required=True); s.add_argument("--node", required=True); s.add_argument("--reason")

    s = sub.add_parser("track", help="跟踪者写督办状态(看板可读·可观测性)")
    s.add_argument("--id", required=True); s.add_argument("--tracker"); s.add_argument("--summary")
    s.add_argument("--watching", help="JSON数组 当前关注项"); s.add_argument("--action", help="本次督办动作")
    s.add_argument("--pending", help="JSON数组 待办督办")

    s = sub.add_parser("show", help="打印某任务网 JSON(附 complexity+efficiency)"); s.add_argument("--id", required=True)
    sub.add_parser("board", help="重建并打印看板视图")

    a = ap.parse_args()
    if a.cmd == "init":
        net = init_net(a.id, a.title, json.loads(a.spine))
        print("初始化: %s 脊柱%d锚点 current_segment=%d" % (a.id, len(net["spine"]), net["current_segment"]))
    elif a.cmd == "add":
        nid = add_node(a.id, a.label, parent=a.parent, owner=a.owner, acceptance=a.acceptance,
                       author=a.author, autonomy=a.autonomy)
        print("加节点: %s (autonomy=%s)" % (nid, a.autonomy))
    elif a.cmd == "fork":
        ids = fork(a.id, a.parent, json.loads(a.labels), autonomy=a.autonomy)
        print("fork %d 子: %s" % (len(ids), ",".join(ids)))
    elif a.cmd == "merge":
        nid = merge(a.id, a.label, json.loads(a.parents), acceptance=a.acceptance, autonomy=a.autonomy)
        print("merge 节点: %s (%d 父)" % (nid, len(json.loads(a.parents))))
    elif a.cmd == "done-node":
        complete_node(a.id, a.node, a.verdict); print("完成: %s" % a.node)
    elif a.cmd == "precheck":
        st = precheck_node(a.id, a.node, not a.fail, [x for x in a.findings.split(",") if x])
        print("校验%s → 节点态=%s" % ("失败" if a.fail else "通过", st))
    elif a.cmd == "decide-batch":
        b = decide_batch(a.id)
        print("待批单: %s (%d 决策)" % (b["batch_id"], len(b["decisions"])) if b else "无 await_decision 节点")
    elif a.cmd == "resolve-batch":
        ap2 = resolve_batch(a.id, json.loads(a.approvals)); print("已落: %s" % json.dumps(ap2, ensure_ascii=False))
    elif a.cmd == "close-frontier":
        close_frontier(a.id); print("前沿封闭")
    elif a.cmd == "collapse":
        c = collapse_segment(a.id, a.summary); print("塌缩: %s" % c["label"])
    elif a.cmd == "update-anchor":
        dg = {"true": True, "false": False}.get(a.decision_gate)
        an = update_anchor(a.id, a.index, anchor=a.anchor, acceptance=a.acceptance, decision_gate=dg)
        print("锚点[%d]更新: %s" % (a.index, an["anchor"]))
    elif a.cmd == "set-goal":
        an = set_goal(a.id, a.goal, a.acceptance); print("终点目标更新: %s" % an["anchor"])
    elif a.cmd == "reopen":
        seg = reopen_segment(a.id, a.segment); print("重开段: %d (status→growing)" % seg)
    elif a.cmd == "remove-node":
        remove_node(a.id, a.node); print("删节点: %s" % a.node)
    elif a.cmd == "skip-node":
        skip_node(a.id, a.node, a.reason); print("跳过节点: %s" % a.node)
    elif a.cmd == "track":
        ts = track(a.id, tracker=a.tracker, summary=a.summary,
                   watching=json.loads(a.watching) if a.watching else None,
                   action=a.action, pending=json.loads(a.pending) if a.pending else None)
        print("跟踪状态更新: %s | 最近动作 %d 条" % (ts.get("summary") or "-", len(ts.get("recent_actions") or [])))
    elif a.cmd == "show":
        net = _load_net(a.id)
        if net:                                   # 需求5:show 附 complexity+efficiency(看"已省N次反复确认")
            net = dict(net)
            net["_complexity"] = _fill_fullnet_complexity(_complexity(_load_net(a.id)), a.id)
            net["_efficiency"] = _efficiency(a.id)
        print(json.dumps(net, ensure_ascii=False, indent=1))
    elif a.cmd == "board":
        _rebuild_board(); print(BOARD.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
