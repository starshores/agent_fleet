# -*- coding: utf-8 -*-
"""bus.agent_data —— per-agent 数据收集/画像(L1-L3·用户任务 2026-09-16"收集每个agent的数据并利用其自我迭代")。

口径 owner=Master数分(4维指标·数据真实性红线·768d38/424c49 schema 实核);机制=MasterAgent。
架构(Master数分):L1 原子采集(只读旁路)→ L2 指标派生(硬指标直算·软信号标旗不混)→ L3 画像聚合
(per-agent 卡)→ L4 画像异常→探针深查→迭代候选(消费侧·非本模块)。

★数据真实性红线(Master数分 5 条·全遵):
  ①每指标可溯源客观事件(usage.db/events/archive/账本)·不从感觉派生;②硬指标 vs 软信号分层;
  ③画像=自我发现工具【非 KPI】(中性呈现·防 game);④只读旁路(不改被测状态);
  ⑤缺数据显"无数据"·不补 0 冒充表现差。

本批=维度2 纪律 + 维度4 产出(schema 已核·424c49 unblock);维度1(需 send --kind)次批;
维度3 假执行(需Master数分核查判据)末批。

数据源(全部现成·零新采集点):
  usage.db.messages(token/cost·per session_id)⋈ session_role_map.json(sid→role 账本·2026-08-03)
  events.jsonl(done/claim 计数·per role)
  <role>/archive+done_archive(掉线催单 from=bus守卫 / 回执时延 notice created→finished)

命令:ab agent-profile [--role X] [--days N] [--json]
"""
import json, os, re, glob, time, sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from bus.config import BUS, cfg, _now


def _t(s):
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def _sid_role_map():
    """L1:sid→role(账本 session_role_map.json + 各 presence 当前绑定补漏)。
    ★权威序(Master数分终验 doc note 2026-09-17):【账本优先·presence 只补账本没有的 sid】(setdefault=
    不覆盖账本已有绑定)——账本 append-only 留史=审计权威;presence 是当前态易被 rebind 覆盖·
    仅用于补"hello 过但账本机制上线前的旧会话"缺口。归属可复现=以账本为准。"""
    m = {}
    try:
        d = json.loads((BUS / "session_role_map.json").read_text(encoding="utf-8"))
        for sid, v in d.items():
            r = v.get("role") if isinstance(v, dict) else v
            # ephemeral dispatch 代号(src=dispatch-adhoc)并 _adhoc 桶不单列(Master数分 Q1 口径 eeb4e5)
            if isinstance(v, dict) and v.get("src") == "dispatch-adhoc":
                r = "_adhoc"
            if r:
                m[sid] = r
    except Exception:
        pass
    for pf in BUS.glob("*/presence.json"):
        try:
            p = json.loads(pf.read_text(encoding="utf-8"))
            if p.get("session_id") and p.get("role"):
                m.setdefault(p["session_id"], p["role"])
        except Exception:
            continue
    return m


def _proj_slug(p):
    """路径→CC projects 目录名转义(C:\\apphome\\agent_fleet → C--apphome-master-agent)。"""
    return re.sub(r"[^A-Za-z0-9]", "-", str(p or ""))


def _bus_domains():
    """bus 项目域集(Master数分 Q2 判据·全从数据派生零硬编码=换域不改平台代码):
    agent_fleet home + default_worker_cwd + 各在册 role presence.cwd 的项目域转义。"""
    doms = set()
    for p in (cfg("agent_fleet_home") or str(Path(__file__).resolve().parents[2]),
              cfg("default_worker_cwd")):
        if p:
            doms.add(_proj_slug(p))
    for pf in BUS.glob("*/presence.json"):
        try:
            c = json.loads(pf.read_text(encoding="utf-8")).get("cwd")
            if c:
                doms.add(_proj_slug(c))
        except Exception:
            continue
    return doms


def _sid_domain(sid):
    """会话 sid → 所在 CC projects 项目域(可溯源判据=会话 jsonl 物理位置);找不到返 None(模糊)。"""
    hits = glob.glob(str(Path.home() / ".claude" / "projects" / "*" / (sid + ".jsonl")))
    return os.path.basename(os.path.dirname(hits[0])) if hits else None


def _usage_by_role(since_date, m):
    """L1+L2 维度4:usage.db ⋈ sid→role → per-role {calls,input,output,cache_read,cost}。
    只读;无归属 session 三分桶(Master数分 Q2 口径 eeb4e5·判据=会话 jsonl 项目域·可溯源):
    '_non_bus'(项目域∉bus 域集=个人/非bus会话·排除主视图防 bus 指标膨胀)/
    '_unattributed'(bus 域或域模糊=真缺口·缺显缺;模糊留缺>假精度)。_adhoc 由账本 src 判(见 _sid_role_map)。"""
    out = defaultdict(lambda: Counter())
    dbp = cfg("usage_db") or str(BUS / "usage.db")
    busdoms = _bus_domains()
    try:
        db = sqlite3.connect("file:%s?mode=ro" % dbp.replace("\\", "/"), uri=True)
        cur = db.execute(
            "select session_id, count(*), sum(input), sum(output), sum(cache_read), sum(cost_usd)"
            " from messages where sh_date >= ? group by session_id", (since_date,))
        for sid, calls, inp, outp, cr, cost in cur:
            role = m.get(sid)
            if not role:
                dom = _sid_domain(sid)
                role = "_non_bus" if (dom and dom not in busdoms) else "_unattributed"
            c = out[role]
            c["calls"] += calls or 0; c["input"] += inp or 0; c["output"] += outp or 0
            c["cache_read"] += cr or 0; c["cost_usd"] += int((cost or 0) * 100)  # 分存·避浮点累加
        db.close()
    except Exception as e:
        out["_error"]["usage_db"] = 1
    return out


def _events_by_role(since_epoch):
    """L1+L2 维度2:events.jsonl → per-role done/claim/send 计数(硬指标·源=审计事件)。"""
    out = defaultdict(Counter)
    ev = BUS / "events.jsonl"
    if not ev.exists():
        return out
    for ln in ev.read_text(encoding="utf-8", errors="replace").split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except Exception:
            continue
        ts = _t(e.get("ts"))
        if since_epoch and ts and ts < since_epoch:
            continue
        k = e.get("event")
        if k == "done" and e.get("role"):
            out[e["role"]]["done"] += 1
        elif k == "claim" and e.get("role"):
            out[e["role"]]["claim"] += 1
        elif k == "send" and (e.get("frm") or e.get("from")):
            out[e.get("frm") or e.get("from")]["send"] += 1
        elif k == "enforce_selfwake_block" and e.get("role"):
            out[e["role"]]["stop_hook_block"] += 1
    return out


def _archive_metrics(since_epoch):
    """L1+L2 维度2:全库 archive 扫 → per-role {掉线催单数, 回执时延列表}(路径=掉线分析实证·Master数分复核 SOUND)。"""
    drops = Counter()
    lat = defaultdict(list)
    for rd in os.listdir(BUS):
        base = BUS / rd
        if not base.is_dir() or rd.startswith((".", "_")):
            continue
        for sub in ("archive", "done_archive", "processing", "inbox"):
            d = base / sub
            if not d.is_dir():
                continue
            for f in glob.glob(str(d / "*.json")):
                try:
                    o = json.loads(open(f, encoding="utf-8").read())
                except Exception:
                    continue
                cts = _t(o.get("created"))
                if since_epoch and cts and cts < since_epoch:
                    continue
                if o.get("from") == "bus守卫" and "重挂" in (o.get("title") or ""):
                    drops[rd] += 1          # 掉线催单(per-role 归属=所在 role 目录·Master数分复核过等价 .to)
                if o.get("kind") == "notice" and o.get("finished") and cts:
                    fts = _t(o.get("finished"))
                    if fts and fts >= cts:
                        lat[rd].append(fts - cts)   # 回执时延=created→done
    return drops, lat


def _pct(sorted_list, p):
    return sorted_list[min(len(sorted_list) - 1, int(len(sorted_list) * p))] if sorted_list else None


def build_profile(days=30, role=None):
    """纯函数 API(供 WebUI /api/agent-profile 端点调·2026-09-17 MasterUI 接 L3):返回画像 dict·零 print。
    cmd_agent_profile=其 CLI 壳。契约字段见 MasterUI 设计(as_of/window_days/note/unattributed_usage/agents[])。"""
    class _A:
        pass
    a = _A(); a.days = days; a.role = role; a.json = None; a._quiet = True
    return _build(a)


def cmd_agent_profile(args):
    """L3 画像聚合 CLI:per-agent 卡(维度2 纪律 + 维度4 产出·硬指标)。--role 单看;--days 窗口(默认 30)。
    中性呈现=自我发现工具非 KPI(Master数分红线③);缺数据显'无数据'(红线⑤)。"""
    result = _build(args)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=1, default=int)); return result
    print("per-agent 画像(近 %d 天·硬指标·非KPI)" % result["window_days"])
    for c in result["agents"]:
        d4 = c["维度4_产出"]; d2 = c["维度2_纪律"]
        t4 = ("calls %s·out %s tok" % (d4.get("calls"), d4.get("output_tokens"))) if isinstance(d4, dict) else d4
        print("  %-12s 产出[%s] 纪律[done %s·claim %s·send %s·掉线 %s·回执中位 %s s(n=%s)]"
              % (c["role"], t4, d2["done"], d2["claim"], d2["send"], d2["掉线催单"],
                 d2["回执时延中位秒"], d2["回执样本数"]))
    if result["unattributed_usage"]:
        print("  (未归属会话 usage:%s —— 缺显缺·不摊派)" % result["unattributed_usage"])
    if result["non_bus_usage"]:
        print("  (非bus会话 usage:%s —— 个人/其他项目·不算bus消耗)" % result["non_bus_usage"])
    if result["adhoc_usage"]:
        print("  (ephemeral dispatch usage:%s —— 一次性代号合桶)" % result["adhoc_usage"])
    return result


def _build(args):
    days = int(getattr(args, "days", None) or 30)
    since_epoch = time.time() - days * 86400
    since_date = time.strftime("%Y-%m-%d", time.localtime(since_epoch))
    m = _sid_role_map()
    usage = _usage_by_role(since_date, m)
    evs = _events_by_role(since_epoch)
    drops, lat = _archive_metrics(since_epoch)

    roles = set(usage) | set(evs) | set(drops) | set(lat)
    # _ 前缀桶(_unattributed/_non_bus/_adhoc/_error)与 __ 测试角色都不入 per-agent 卡:
    # 画像是 per-【稳定 role】·桶走 summary 字段 labeled 可见(Master数分 Q1/Q2 口径·缺显缺不藏不摊派)
    roles = {r for r in roles if not r.startswith("_")}
    only = getattr(args, "role", None)
    if only:
        roles = {r for r in roles if r == only} or {only}

    cards = []
    for r in sorted(roles):
        u = usage.get(r) or {}
        e = evs.get(r) or {}
        ls = sorted(lat.get(r) or [])
        card = {
            "role": r, "window_days": days,
            "维度4_产出": {
                "calls": u.get("calls") or None, "output_tokens": u.get("output") or None,
                "input_tokens": u.get("input") or None, "cache_read": u.get("cache_read") or None,
            } if u else "无数据(usage 无归属会话)",
            "维度2_纪律": {
                "done": e.get("done") or 0, "claim": e.get("claim") or 0, "send": e.get("send") or 0,
                "掉线催单": drops.get(r, 0), "stop_hook_block": e.get("stop_hook_block") or 0,
                "回执时延中位秒": _pct(ls, .5), "回执时延P90秒": _pct(ls, .9), "回执样本数": len(ls),
            },
        }
        cards.append(card)

    result = {"as_of": _now(), "window_days": days, "note": "硬指标·只读旁路·中性呈现非KPI(Master数分红线)·维度1/3待次批",
              "unattributed_usage": dict(usage.get("_unattributed") or {}) or None,
              "non_bus_usage": dict(usage.get("_non_bus") or {}) or None,     # 个人/非bus会话·不算bus消耗(Q2)
              "adhoc_usage": dict(usage.get("_adhoc") or {}) or None,         # ephemeral dispatch 代号合桶(Q1)
              "agents": cards}
    return result
