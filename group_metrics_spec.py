# 工作组并行指标·权威口径实现(使用方 出 · 供 MasterUI 直接嵌 build_groups / /api/groups)
# 这就是手算 示例组 1.10 / 负载 12-9-9 用的同一套。纯 stdlib,可整段搬进 server.py。
#
# 【答 MasterUI 4 问】
# A(scope):④⑤ 并行指标 = 只算【该组的组任务】busy(用 send_group 事件登记的 task id 集,权威、
#    不靠 id 前缀猜)。③ 负载份额 = 每成员认领的【组任务】数/busy 占比(看组活是否摊开)。
#    不混入成员的非组活(那属 fleet 级另算)。→ 组面板要的是"这个组的活摊没摊 / 并行没并行"。
# B(窗口):[now - window_seconds, now],now 用 server.py 的 time.time()(endpoint 在 server 里跑,可用)。
#    默认窗口给 24h;组活稀疏可放大到 7d。按 send 时间入窗(任务何时进组)。
# C(重叠合并+截断):每 role 收 [claim_ts, done_ts] 区间;无 done 的用 min(now, claim+cap) 兜底;
#    单区间 >1800s(30min)截到 30min(防 claimed 滞留污染);同 role 区间排序后合并重叠(一个 role
#    同刻只算 busy 一次)。
# D(≥1/≥2 扫线):所有 role 的 busy 区间打 +1/-1 边界,排序扫;累计 有≥1busy 时长 与 有≥2busy 时长。
#    有效并行系数 = Σ(各role busy时长) / 有≥1busy的墙钟时长(=1串行,→成员数理想)。
#    组内并行率 = 有≥2busy时长 / 有≥1busy时长。

import json
from datetime import datetime

_CAP_S = 1800   # 单任务 busy 上限截断(防 claimed 滞留把区间拉爆)

def _pt(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None

def group_metrics(events_path, group, now_epoch, window_seconds=24*3600):
    """返回该组并行指标。events_path=events.jsonl 路径;now_epoch=time.time();window 秒。
    返回 {members_load:[{role,claims,busy_s,share_pct}], effective_parallelism, intra_parallel_rate,
          active_wall_s, tasks_in_window, done_in_window}。"""
    cutoff = now_epoch - window_seconds
    group_task_ids = set()          # 该组的组任务 id(来自 send_group 事件)
    claim = {}                      # id -> (ts_epoch, role)
    done = {}                       # id -> ts_epoch
    for line in open(events_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        ev = e.get("event"); t = _pt(e.get("ts", ""))
        if t is None:
            continue
        if ev == "send_group" and e.get("group") == group and t >= cutoff:
            group_task_ids.add(e.get("id"))
        elif ev == "claim" and e.get("id"):
            claim.setdefault(e["id"], (t, e.get("role")))
        elif ev == "done" and e.get("id"):
            done.setdefault(e["id"], t)

    # 每 role 的 busy 区间(仅组任务)
    per_role = {}
    for tid in group_task_ids:
        if tid not in claim:
            continue
        ct, role = claim[tid]
        et = done.get(tid, min(now_epoch, ct + _CAP_S))
        if et <= ct:
            continue
        if et - ct > _CAP_S:
            et = ct + _CAP_S
        per_role.setdefault(role, []).append((ct, et))

    def merge(iv):
        iv = sorted(iv); out = []
        for s, e in iv:
            if out and s <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], e))
            else:
                out.append((s, e))
        return out

    role_busy = {r: merge(v) for r, v in per_role.items()}
    busy_s = {r: sum(e - s for s, e in v) for r, v in role_busy.items()}
    total_busy = sum(busy_s.values())

    # ≥1 / ≥2 并发扫线
    edges = []
    for r, v in role_busy.items():
        for s, e in v:
            edges.append((s, 1)); edges.append((e, -1))
    edges.sort()
    cur = 0; last = None; act1 = 0.0; act2 = 0.0
    for t, d in edges:
        if last is not None and cur > 0:
            act1 += (t - last)
            if cur >= 2:
                act2 += (t - last)
        cur += d; last = t

    # 负载份额(按 busy;组任务数也一并给)
    claims_ct = {r: len(per_role.get(r, [])) for r in per_role}
    members_load = sorted(
        ({"role": r, "claims": claims_ct[r], "busy_s": round(busy_s[r], 1),
          "share_pct": round(100 * busy_s[r] / total_busy, 1) if total_busy else 0.0}
         for r in per_role),
        key=lambda x: -x["busy_s"])
    return {
        "members_load": members_load,
        "effective_parallelism": round(total_busy / act1, 2) if act1 else 0.0,  # =1串行 →N理想
        "intra_parallel_rate": round(100 * act2 / act1, 1) if act1 else 0.0,     # ≥2并发时长占比%
        "active_wall_s": round(act1, 1),
        "tasks_in_window": len(group_task_ids),
        "done_in_window": sum(1 for i in group_task_ids if i in done),
    }

# 注:成员激活进度(members.json 的 members[] vs 预期)、组 inbox 深度(inbox/*.json 计数)、
# via_group 溯源 —— MasterUI 读文件自行实现(你说你自己搞①②⑥)。这段只覆盖 ③④⑤ 的口径。
