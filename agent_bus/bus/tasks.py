# -*- coding: utf-8 -*-
"""bus.tasks —— 长期任务线看板（tasklines）。ask-user/answer 待办池后续增量并入。

tasklines.json = 运行态磁盘文件（gitignore·不进 bus 核心 git=洁净基准[含业务明细+owner]；
落磁盘=防 compact+跨会话·Master 拍板存储方案A）。每条决策点带 ref → 对应 todos.json（ask-user 待办池）
条目，看板据 ref 显示 已答/待答。展示:阶段1=本 CLI(ab tasklines)；阶段2=WebUI 面板。

依赖：config(BUS)。cmd_tasklines 只读展示（交叉 todos.json 状态）。无反向依赖=无环。
"""
import json

from bus.config import BUS


def _tasklines_path():
    return BUS / "tasklines.json"

def _load_tasklines():
    p = _tasklines_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        tl = d.get("tasklines", []) if isinstance(d, dict) else []
        return tl if isinstance(tl, list) else []
    except Exception:
        return []

def _decision_status_map():
    """ref → todos.json 里对应条目的 status(决策点是否已被用户答/标完成)。"""
    f = BUS / "todos.json"
    m = {}
    try:
        items = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        for it in (items if isinstance(items, list) else []):
            r = it.get("ref")
            if r:
                m[r] = it.get("status")
    except Exception:
        pass
    return m

def cmd_tasklines(args):
    """【只读·长期任务线看板】列各 owner 的持续/长期任务线:状态/owner/节奏/待决策数/更新日。
    --id X 看单条详情(依赖/决策点/真相源)。数据源 tasklines.json(持久·跨会话·防compact)。
    决策点据 ref 交叉 todos.json 显示 已答/待答(决策走 ask-user 待办池,不滚走)。"""
    tls = _load_tasklines()
    dsm = _decision_status_map()
    _done = ("完成", "已完成", "done")
    if getattr(args, "json", False):
        print(json.dumps(tls, ensure_ascii=False, indent=1)); return
    if not tls:
        print("(长期任务线看板为空 · tasklines.json 无数据)"); return
    tid = getattr(args, "id", None)
    if tid:
        tl = next((t for t in tls if t.get("id") == tid), None)
        if not tl:
            print("无此任务线:%s(ab tasklines 看全部)" % tid); return
        print("═" * 72)
        print("【%s】%s" % (tl.get("id"), tl.get("title")))
        print("  owner=%s   节奏=%s   更新=%s" % (tl.get("owner"), tl.get("cadence"), tl.get("updated")))
        print("  状态:%s" % (tl.get("status") or ""))
        if tl.get("current"):
            print("  当前:%s" % tl.get("current"))
        if tl.get("deps"):
            print("  依赖:")
            for d in tl["deps"]:
                print("    - %s" % d)
        if tl.get("decisions"):
            print("  待决策点(→ 待办池):")
            for de in tl["decisions"]:
                stt = dsm.get(de.get("ref"))
                mark = "✓已答" if stt in _done else ("⧗待用户" if stt else "·未投池")
                print("    [%s] %s" % (mark, de.get("q")))
                if de.get("note"):
                    print("            ↳ %s" % de["note"])
        if tl.get("truth_source"):
            print("  真相源:%s" % tl.get("truth_source"))
        print("═" * 72)
        return
    print("━" * 78)
    print("长期任务线看板 · %d 条 · 数据源 tasklines.json(持久·跨会话·防compact)" % len(tls))
    print("━" * 78)
    for t in tls:
        decs = t.get("decisions", [])
        pending = sum(1 for d in decs if dsm.get(d.get("ref")) not in _done)
        dbadge = ("⧗待决策 %d/%d" % (pending, len(decs))) if decs else "—"
        print("【%s】%s" % (t.get("id"), t.get("title")))
        print("      owner=%-14s 节奏=%-16s 决策=%-10s 更新=%s"
              % (t.get("owner"), t.get("cadence"), dbadge, t.get("updated")))
        print("      状态:%s" % (t.get("status") or ""))
    print("━" * 78)
    print("详情:ab tasklines --id <TL-x>   ·   决策点走 ask-user 待办池(ab stale / WebUI 可看)")
