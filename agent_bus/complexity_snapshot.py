# -*- coding: utf-8 -*-
"""长程任务「生长网」复杂度全网口径 —— 单一真源(使用方 owns·2026-07-13)。

背景:bus 长程任务=锚定生长网(见 LONGRANGE_TASK_BRIEF_2026-07-13.md)。
state 快照 longrange_tasks.json 里达锚段会塌缩归档、只留 collapsed{} 摘要,活态图
丢结构;故【全网 shape 复杂度】必须从 append-only longrange_events.jsonl 重建
(node_added 带 segment、edge_added 带 from/to、塌缩段的节点/边 events 不删)。

MasterAgent 在 complexity{} 块留 depth/width_max/merges = null 占位,由本工具填。
活跃段口径 depth_live/width_live/merges_live 由 MasterAgent 侧算(调度/资源视图),本工具不碰。

口径定义(使用方 定准):
- nodes_total/edges_total : 全网累计(含归档)节点/边数 = 复杂度总量。
- depth      : 全网关键路径长度(节点数)。段间由脊柱锚点串行相连(任一时刻仅一段活),
               故 depth = Σ 各段内部最长路径。反映"最快多少步能到终点"。
- width_max  : 全网最大并行度 = 各段"层宽"峰值的最大值(层=按距源最长距离分层,
               同层节点无先后依赖=可并行)。段串行→全局取各段 max。
- merges     : 全网入度≥2 的节点数(合并/汇聚点)= 协调复杂度。
- 另附 per_segment 明细 + reconstructed{nodes,edges} 供核对/审计。

用法(MasterAgent 在 emit longrange_tasks.json 时):
    from complexity_snapshot import complexity_from_events
    shape = complexity_from_events(events_path, task_id)
    task["complexity"].update(shape)   # 覆盖 null 占位;*_live 字段保留不动
"""
import json
from collections import defaultdict


def _load_events(events_path, task_id):
    nodes = {}          # nid -> {"segment": int, "label": str}
    edges = []          # (from, to)
    with open(events_path, encoding="utf-8") as fh:      # 显式 UTF-8(Win GBK 坑)
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if task_id is not None and e.get("task") != task_id:
                continue
            ev = e.get("event")
            if ev == "node_added":
                nid = e.get("node")
                if nid:
                    nodes[nid] = {"segment": e.get("segment"), "label": e.get("label", "")}
            elif ev == "edge_added":
                f, t = e.get("from"), e.get("to")
                if f and t:
                    edges.append((f, t))
    return nodes, edges


def _seg_of(nodes, nid):
    return (nodes.get(nid) or {}).get("segment")


def _longest_path_and_levels(seg_nodes, seg_edges):
    """段内 DAG:返回 (最长路径节点数, 层宽峰值)。
    层 = 节点距源(入度0)的最长距离;longest path = max(level)+1(以节点计)。"""
    if not seg_nodes:
        return 0, 0
    succ = defaultdict(list)
    indeg = {n: 0 for n in seg_nodes}
    for f, t in seg_edges:
        if f in seg_nodes and t in seg_nodes:
            succ[f].append(t)
            indeg[t] += 1
    # 最长距离(层号)= 拓扑 DP
    level = {n: 0 for n in seg_nodes}
    # Kahn 拓扑序
    from collections import deque
    q = deque([n for n in seg_nodes if indeg[n] == 0])
    indeg_w = dict(indeg)
    order = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in succ[n]:
            level[m] = max(level[m], level[n] + 1)
            indeg_w[m] -= 1
            if indeg_w[m] == 0:
                q.append(m)
    # 有环兜底(生长网应无环;若重建出环,order 不全→退化按节点数)
    if len(order) < len(seg_nodes):
        return len(seg_nodes), 1
    depth = max(level.values()) + 1 if level else 0          # 以节点计
    width = defaultdict(int)
    for n in seg_nodes:
        width[level[n]] += 1
    width_max = max(width.values()) if width else 0
    return depth, width_max


def complexity_from_events(events_path, task_id):
    """从 longrange_events.jsonl 重建全网,算全网 shape 复杂度。
    返回 dict:填 MasterAgent 的 complexity{} null 占位(depth/width_max/merges/nodes_total/edges_total)
    + per_segment 明细 + note。*_live 字段不在此(MasterAgent 侧算)。"""
    nodes, edges = _load_events(events_path, task_id)
    # 全网入度(算 merges)
    indeg = defaultdict(int)
    for f, t in edges:
        indeg[t] += 1
    merges = sum(1 for n in nodes if indeg[n] >= 2)
    # 分段
    seg_ids = sorted({n["segment"] for n in nodes.values() if n["segment"] is not None})
    per_segment = []
    depth_total = 0
    width_global = 0
    for s in seg_ids:
        sn = {nid for nid, meta in nodes.items() if meta["segment"] == s}
        se = [(f, t) for (f, t) in edges if f in sn and t in sn]
        d, w = _longest_path_and_levels(sn, se)
        per_segment.append({"segment": s, "nodes": len(sn), "edges": len(se),
                            "depth": d, "width_max": w})
        depth_total += d                       # 段串行(脊柱锚点相连)→ 关键路径 = Σ 各段
        width_global = max(width_global, w)     # 段串行 → 并行峰值 = 各段 max
    return {
        "nodes_total": len(nodes),
        "edges_total": len(edges),
        "depth": depth_total,
        "width_max": width_global,
        "merges": merges,
        "per_segment": per_segment,
        "note": "全网口径·使用方·从 longrange_events.jsonl 重建(含归档段);depth=Σ段内最长路,width_max=各段层宽峰值max,merges=全网入度≥2节点数",
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path
    ep = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / ".claude/agent_bus/longrange_events.jsonl")
    tid = sys.argv[2] if len(sys.argv) > 2 else "demo-feat-migrate"
    print(json.dumps(complexity_from_events(ep, tid), ensure_ascii=False, indent=1))
