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
    """派生就绪态:父全 done → ready(除非已 running/done/failed/blocked/await_decision);父未全完成 → pending。"""
    n = net["nodes"][nid]
    st = n["status"]
    if st in ("running", "done", "failed", "blocked", "await_decision"):
        return st
    parents = n.get("parents", [])
    if all(net["nodes"].get(p, {}).get("status") == "done" for p in parents):
        return "ready"
    return "pending"


def _refresh_statuses(net):
    """把所有非终结、非人工态的节点按父完成度刷成 pending/ready。"""
    for nid, n in net["nodes"].items():
        if n.get("archived"):
            continue
        if n["status"] in ("pending", "ready"):
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
    return all(n["status"] == "done" for n in seg.values())


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


def add_node(task_id, label, parent=None, owner=None, acceptance=None, author="machine"):
    """拆解者在当前段续接一个节点。P0 线性:parent 默认=当前段最后一个节点(无则接段起点=空父)。
    加边守无环。返回 node_id。"""
    net = _load_net(task_id)
    if not net:
        raise SystemExit("net 不存在: %s" % task_id)
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
    }
    _refresh_statuses(net)
    _save_net(net)
    _emit_event(task_id, "node_added", node=nid, label=label, segment=seg, author=author)
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
        "nodes_total": len(live) + arch_nodes,        # 含归档(塌缩计入总量)
        "edges_total": live_edges + arch_edges,
        "nodes_live": len(live), "edges_live": live_edges,
        "width_max": max(width.values()) if width else 0,   # 并行度
        "depth": (max(lvl.values()) + 1) if lvl else 0,      # 关键路径
        "merges": merges, "cross_task_edges": 0,             # P2 跨任务边
        "note": "raw·口径待使用方 P1 定准",
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
        "complexity": _complexity(net),
    }


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

    s = sub.add_parser("done-node", help="标节点完成")
    s.add_argument("--id", required=True); s.add_argument("--node", required=True)
    s.add_argument("--verdict", default="ok")

    s = sub.add_parser("close-frontier", help="声明当前段前沿封闭")
    s.add_argument("--id", required=True)

    s = sub.add_parser("collapse", help="达锚收敛塌缩推进")
    s.add_argument("--id", required=True); s.add_argument("--summary")

    s = sub.add_parser("show", help="打印某任务网 JSON"); s.add_argument("--id", required=True)
    sub.add_parser("board", help="重建并打印看板视图")

    a = ap.parse_args()
    if a.cmd == "init":
        net = init_net(a.id, a.title, json.loads(a.spine))
        print("初始化: %s 脊柱%d锚点 current_segment=%d" % (a.id, len(net["spine"]), net["current_segment"]))
    elif a.cmd == "add":
        nid = add_node(a.id, a.label, parent=a.parent, owner=a.owner, acceptance=a.acceptance, author=a.author)
        print("加节点: %s" % nid)
    elif a.cmd == "done-node":
        complete_node(a.id, a.node, a.verdict); print("完成: %s" % a.node)
    elif a.cmd == "close-frontier":
        close_frontier(a.id); print("前沿封闭")
    elif a.cmd == "collapse":
        c = collapse_segment(a.id, a.summary); print("塌缩: %s" % c["label"])
    elif a.cmd == "show":
        print(json.dumps(_load_net(a.id), ensure_ascii=False, indent=1))
    elif a.cmd == "board":
        _rebuild_board(); print(BOARD.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
