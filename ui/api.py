#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.api —— WebUI 数据/API 层(BUS 2.0 P4 · Phase C · 2026-07-20)。

抽自 server.py:全部 build_*/get_*/compute_* 数据产出 + 各簇缓存/锁 + refresher 线程启动
+ 通知/消息/fleet_status 推送 + send/clear + todos + live/usage/sessions/ctx/who/prompts/
tasklines/groups/mechanisms/longrange/archive。契约铁律(P4):纯搬运,逻辑/端点/口径不变。
server.py 以 facade 方式把本模块全部名字并入其命名空间(Handler/main/子进程裸名引用不变,
含 SSE 用的 _change_cv/_bump 等下划线名 + 各缓存对象)。
"""
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from common import ABDIR, ab, BUS  # noqa: F401
from common import _roles, _count, _read_tasks, _safe_cwd, _turn_state, _vclass_of  # noqa: F401


def compute_fleet():
    """舰队总览 —— 2a 步骤3(2026-07-20·灭 role-verdict 4处分叉之 WebUI 侧):
    verdict/present/alive/mode/pid/cwd/pseudo/retired/parked 等【状态判定】权威源 = ab._who_data()
    (与 who/selfwake 同一单一源,不再本地自算 verdict)。本函数只做 UI 派生:
      · vclass 显示类(_vclass_of)· pid_display · 任务计数(inbox…)· turn · self_wake(state+detail)。
    self_wake 是与 verdict 正交的另一维度、其单一源=ab._self_wake_state(此处调它取 detail 供 tooltip,非 verdict 分叉);
    伪角色/退役无自醒态(按 _who_data 口径抑制)。"""
    wd = ab._who_data()
    psutil_ok = bool(wd.get("psutil"))
    alive = set(wd.get("alive_claude") or [])                 # 复用 _who_data 已扫的 alive(不重扫)
    claimed = set(wd.get("claimed_pids") or [])
    sw_roles = ab._wait_proc_roles() if psutil_ok else set()  # 供 _scan(fleet_status 推送复用)+ 自醒 detail
    rows = []
    for rec in wd.get("roles", []):
        r = rec["role"]
        # 自醒态(与 verdict 正交):伪角色/退役无自醒(_who_data 口径),其余取单一源 _self_wake_state
        if rec.get("pseudo") or rec.get("retired"):
            sw_state, sw_detail = None, ""
        else:
            sw_state, sw_detail = ab._self_wake_state(r, alive, sw_roles)
        # pid_display 派生
        if rec.get("mode") == "daemon":
            pid_display = "d:%s" % (rec.get("daemon_pid") or "?")
        elif rec.get("claude_pid"):
            pid_display = str(rec.get("claude_pid"))
        else:
            pid_display = None
        row = {
            "role": r,
            "inbox": _count(r, "inbox"),
            "processing": _count(r, "processing"),
            "done": _count(r, "done"),
            "held": _count(r, "held"),      # 高风险待批(requires_approval 留 inbox;held 为显式批前暂存)
            "failed": _count(r, "failed"),  # 毒任务(reap 超 max-requeue)
            "pid": rec.get("claude_pid"), "pid_display": pid_display,
            "age": rec.get("age_s"), "mode": rec.get("mode"), "cwd": rec.get("cwd"),
            "started": None, "host": None,
            "verdict": rec.get("verdict") or "未声明(无 presence)",   # ← 权威源(灭本地自算)
            "vclass": _vclass_of(rec),                                  # 派生显示类
            "self_wake": sw_state, "self_wake_detail": sw_detail,
            "turn": _turn_state(r),   # 实时 turn 状态(hook 盖):thinking/tool/idle + 时长
        }
        if rec.get("parked"):
            row["parked"] = True
        if rec.get("pseudo"):
            row["pseudo"] = True
        rows.append(row)

    # 在跑但未认领的 claude 进程:_who_data 已分好(tagged/unclaimed_pids),此处仅补 cwd(display 层)
    tags = ab._load_pidtags()
    tagged, unknown = [], []
    for t in (wd.get("tagged") or []):
        pid = t.get("pid")
        if t.get("private"):
            # opt-in 隐藏(用户显式 pid_tags private=true):只 pid+label,不探 cwd/细节。
            tagged.append({"pid": pid, "label": t.get("label", "?"), "private": True})
        else:
            tagged.append({"pid": pid, "label": t.get("label", "?"),
                           "private": False, "cwd": _safe_cwd(pid)})
    for pid in (wd.get("unclaimed_pids") or []):
        unknown.append({"pid": pid, "cwd": _safe_cwd(pid)})

    return {
        "psutil": psutil_ok,
        "roles": rows,
        "tagged": tagged,
        "unknown": unknown,
        "alive_count": len(alive),
        "fleet_count": len(claimed),
        # 原始扫描结果随缓存带出:让 fleet_status 推送复用,避免它在主进程内再扫一次(GIL)。
        "_scan": {"alive": sorted(alive), "wait_roles": sorted(sw_roles)},
    }


# compute_fleet 每次跑 ab._alive_claude_pids()(全进程扫描),本机进程多 ~4s。
# SSE/轮询频繁打 /api/state → 卡。加短 TTL(3s)内存缓存:窗口内直接返回上次结果,
# 全进程扫描最多每 3s 一次。compute 放锁内 → 同一窗口的并发请求只扫一次,其余等到后拿新缓存。
# 全进程扫描 ab._alive_claude_pids() 在本机 0.1~17s 剧烈波动(进程多)。绝不能在请求里同步阻塞,
# 也不能持锁现算(否则一次冷扫把所有 /api/state 卡死 17s)。
# 用 stale-while-revalidate:永远秒回缓存,过期就后台线程刷新,不阻塞任何请求。
_FLEET_TTL = 30.0
_fleet_cache = {"ts": 0.0, "data": None}
_fleet_lock = threading.Lock()
_fleet_refreshing = {"v": False}


# 把 ~2s 的全进程扫描搬到【子进程】跑(独立 GIL)——否则它在 server 进程内的后台线程里
# 霸占 GIL 2s,正在服务的 /api/state 请求线程被饿死 → 偶发 2-4s → 前端超时空白。
# 子进程 import 本模块(main() 在 __main__ 卫下,import 不会起服务),调 compute_fleet 输出 JSON。
_FLEET_SUBPROC = (
    "import sys,json;"
    "sys.path.insert(0, r'%s');"
    "sys.stdout.reconfigure(encoding='utf-8', errors='replace');"
    "import server;"
    "print(json.dumps(server.compute_fleet(), ensure_ascii=False))"
) % os.path.dirname(os.path.abspath(__file__))

# 关键:server 由 pythonw.exe 启动(WMI 常驻),pythonw 丢弃 stdout → 子进程 print 抓不到 →
# 误判失败 → 退回线程内同步扫描 → 2s GIL 饿死请求(就是 /api/state 冷/过期时卡 2-5s 的真因)。
# 故子进程显式用 python.exe(有 stdout 管道),不要用 sys.executable(可能是 pythonw)。
_PYEXE = os.path.join(os.path.dirname(sys.executable), "python.exe")
if not os.path.exists(_PYEXE):
    _PYEXE = sys.executable

# Windows:用 python.exe(有 stdout 管道)起子进程会弹控制台窗口 → 刷屏用户。
# CREATE_NO_WINDOW 隐藏控制台,stdout 仍走 capture_output 的管道(两者兼得)。
# 非 Windows 无此标志 → getattr 兜底为 0(无影响)。所有 subprocess 统一带上。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _refresh_fleet():
    try:
        data = None
        try:
            r = subprocess.run([_PYEXE, "-c", _FLEET_SUBPROC],
                               capture_output=True, timeout=60, creationflags=_NO_WINDOW)
            out = r.stdout.decode("utf-8", "replace").strip()
            if r.returncode == 0 and out:
                data = json.loads(out)
        except Exception:
            data = None
        # 子进程失败:只有【缓存还完全空】时才退回线程内算一次(否则保留 stale、下个 tick 再试),
        # 避免在请求繁忙期反复在主进程做 2s 扫描而饿死请求线程。
        if data is None:
            with _fleet_lock:
                have = _fleet_cache["data"] is not None
            if have:
                return   # 保留上次 stale,不在主进程扫
            data = compute_fleet()   # 冷启且子进程失败的最后兜底(极少)
        with _fleet_lock:
            _fleet_cache["data"] = data
            _fleet_cache["ts"] = time.time()
    except Exception:
        pass
    finally:
        _fleet_refreshing["v"] = False


def compute_fleet_cached(force=False):
    # 请求线程【只读缓存,永不触发刷新】——连 force 也不触发。
    # 关键教训:即便刷新走子进程,发起刷新的【那条请求线程】后续在 build_state 里的磁盘读
    # 会和子进程启动(import server + 2s 扫描的 CPU)抢资源 → 那一条冷请求被拖到 ~1.7s。
    # 故保活完全交给 _fleet_refresher 后台线程(它阻塞 ~4s 不影响任何请求);请求路径零扫描、零 spawn。
    with _fleet_lock:
        data = _fleet_cache["data"]
    if data is None:
        # 冷启:刷新线程还没填上 → 立即回 loading 占位(前端保留上次/显示加载中),不阻塞
        return {"psutil": ab.psutil is not None, "roles": [], "tagged": [],
                "unknown": [], "alive_count": 0, "fleet_count": 0, "loading": True}
    return data  # 立即返回缓存(刷新线程保证 ≤interval 新鲜),绝不阻塞


# =============================================================
#  bus 托盘/健康指示器计数 —— 单一源(与运维面板同吃 compute_fleet_cached 缓存,口径不漂移)。
#  由 bus_tray.py(系统托盘)与本 WebUI 共用:数据口径归 Master数分2、渲染/机制归 MasterUI。
#  用户 2026-07-14 指名把托盘交 UI 维护,并要求托盘实时/口径对齐运维面板。
# =============================================================
_TRAY_RUN_TTL = int(os.environ.get("BUS_TRAY_RUN_TTL", "3600"))  # processing claim 超此秒=僵尸,不计在跑


def _parse_claimed_epoch(s):
    """bus 的 claimed 是本机本地时间 ISO(无时区)→ 本地时间戳(与 bus_tray 口径一致)。"""
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def bus_tray_counts():
    """运转指示计数(单一源=运维面板同款 compute_fleet_cached 缓存):
      在岗N = 进程活着的真角色(vclass live/idle/warn·排除 dead/none/parked);
      静默K = 在岗里 verdict 含"静默"(是 N 的子集);
      休眠P = parked(故意暂离·不计在岗);
      在跑M = processing/ 里 non-notice claim 新鲜(<RUN_TTL)且 owner 在岗 → 真执行;
      卡死stale = processing/ 里超 RUN_TTL 的 non-notice 僵尸 claim(rot·单列告警不计在跑)。
    不用 who.current_task 当在跑源(该字段滞留在已卡死旧 claim 上→虚报在跑·Master数分2 口径坑①)。"""
    fl = compute_fleet_cached()
    roles = fl.get("roles", []) or []
    loading = bool(fl.get("loading"))
    alive_names, n, k, p = set(), 0, 0, 0
    for r in roles:
        vc = r.get("vclass")
        if r.get("parked") or vc == "parked":
            p += 1
            continue
        if vc in ("live", "idle", "warn"):
            n += 1
            alive_names.add(r.get("role"))
            if "静默" in (r.get("verdict") or ""):
                k += 1
    now = time.time()
    running, stale = set(), 0
    for d in glob.glob(str(BUS / "*")):
        if not os.path.isdir(d):
            continue
        role = os.path.basename(d)
        if role.startswith("_") or role in ("shared",):
            continue
        for f in glob.glob(os.path.join(d, "processing", "*.json")):
            if "notice-" in os.path.basename(f):
                continue
            ts = None
            try:
                ts = _parse_claimed_epoch(json.loads(Path(f).read_text(encoding="utf-8")).get("claimed", ""))
            except Exception:
                ts = None
            if ts is None:
                try:
                    ts = os.path.getmtime(f)
                except Exception:
                    ts = now
            if now - ts < _TRAY_RUN_TTL:
                if role in alive_names:
                    running.add(role)
            else:
                stale += 1
    return {"ok": True, "loading": loading, "present": n, "running": len(running),
            "silent": k, "parked": p, "stale": stale,
            "as_of": time.strftime("%H:%M:%S"),
            "source": "webui-fleet-cache"}


def start_fleet_refresher(interval=20):
    """唯一的舰队刷新者:后台线程,每 interval 秒跑一次子进程扫描刷新 _fleet_cache。
    - 子进程扫描在独立 GIL,本线程阻塞 ~4s 完全不影响 HTTP 请求线程。
    - interval(20s) < _FLEET_TTL(30s):请求永远命中尚未过期的缓存,无需自行刷新。
    - 首轮立即跑 → 启动 ~4s 后缓存就绪(替代原 prewarm 的 compute_fleet_cached)。"""
    stop = threading.Event()

    def loop():
        while True:
            _refresh_fleet()
            if stop.wait(interval):
                break

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[fleet] 后台舰队刷新线程已启动,间隔 %ds(子进程扫描,主进程/请求零扫描)" % interval)
    return t


# =============================================================
#  bus 健康徽标 —— 吃 `agentbus health --json`(内部 psutil 全扫 ~1-2s)。
#  同 fleet:绝不在请求线程现扫 —— 后台线程跑子进程刷新缓存,/api/health 只读缓存。
# =============================================================
_health_cache = {"ts": 0.0, "data": None}
_health_lock = threading.Lock()


def _refresh_health():
    try:
        r = subprocess.run([_PYEXE, str(ABDIR / "agentbus.py"), "health", "--json"],
                           capture_output=True, timeout=60, creationflags=_NO_WINDOW)
        out = r.stdout.decode("utf-8", "replace").strip()
        if r.returncode == 0 and out:
            data = json.loads(out)
            with _health_lock:
                _health_cache["data"] = data
                _health_cache["ts"] = time.time()
    except Exception:
        pass   # 失败保留上次缓存,下个 tick 再试(绝不退回请求线程现扫)


def get_health():
    with _health_lock:
        return _health_cache["data"]


def start_health_refresher(interval=25):
    """唯一的健康刷新者:后台线程每 interval 秒跑一次子进程 `health --json` 刷新缓存。
    健康态不需秒级,25s 足够;子进程独立 GIL,不饿死请求。首轮立即跑。"""
    stop = threading.Event()

    def loop():
        while True:
            _refresh_health()
            if stop.wait(interval):
                break

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[health] 后台健康刷新线程已启动,间隔 %ds(子进程 health --json,请求零扫描)" % interval)
    return t


# =============================================================
#  BI 资源消耗模块 —— token / 成本(metrics.jsonl)+ 系统资源(psutil)
# =============================================================
_proc_cache = {}   # pid -> psutil.Process(复用,使 cpu_percent 能算两次调用间增量)


def _proc_tree_usage(root_pid):
    """取一个根 pid 整棵进程树(自身+递归子)的 CPU% 与 RSS 之和。
    daemon_pid 是常驻 python(多空闲),它跑任务时 fork 的 claude -p 子进程才吃资源 → 算整树更真实。
    cpu_percent(None) 非阻塞:返回自【上次对该对象调用】以来的占用,首次见到的进程返回 0。"""
    if ab.psutil is None or not root_pid:
        return None
    try:
        root = _proc_cache.get(root_pid)
        if root is None or not root.is_running():
            root = ab.psutil.Process(root_pid)
            _proc_cache[root_pid] = root
        procs = [root]
        try:
            procs += root.children(recursive=True)
        except Exception:
            pass
        cpu = 0.0
        rss = 0
        n = 0
        for p in procs:
            try:
                cp = _proc_cache.get(p.pid)
                if cp is None:
                    cp = p
                    _proc_cache[p.pid] = p
                cpu += cp.cpu_percent(None)
                rss += cp.memory_info().rss
                n += 1
            except Exception:
                continue
        return {"cpu": round(cpu, 1), "rss_mb": round(rss / 1048576, 1), "nproc": n}
    except Exception:
        return None


def _local_day_start(epoch):
    lt = time.localtime(epoch)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, lt.tm_wday, lt.tm_yday, -1))


def _ev_epoch(ts):
    """events.jsonl 的 '%Y-%m-%dT%H:%M:%S'(本地时区写入)→ epoch 秒。解析不出返回 None。"""
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def _is_test_role(r):
    return (not r) or r.startswith("__") or r.startswith("zz") or r in ("?",)


def build_activity():
    """从 events.jsonl 重建【任务活动历史】(全程,无 token/成本)。
    token/成本只有埋点后的前向真实数据,历史无从恢复,故活动单列、不混入消耗 BI。
    claim→done 按 id 配对算时长;过滤 __*/zz* 等测试角色。"""
    ev = BUS / "events.jsonl"
    if not ev.exists():
        return {"roles": [], "total": 0, "span": None}
    try:
        lines = ev.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return {"roles": [], "total": 0, "span": None}
    recs = []
    for ln in lines:
        ln = ln.strip()
        if ln:
            try:
                recs.append(json.loads(ln))
            except Exception:
                pass
    claim_ts = {}
    for r in recs:
        if r.get("event") == "claim" and r.get("id"):
            claim_ts[r["id"]] = _ev_epoch(r.get("ts", ""))
    now = time.time()
    h24 = now - 86400
    per = {}
    for r in recs:
        if r.get("event") not in ("done", "dispatch_done"):
            continue
        role = r.get("role", "?")
        if _is_test_role(role):
            continue
        dt = _ev_epoch(r.get("ts", ""))
        d = per.setdefault(role, {"role": role, "tasks": 0, "ok": 0, "err": 0,
                                  "tasks24": 0, "_durs": [], "_last": None})
        d["tasks"] += 1
        if r.get("status") == "error":
            d["err"] += 1
        else:
            d["ok"] += 1
        if dt and dt >= h24:
            d["tasks24"] += 1
        ct = claim_ts.get(r.get("id"))
        if ct and dt and dt >= ct:
            d["_durs"].append(dt - ct)
        if dt and (d["_last"] is None or dt > d["_last"]):
            d["_last"] = dt
    out = []
    for d in per.values():
        durs = d.pop("_durs")
        last = d.pop("_last")
        d["avg_dur_s"] = round(sum(durs) / len(durs), 1) if durs else 0
        d["last_active"] = time.strftime("%m-%d %H:%M", time.localtime(last)) if last else "-"
        out.append(d)
    out.sort(key=lambda x: -x["tasks"])
    span = None
    done_ts = [_ev_epoch(r.get("ts", "")) for r in recs if r.get("event") in ("done", "dispatch_done")]
    done_ts = [t for t in done_ts if t]
    if done_ts:
        span = {"from": time.strftime("%m-%d %H:%M", time.localtime(min(done_ts))),
                "to": time.strftime("%m-%d %H:%M", time.localtime(max(done_ts)))}
    return {"roles": out, "total": sum(x["tasks"] for x in out), "span": span}


def _agg_metrics(rows):
    d = {"tasks": 0, "tok_in": 0, "tok_out": 0, "cache_read": 0, "cost": 0.0, "dur_ms": 0, "errors": 0}
    for r in rows:
        d["tasks"] += 1
        d["tok_in"] += r.get("tokens_in", 0) or 0
        d["tok_out"] += r.get("tokens_out", 0) or 0
        d["cache_read"] += r.get("cache_read", 0) or 0
        d["cost"] += r.get("cost_usd", 0) or 0
        d["dur_ms"] += r.get("duration_ms", 0) or 0
        if r.get("status") == "error":
            d["errors"] += 1
    d["cost"] = round(d["cost"], 4)
    d["tok_total"] = d["tok_in"] + d["tok_out"]
    d["avg_dur_s"] = round(d["dur_ms"] / 1000.0 / d["tasks"], 1) if d["tasks"] else 0
    return d


def build_bi():
    """运营总览口径:今日/近24h 全队总消耗 + 各角色排名(近24h)+ 当前在跑 + 系统资源快照。"""
    now = time.time()
    day0 = _local_day_start(now)
    h24 = now - 86400
    recs = ab.read_metrics()
    today = [r for r in recs if r.get("t", 0) >= day0]
    last24 = [r for r in recs if r.get("t", 0) >= h24]

    per = {}
    for r in last24:
        per.setdefault(r.get("role", "?"), []).append(r)

    per_role = []
    for role in sorted(set(list(per.keys()) + _roles())):
        a = _agg_metrics(per.get(role, []))
        sysu = None
        pf = BUS / role / "presence.json"
        if pf.exists():
            try:
                pr = json.loads(pf.read_text(encoding="utf-8"))
                pid = pr.get("daemon_pid") if pr.get("mode") == "daemon" else pr.get("claude_pid")
                sysu = _proc_tree_usage(pid)
            except Exception:
                pass
        if a["tasks"] == 0 and not sysu:
            continue                      # 既无近24h活动、又无活进程 → 减噪不列
        a["role"] = role
        a["sys"] = sysu
        per_role.append(a)
    per_role.sort(key=lambda x: (-x["cost"], -x["tok_total"]))

    running = []
    for role in _roles():
        pf = BUS / role / "presence.json"
        if not pf.exists():
            continue
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if pr.get("current_task"):
            running.append({"role": role, "task": pr.get("current_task"),
                            "title": pr.get("current_title") or ""})

    # bus 总占用内存:各角色进程树 RSS/CPU 合计(用户令·2026-07-02)
    sys_total = None
    if ab.psutil is not None:
        _srss = sum((r["sys"].get("rss_mb") or 0) for r in per_role if r.get("sys"))
        _scpu = sum((r["sys"].get("cpu") or 0) for r in per_role if r.get("sys"))
        _sn = sum((r["sys"].get("nproc") or 0) for r in per_role if r.get("sys"))
        _sroles = sum(1 for r in per_role if r.get("sys"))
        sys_total = {"rss_mb": round(_srss, 1), "cpu": round(_scpu, 1), "nproc": _sn, "roles": _sroles}

    # 今日消耗:改接 usage.db(全量·含前台 cli),取代已随 daemon 移除而冻结的 metrics 口径(统一运营口径·2026-07-02)
    today_usage = None
    try:
        _u = get_usage(force=False)          # 复用带 TTL 缓存的 token_stats json
        _daily = _u.get("daily") or []
        if _daily:
            _last = _daily[-1]
            _tstr = time.strftime("%Y-%m-%d", time.gmtime(now + 8 * 3600))  # Shanghai(UTC+8)显式·换机不漂(MasterUI零基评估挑出·2026-07-02)
            _row = _last if _last.get("date") == _tstr else None
            _src = _row or _last
            today_usage = {
                "date": _src.get("date"), "is_today": bool(_row),
                "tokens_total": _src.get("tokens_total") or 0,
                "tokens_io": _src.get("tokens_io") or 0,
                "cost_usd": _src.get("cost_usd") or 0,
                "calls": _src.get("calls") or 0,
                "input": _src.get("input") or 0, "output": _src.get("output") or 0,
                "as_of": _u.get("as_of"),
            }
    except Exception:
        today_usage = None

    return {
        "now": time.strftime("%H:%M:%S", time.localtime(now)),
        "today_usage": today_usage,          # ← usage.db 活数据(运营/资源新口径)
        "sys_total": sys_total,              # ← bus 总占用内存/CPU 合计
        "per_role": per_role,
        "running": running,
        "psutil": ab.psutil is not None,
    }


# =============================================================
#  派工/协作效率面板 —— bus 自动化派工埋点(metrics.jsonl)
#  口径铁律:仅覆盖【自动化派工】(后台 daemon + dispatch 一次性任务),
#  绝不含交互式 tab 会话 —— 实测同期它只占真实总成本 ≈0.5%。
#  故此面板【绝不是总账】;总成本看「历史用量」(token_stats)。两块各司其职。
#  数据 = ab.read_metrics()(metrics.jsonl 尾部,坏行已跳过),只读;短 TTL 缓存仿 /api/usage。
# =============================================================
_DISPATCH_TTL = 20.0
_dispatch_cache = {"ts": 0.0, "data": None}
_dispatch_lock = threading.Lock()


def _build_dispatch_metrics():
    """聚合自动化派工埋点:总账 KPI + 各 role + daemon/dispatch 拆分 + Top 耗时任务。"""
    def num(r, k):
        return r.get(k, 0) or 0

    recs = ab.read_metrics()
    total = {"tasks": 0, "cost": 0.0, "turns": 0, "dur_ms": 0,
             "tok_in": 0, "tok_out": 0, "errors": 0, "daemon": 0, "dispatch": 0}
    per_role = {}
    by_kind = {"daemon": {"kind": "daemon", "tasks": 0, "cost": 0.0, "turns": 0, "dur_ms": 0},
               "dispatch": {"kind": "dispatch", "tasks": 0, "cost": 0.0, "turns": 0, "dur_ms": 0}}
    tasks = []
    span_min = span_max = None

    for r in recs:
        kind = r.get("kind") or "daemon"
        if kind not in by_kind:
            kind = "daemon"
        role = r.get("role") or "?"
        cost = float(num(r, "cost_usd"))
        turns = int(num(r, "num_turns"))
        dur = int(num(r, "duration_ms"))

        total["tasks"] += 1
        total["cost"] += cost
        total["turns"] += turns
        total["dur_ms"] += dur
        total["tok_in"] += int(num(r, "tokens_in"))
        total["tok_out"] += int(num(r, "tokens_out"))
        total[kind] += 1
        if r.get("status") == "error":
            total["errors"] += 1

        d = per_role.setdefault(role, {"role": role, "tasks": 0, "cost": 0.0,
                                       "turns": 0, "dur_ms": 0, "daemon": 0, "dispatch": 0})
        d["tasks"] += 1; d["cost"] += cost; d["turns"] += turns; d["dur_ms"] += dur; d[kind] += 1

        k = by_kind[kind]
        k["tasks"] += 1; k["cost"] += cost; k["turns"] += turns; k["dur_ms"] += dur

        t = r.get("t") or 0
        if t:
            span_min = t if span_min is None else min(span_min, t)
            span_max = t if span_max is None else max(span_max, t)

        tasks.append({"role": role, "title": r.get("title") or "", "kind": kind,
                      "dur_ms": dur, "cost": round(cost, 4), "turns": turns,
                      "status": r.get("status"), "ts": r.get("ts")})

    roles = []
    for d in per_role.values():
        d["cost"] = round(d["cost"], 4)
        d["per_turn"] = round(d["cost"] / d["turns"], 4) if d["turns"] else 0
        d["avg_dur_s"] = round(d["dur_ms"] / 1000.0 / d["tasks"], 1) if d["tasks"] else 0
        roles.append(d)
    roles.sort(key=lambda x: -x["cost"])

    for k in by_kind.values():
        k["cost"] = round(k["cost"], 4)
        k["per_task"] = round(k["cost"] / k["tasks"], 4) if k["tasks"] else 0
        k["avg_dur_s"] = round(k["dur_ms"] / 1000.0 / k["tasks"], 1) if k["tasks"] else 0

    top_dur = sorted(tasks, key=lambda x: -x["dur_ms"])[:10]
    total["cost"] = round(total["cost"], 4)
    total["avg_cost"] = round(total["cost"] / total["tasks"], 4) if total["tasks"] else 0
    total["avg_dur_s"] = round(total["dur_ms"] / 1000.0 / total["tasks"], 1) if total["tasks"] else 0

    span = None
    if span_min and span_max:
        span = {"from": time.strftime("%m-%d %H:%M", time.localtime(span_min)),
                "to": time.strftime("%m-%d %H:%M", time.localtime(span_max))}

    return {
        "now": time.strftime("%H:%M:%S", time.localtime(time.time())),
        "total": total,
        "per_role": roles,
        "by_kind": by_kind,
        "top_dur": top_dur,
        "span": span,
    }


def get_dispatch_metrics(force=False):
    """短 TTL 缓存(同步算,聚合很轻 —— 只读小 jsonl)。force=手动刷新强制新算。"""
    with _dispatch_lock:
        data = _dispatch_cache["data"]
        age = time.time() - _dispatch_cache["ts"]
    if data is not None and not force and age < _DISPATCH_TTL:
        return data
    data = _build_dispatch_metrics()
    with _dispatch_lock:
        _dispatch_cache["data"] = data
        _dispatch_cache["ts"] = time.time()
    return data


# =============================================================
#  能力池 caps 面板 —— capabilities.json(能力定义)+ agents.json(各 role caps)
#  + retired_roster.json(退役档案的存档 caps)。全只读,绝不改 caps。
#  复用 agentbus 真相源:_load_caps_pool / _load_agents / _is_test_role,不另写一套。
# =============================================================
# 风险标注:哪些能力是【对外受限】/【高风险闸】—— 最小授权审视用。
# 口径见 capabilities.json:external-tool-edge 只给对外 agent;high-risk 是写库/外发的人工审批闸。
_CAP_RISK = {
    "external-tool-edge": {"level": "对外受限", "cls": "edge"},
    "high-risk":   {"level": "高风险闸", "cls": "hi"},
}


def build_caps():
    """能力池/caps 数据:① 能力池清单(desc/default_for/风险/持有者)② agent×cap 矩阵
    ③ 能力分布(每 cap 几人持有)④ 缺口(空caps在岗 agent / 无持有者能力 / 过滤的测试角色)
    ⑤ 最小授权视角(高风险能力持有者)。文件实时读,只读不改。"""
    pool = ab._load_caps_pool()        # capabilities.json 的 capabilities 段
    agents = ab._load_agents()         # agents.json 花名册(含 retired 标记)
    try:
        roster = ab._load_retired_roster()    # retired_roster.json(已摘番号的存档)
    except Exception:
        roster = []

    cap_order = list(pool.keys())

    # 在册角色分流:测试角色过滤;retired 字段标注;其余为现行授权(active)。
    active, retired_in_agents, test_roles = {}, {}, []
    for role, v in agents.items():
        v = v if isinstance(v, dict) else {}
        if ab._is_test_role(role):
            test_roles.append(role)
            continue
        caps = v.get("caps") or []
        if v.get("retired"):
            retired_in_agents[role] = {"caps": caps, "succeeded_by": v.get("succeeded_by"),
                                       "desc": v.get("desc", "")}
        else:
            active[role] = caps

    # 退役档案(retired_roster.json 里、已从 agents.json 摘除番号的纯存档角色)
    retired = []
    for rec in retired_in_agents.items():
        retired.append({"role": rec[0], "caps": rec[1]["caps"],
                        "succeeded_by": rec[1].get("succeeded_by"),
                        "desc": rec[1].get("desc", ""), "src": "agents.json"})
    for rec in roster:
        r = rec.get("role")
        if r and r not in agents:
            retired.append({"role": r, "caps": rec.get("caps") or [],
                            "succeeded_by": rec.get("succeeded_by"),
                            "desc": rec.get("desc", ""),
                            "reason": rec.get("reason", ""),
                            "retired_at": rec.get("retired"), "src": "roster"})

    # 持有者统计:只数【现行授权】(active)—— 这才是此刻生效的能力分配口径。
    def holders_of(cap):
        return sorted([r for r, caps in active.items() if cap in caps])

    caps_list = []
    for name in cap_order:
        info = pool.get(name, {})
        h = holders_of(name)
        risk = _CAP_RISK.get(name)
        caps_list.append({
            "name": name,
            "desc": info.get("desc", ""),
            "default_for": info.get("default_for", ""),
            "risk": risk["level"] if risk else None,
            "risk_cls": risk["cls"] if risk else None,
            "holders": h,
            "count": len(h),
        })

    # agent×cap 矩阵:现行 agent(按 caps 数降序、名次稳定),retired 单列在后。
    matrix = []
    for role in sorted(active, key=lambda r: (-len(active[r]), r)):
        matrix.append({"role": role, "caps": active[role], "retired": False,
                       "empty": not active[role]})
    # 缺口(a):在岗(在册非退役非测试)但 caps 为空 —— 待补
    empty_caps_agents = sorted([r for r, c in active.items() if not c])
    # 缺口(b):能力池里无人持有的能力 —— 无持有者
    no_holder_caps = [c["name"] for c in caps_list if c["count"] == 0]

    # ⑤ 最小授权:高风险/对外能力的持有者(一眼可见谁握着风险闸)
    high_risk_view = []
    for name in _CAP_RISK:
        if name in pool:
            high_risk_view.append({"name": name, "level": _CAP_RISK[name]["level"],
                                   "cls": _CAP_RISK[name]["cls"],
                                   "holders": holders_of(name)})

    # ⓪ Agent 详情:每个 agent「会哪些内容」= desc(职责全文)+ caps + 在岗状态。
    # 状态从舰队缓存(compute_fleet_cached,只读缓存零扫描)取 verdict/mode/self_wake;
    # 据此派生 form(前台cli / daemon / 离线)+ cls(配色)。退役 agent 从 retired 列表单列、标接替者。
    fleet = compute_fleet_cached()
    fmap = {r.get("role"): r for r in (fleet.get("roles") or [])}

    def _status_of(role):
        fr = fmap.get(role) or {}
        verdict = fr.get("verdict", "未声明(无 presence)")
        vclass = fr.get("vclass", "none")
        mode = fr.get("mode")
        sw = fr.get("self_wake")
        if vclass == "parked":
            form, fcls = "🅿️休眠", "off"   # 故意暂离≠离线红:与运维 verdict 短路口径一致
        elif mode == "daemon":
            form, fcls = "daemon", "daemon"
        elif vclass in ("live", "idle") or sw in ("armed", "recent") or mode == "listening":
            form, fcls = "前台cli", "cli"
        else:
            form, fcls = "离线", "off"
        return {"verdict": verdict, "vclass": vclass, "mode": mode,
                "self_wake": sw, "form": form, "form_cls": fcls}

    agents_detail = []
    for role in sorted(active, key=lambda r: (-len(active[r]), r)):
        v = agents.get(role) or {}
        st = _status_of(role)
        agents_detail.append({
            "role": role,
            "desc": v.get("desc", "") if isinstance(v, dict) else "",
            "caps": active[role],
            "registered": v.get("registered", "") if isinstance(v, dict) else "",
            "retired": False,
            **st,
        })
    agents_retired = []
    for r in retired:
        agents_retired.append({
            "role": r["role"],
            "desc": r.get("desc", ""),
            "caps": r.get("caps") or [],
            "succeeded_by": r.get("succeeded_by"),
            "reason": r.get("reason", ""),
            "retired_at": r.get("retired_at", ""),
            "retired": True,
            "form": "退役", "form_cls": "ret",
        })

    return {
        "now": time.strftime("%H:%M:%S", time.localtime(time.time())),
        "cap_order": cap_order,
        "caps": caps_list,
        "matrix": matrix,
        "retired": retired,
        "gaps": {
            "empty_caps_agents": empty_caps_agents,
            "no_holder_caps": no_holder_caps,
            "test_roles": sorted(test_roles),
        },
        "high_risk_view": high_risk_view,
        "agents": agents_detail,
        "agents_retired": agents_retired,
        "summary": {
            "n_caps": len(cap_order),
            "n_active": len(active),
            "n_retired": len(retired),
            "n_empty": len(empty_caps_agents),
        },
    }


def read_replies(role="Master"):
    d = BUS / role / "done"
    out = []
    if d.exists():
        for p in sorted(d.glob("*.json")):
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                o = {"id": p.stem, "result": "(无法解析)"}
            out.append({
                "file": p.name,
                "id": o.get("id", p.stem),
                "from": o.get("from", "?"),
                "status": o.get("status", ""),
                "title": o.get("title", ""),
                "result": o.get("result", ""),
                "finished": o.get("finished", ""),
            })
    return out


def compute_daemons():
    """daemon 自愈面板:读 fleet.json 期望清单,交叉 ab._daemon_alive 判存活。
    复用 agentbus 的 BUS / _daemon_alive,不另写一套判活逻辑。"""
    out = {"daemons": [], "error": None}
    fp = BUS / "fleet.json"
    if not fp.exists():
        return out
    try:
        fleet = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as e:
        out["error"] = "读 fleet.json 失败: %s" % e
        return out
    for d in fleet.get("daemons", []):
        role = d.get("role")
        if not role:
            continue
        out["daemons"].append({
            "role": role,
            "cwd": d.get("cwd") or ab.cfg("default_worker_cwd"),
            "wait_timeout": d.get("wait_timeout", 300),
            "alive": ab._daemon_alive(role),
        })
    return out


def read_events(n=40):
    """审计流:读 events.jsonl 末 N 行(send/claim/done),倒序(最新在前)。
    events.jsonl 是 agentbus._audit 的 append-only 真相源,UI 只读不写。"""
    fp = BUS / "events.jsonl"
    if not fp.exists():
        return []
    try:
        lines = fp.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-n:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            out.append({"ts": "", "event": "(无法解析)", "raw": ln})
    out.reverse()
    return out


def reap_dryrun():
    """孤儿巡检:复用 agentbus reap --dry-run(子进程),返回其逐行输出。
    dry-run 绝不改动队列,只报告哪些 processing 任务 owner 已死/超期。"""
    cmd = [sys.executable, str(ABDIR / "agentbus.py"), "reap", "--dry-run"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=40, creationflags=_NO_WINDOW)
    except Exception as e:
        return {"ok": False, "error": str(e), "lines": []}
    out = (r.stdout or "").strip()
    lines = [l for l in out.splitlines() if l.strip()] if out else []
    return {"ok": r.returncode == 0, "error": (r.stderr or "").strip() or None, "lines": lines}


def run_supervise(dry_run=False):
    """daemon 自愈:复用 agentbus supervise(子进程,重启 fleet.json 里已死 daemon)。
    与 WebUI 后台 supervisor 线程同一命令,这里给一个手动触发入口。"""
    cmd = [sys.executable, str(ABDIR / "agentbus.py"), "supervise"]
    if dry_run:
        cmd.append("--dry-run")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60, creationflags=_NO_WINDOW)
    except Exception as e:
        return {"ok": False, "error": str(e), "lines": []}
    out = (r.stdout or "").strip()
    lines = [l for l in out.splitlines() if l.strip()] if out else []
    return {"ok": r.returncode == 0, "error": (r.stderr or "").strip() or None, "lines": lines}


def build_state(reply_role="Master", fresh=False):
    fleet = compute_fleet_cached(force=fresh)  # 缓存兜住轮询;fresh=SSE/手动刷新强制新算
    # 给每个 role 附带可点开的任务明细(inbox/processing/held/failed 任一非空就读)
    for row in fleet["roles"]:
        row["turn"] = _turn_state(row["role"])   # 现读 turn 状态(不吃 20s 舰队缓存;/api/state 有 3s TTL → ≤3s 刷新,灵敏)
        if row["inbox"] or row["processing"] or row["held"] or row["failed"]:
            row["tasks"] = _read_tasks(row["role"])
        else:
            row["tasks"] = []
    return {
        "now": ab._now(),
        "bus": str(BUS),
        "fleet": fleet,
        "reply_role": reply_role,
        "replies": read_replies(reply_role),
        "all_roles": _roles(),
        "daemons": compute_daemons(),
        "events": read_events(40),
        "todos": _sorted_todos(),
    }


# =============================================================
#  变更通知 —— watchdog 文件事件,驱动 SSE 推送(不 sleep polling)
# =============================================================
_change_cv = threading.Condition()
_change_ver = 0


def _bump():
    global _change_ver
    with _change_cv:
        _change_ver += 1
        _change_cv.notify_all()


# --- 通知/消息管理(新架构:daemon 不再执行,执行交可见 CLI tab;通知收进常驻 WebUI)---
# 某 role 来了新任务但【没有活的可见执行 tab】(mode=listening 的 /loop)→ 弹 OS toast 提醒人去处理。
# 复用 agentbus 的 _fire_toast(WinRT)。已通知过的 task id 记下,避免 watchdog 重复事件刷屏。
_notified = set()


def _role_has_live_executor(role):
    """role 是否有活的可见执行 tab(/loop serve = mode listening + claude_pid 在跑且心跳新鲜)。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return False
    try:
        pr = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return False
    if pr.get("mode") != "listening":
        return False
    pid = pr.get("claude_pid")
    if not (pid and ab.psutil is not None and ab.psutil.pid_exists(pid)):
        return False
    age = ab._age_seconds(pr.get("last_seen", ""))
    wt = pr.get("wait_timeout") or 110
    return age is not None and age <= wt + 60


def _maybe_notify_pending(path):
    """新 inbox 任务且该 role 无活执行 tab → toast 提醒。best-effort,不抛错。"""
    try:
        p = Path(path)
        if p.suffix != ".json" or p.parent.name != "inbox":
            return
        role = p.parent.parent.name
        if role.startswith((".", "_")):
            return
        o = json.loads(p.read_text(encoding="utf-8"))
        tid = o.get("id", p.stem)
        if tid in _notified:
            return
        _notified.add(tid)
        if len(_notified) > 800:            # 防无界增长,留近期
            _notified.clear(); _notified.add(tid)
        if _role_has_live_executor(role):   # 有可见 tab 在听 → 它会自己处理,不打扰
            return
        title = o.get("title") or (o.get("task", "") or "")[:40]
        flag = "⚠需审批 " if o.get("requires_approval") else ""
        ab._fire_toast("📥 %s 有新任务待处理" % role,
                       "%s%s — 请在该 tab 起 /loop 或人工处理" % (flag, title))
    except Exception:
        pass


# --- 定时通信通道(daemon→agent,2026-06-23):每 5s 把舰队战报刷到 fleet_status.json ---
# 协调 agent(Master/MasterAgent)随时读它即得 ≤5s 新鲜的全舰队状态(谁在做啥/队列/最近完成),
# 不必自己跑 who/list,也不必起 /loop。要紧事(error/待审)仍由 _maybe_notify_pending 即时 toast。
FLEET_STATUS = ABDIR / "fleet_status.json"


def _build_fleet_status():
    # 复用刷新线程算好的扫描结果(_fleet_cache._scan),绝不在本(推送)线程里再做 _proc_scan。
    # 原先 _self_wake_state(r) 不带 wait_roles → 触发 _proc_scan(主进程 2s GIL 占用,每 15s 一次,
    # 会饿死正在服务的 /api/state 请求线程)。现传入预扫的 wait_roles 集合 → 零进程扫。
    with _fleet_lock:
        _cache = _fleet_cache["data"]
    _scan = (_cache or {}).get("_scan") or {}
    _wait_roles = set(_scan.get("wait_roles") or [])
    _has_scan = _cache is not None
    roles = []
    for r in ab._list_roles():
        pf = BUS / r / "presence.json"
        mode = cur = curt = None
        alive = False
        if pf.exists():
            try:
                pr = json.loads(pf.read_text(encoding="utf-8"))
                mode = pr.get("mode")
                cur = pr.get("current_task"); curt = pr.get("current_title")
                if mode == "daemon":
                    dp = pr.get("daemon_pid"); alive = bool(dp and ab.psutil and ab.psutil.pid_exists(dp))
                else:
                    cp = pr.get("claude_pid"); alive = bool(cp and ab.psutil and ab.psutil.pid_exists(cp))
            except Exception:
                pass
        # 传入预扫 wait_roles → _self_wake_state 内部不再 _wait_proc_roles() 全扫;pid_exists 便宜判活。
        # 缓存未就绪(冷启 ~4s 窗口)时降级标 off,避免回退到现扫。
        _sw = ab._self_wake_state(r, None, _wait_roles) if _has_scan else ("off", "缓存预热中")
        roles.append({
            "role": r, "mode": mode, "alive": alive,
            "current_task": cur, "current_title": curt,
            "inbox": _count(r, "inbox"), "processing": _count(r, "processing"),
            "done": _count(r, "done"), "held": _count(r, "held"), "failed": _count(r, "failed"),
            "self_wake": _sw[0], "self_wake_detail": _sw[1],
        })
    # 最近事件(events.jsonl 末 8 行)
    recent = []
    ef = BUS / "events.jsonl"
    if ef.exists():
        try:
            recent = [json.loads(x) for x in ef.read_text(encoding="utf-8").splitlines()[-8:] if x.strip()]
        except Exception:
            recent = []
    return {"ts": ab._now(), "roles": roles, "recent": recent}


def start_status_pusher(interval=5):
    """每 interval 秒把舰队战报写到 fleet_status.json(daemon→agent 定时通信)。
    用 Event.wait 计时(非忙等);写的是被 watcher 忽略的文件,不会自触发 SSE。"""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                tmp = FLEET_STATUS.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(_build_fleet_status(), ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, FLEET_STATUS)
            except Exception:
                pass

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[status] fleet_status.json 定时战报已启动,间隔 %ds → %s" % (interval, FLEET_STATUS))
    return t


def start_watcher():
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except Exception as e:
        print("[watch] watchdog 不可用,SSE 退化为定时心跳:", e)
        return None

    class H(FileSystemEventHandler):
        def _hit(self, path):
            s = str(path)
            if s.endswith("fleet_status.json"):
                return False   # 5s 定时战报文件,自己写的,不触发 SSE(否则 5s 刷屏)
            return s.endswith(".json")

        def on_created(self, e):
            _maybe_notify_pending(getattr(e, "src_path", ""))

        def on_moved(self, e):
            _maybe_notify_pending(getattr(e, "dest_path", ""))

        def on_any_event(self, e):
            try:
                if self._hit(getattr(e, "src_path", "")) or self._hit(getattr(e, "dest_path", "")):
                    _bump()
            except Exception:
                pass

    BUS.mkdir(parents=True, exist_ok=True)
    obs = Observer()
    obs.schedule(H(), str(BUS), recursive=True)
    obs.daemon = True
    obs.start()
    print("[watch] watchdog 监听:", BUS)
    return obs


# =============================================================
#  发任务 —— 复用 agentbus send(子进程,确保 --from Master)
# =============================================================

def send_task(to, title, task, requires_approval=False, notify="auto"):
    """复用 agentbus send 派活(from=Master)。
    requires_approval=True → 加 --requires-approval(高风险:自动监听者不领,留 inbox 待人工批)。
    notify 是 worker 完成回填(done)时的通知偏好:agentbus send 本身不带 notify(它是 done 端 flag),
    故仅当 != auto 时把它作为前向提示字段写进任务 JSON(worker done 默认 auto 自决,不读也无害),
    复用 ab._write_json 落地,不另写一套写盘逻辑。"""
    cmd = [sys.executable, str(ABDIR / "agentbus.py"), "send",
           "--to", to, "--from", "Master", "--title", title or "", "--task", task]
    if requires_approval:
        cmd.append("--requires-approval")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "send 失败")
    tid = r.stdout.strip()  # task id
    if notify and notify != "auto":
        # 前向提示:把 notify 偏好写回刚落地的 inbox 任务(best-effort,不影响主流程)
        try:
            p = BUS / to / "inbox" / ("%s.json" % tid)
            if p.exists():
                o = json.loads(p.read_text(encoding="utf-8"))
                o["notify"] = notify
                ab._write_json(p, o)
        except Exception:
            pass
    return tid


def clear_replies(role, file=None):
    d = BUS / role / "done"
    n = 0
    if not d.exists():
        return 0
    targets = [d / file] if file else sorted(d.glob("*.json"))
    for p in targets:
        try:
            if p.exists() and p.parent == d:  # 防越界
                p.unlink()
                n += 1
        except Exception:
            pass
    return n


# =============================================================
#  待办池 TODO Pool —— WebUI 自管,存 agent_bus/todos.json
#  (不改 agentbus.py 核心,保持职责边界;派活时才复用 send_task)
#  schema: {id,title,detail,source,priority(高/中/低),status(待办/进行中/完成/搁置),
#           deadline,created,updated,task_id,dispatched_to}
# =============================================================
TODOS = ABDIR / "todos.json"
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}
STATUS_ORDER = {"待办": 0, "进行中": 1, "搁置": 2, "完成": 3}
_VALID_PRIORITY = set(PRIORITY_ORDER)
_VALID_STATUS = set(STATUS_ORDER)

# 种子(空)—— 仅首次创建 todos.json 时写入
_SEED_TODOS = [
]


def _todo_id():
    return "t-%d-%s" % (time.time_ns() // 1_000_000, os.urandom(2).hex())


def _load_todos():
    if not TODOS.exists():
        # 首次:写入种子
        seeded = []
        for s in _SEED_TODOS:
            o = dict(s)
            o.update({"id": _todo_id(), "created": ab._now(), "updated": ab._now(),
                      "task_id": None, "dispatched_to": None})
            seeded.append(o)
        _save_todos(seeded)
        return seeded
    try:
        data = json.loads(TODOS.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_todos(items):
    ABDIR.mkdir(parents=True, exist_ok=True)
    tmp = TODOS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, TODOS)


def _sync_dispatched(items):
    """派活闭环:进行中的待办若其 task_id 已在 Master/done 落了结果,自动标✓完成并附结果摘要。
    只 stat 派过活的项(便宜),仅有变更才写盘。返回是否有变更。"""
    changed = False
    for o in items:
        if o.get("status") == "进行中" and o.get("task_id") and not o.get("dispatch_result"):
            dp = BUS / "Master" / "done" / ("%s.json" % o["task_id"])
            if dp.exists():
                try:
                    r = json.loads(dp.read_text(encoding="utf-8"))
                    res = (r.get("result") or "")[:500]
                    o["status"] = "完成"
                    o["dispatch_result"] = res
                    o["dispatch_result_status"] = r.get("status", "ok")
                    o["updated"] = ab._now()
                    changed = True
                except Exception:
                    pass
    return changed


def _sorted_todos():
    items = _load_todos()
    if _sync_dispatched(items):
        _save_todos(items)
    items.sort(key=lambda t: (STATUS_ORDER.get(t.get("status"), 9),
                              PRIORITY_ORDER.get(t.get("priority"), 9),
                              t.get("deadline") or "9999"))
    return items


def todo_add(b):
    items = _load_todos()
    src = (b.get("source") or "").strip()
    # 「谁发的」:UI 手动加的来源空就默认「用户」(或用户填的名)
    creator = (b.get("creator") or "").strip() or src or "用户"
    o = {
        "id": _todo_id(),
        "title": (b.get("title") or "").strip() or "(无标题)",
        "detail": (b.get("detail") or "").strip(),
        "source": src,
        "creator": creator,
        "priority": b.get("priority") if b.get("priority") in _VALID_PRIORITY else "中",
        "status": b.get("status") if b.get("status") in _VALID_STATUS else "待办",
        "deadline": (b.get("deadline") or "").strip(),
        "created": ab._now(), "updated": ab._now(),
        "task_id": None, "dispatched_to": None,
    }
    items.append(o)
    _save_todos(items)
    return {"ok": True, "id": o["id"]}


def todo_update(b):
    tid = b.get("id")
    items = _load_todos()
    for o in items:
        if o.get("id") == tid:
            for f in ("title", "detail", "source", "deadline", "note"):
                if f in b and b[f] is not None:
                    o[f] = str(b[f]).strip()   # note = 用户常驻补充说明(与 detail 原文、dispatch_note 一次性 区分)
            if b.get("priority") in _VALID_PRIORITY:
                o["priority"] = b["priority"]
            if b.get("status") in _VALID_STATUS:
                o["status"] = b["status"]
            if "ignored" in b:
                o["ignored"] = bool(b["ignored"])   # 忽略/了结标记(向后兼容,可选字段)
            o["updated"] = ab._now()
            _save_todos(items)
            return {"ok": True}
    return {"ok": False, "error": "未找到 id %s" % tid}


def todo_delete(b):
    tid = b.get("id")
    items = _load_todos()
    n = len(items)
    items = [o for o in items if o.get("id") != tid]
    _save_todos(items)
    return {"ok": True, "deleted": n - len(items)}


def todo_answer(b):
    """闭环 ask-user:用户在🙋待确认卡回复 → shell `agentbus answer` 把答复路由回提问 agent
    (confirm_from)的 inbox + 标该待办完成。子进程带 CREATE_NO_WINDOW 不弹窗。"""
    tid = (b.get("id") or "").strip()
    reply = (b.get("reply") or "").strip()
    if not tid:
        return {"ok": False, "error": "缺 id"}
    if not reply:
        return {"ok": False, "error": "回复内容不能为空"}
    try:
        r = subprocess.run(
            [_PYEXE, str(ABDIR / "agentbus.py"), "answer", "--todo", tid, "--reply", reply, "--from", "用户"],
            capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        out = (r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")).strip()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # answer 成功打印纯文本「答复已路由…」;失败打印 {"error": "..."}
    if r.returncode == 0 and out and not out.lstrip().startswith('{"error"'):
        return {"ok": True, "msg": out}
    err = out
    try:
        err = json.loads(out).get("error", out)
    except Exception:
        pass
    return {"ok": False, "error": err or "answer 失败(无输出)"}


def todo_dispatch(b):
    """把一条待办派成 bus 任务(复用 send_task,from=Master),回写 task_id 闭环追踪。"""
    tid = b.get("id")
    to = (b.get("to") or "").strip()
    note = (b.get("note") or "").strip()   # 派活当场补的【额外】说明(可空,只为派活当下的额外话)
    if not to:
        return {"ok": False, "error": "未选 to role"}
    items = _load_todos()
    for o in items:
        if o.get("id") == tid:
            title = o.get("title") or "(待办)"
            parts = [o.get("detail") or ""]
            todo_note = (o.get("note") or "").strip()   # 待办常驻💬补充说明 → 派活【自动带上】,用户不必重写
            if todo_note:
                parts.append("\n\n补充说明:%s" % todo_note)
            if o.get("source"):
                parts.append("\n来源:%s" % o["source"])
            if o.get("deadline"):
                parts.append("\n截止:%s" % o["deadline"])
            if note:
                parts.append("\n\n派活额外说明(用户 via WebUI @ %s):%s" % (ab._now(), note))
            parts.append("\n(由 WebUI 待办池派发,todo id=%s)" % tid)
            task_text = "".join(p for p in parts if p).strip() or title
            try:
                task_id = send_task(to, title, task_text,
                                    requires_approval=bool(b.get("requires_approval")),
                                    notify=b.get("notify") or "auto")
            except Exception as e:
                return {"ok": False, "error": str(e)}
            o["task_id"] = task_id
            o["dispatched_to"] = to
            if note:  # 记下附加说明 + 谁/何时加的
                o["dispatch_note"] = note
                o["dispatch_note_by"] = "用户(WebUI)"
                o["dispatch_note_at"] = ab._now()
            if o.get("status") == "待办":
                o["status"] = "进行中"
            o["updated"] = ab._now()
            _save_todos(items)
            return {"ok": True, "task_id": task_id, "to": to}
    return {"ok": False, "error": "未找到 id %s" % tid}


# =============================================================
#  实时视图 live.log —— 只读 headless 执行流(claude_run.py 流式写)
#  持久 daemon → <BUS>/<role>/live.log ;一次性 ad-hoc → <BUS>/_dispatch/<id>.live.log
#  只读取末尾 ~12KB,不读整文件;复用 ab.BUS,绝不改文件。
# =============================================================
_LIVE_TAIL = 12 * 1024


def read_live(role=None, dispatch_id=None):
    if dispatch_id:
        # 防穿越:id 只允许安全字符
        if any(c in dispatch_id for c in ("/", "\\", "..")) or not dispatch_id:
            return {"ok": False, "error": "bad id"}
        p = BUS / "_dispatch" / ("%s.live.log" % dispatch_id)
        label = "_dispatch/%s.live.log" % dispatch_id
    elif role:
        if role.startswith((".", "_")) or any(c in role for c in ("/", "\\", "..")):
            return {"ok": False, "error": "bad role"}
        p = BUS / role / "live.log"
        label = "%s/live.log" % role
    else:
        return {"ok": False, "error": "需要 role 或 id 参数"}
    if not p.exists():
        return {"ok": True, "exists": False, "content": "", "mtime": None, "path": label}
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > _LIVE_TAIL:
                f.seek(size - _LIVE_TAIL)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        if size > _LIVE_TAIL and "\n" in text:
            text = text.split("\n", 1)[1]  # 丢掉被截断的首行残片
        return {
            "ok": True, "exists": True, "content": text, "path": label,
            "size": size,
            "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime)),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================
#  历史用量 BI (P0)—— 使用分析 token_stats.py --json(增量 ETL,已 ~50ms)
#  口径:已按 message.id 去重、Shanghai 日、cost=API 等价。
#  fresh=1 同步跑增量再返回(点一次即最新);非 fresh 走缓存,含今日缩 TTL 60s 贴近实时。
# =============================================================
TOKEN_STATS = str(Path.home() / ".claude" / "projects" / "token_stats.py")
_USAGE_TTL = 300.0          # 纯历史(数据不含今日)长 TTL
_USAGE_TTL_TODAY = 60.0     # 数据含今日 → 缩 TTL 贴近实时(ETL 已增量 ~50ms)
_usage_cache = {"ts": 0.0, "data": None}
_usage_lock = threading.Lock()
_usage_refreshing = {"v": False}


def _data_has_today(data):
    """data 最新一天是否就是今天(本地=Shanghai 日)。含今日则用短 TTL。"""
    try:
        daily = (data or {}).get("daily") or []
        if not daily:
            return False
        return daily[-1].get("date") == time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))  # Shanghai(UTC+8)显式·换机不漂
    except Exception:
        return False


def _augment_daily_with_backfill(data):
    """把 usage.db 的 daily_backfill(03-27~04-28,Claude Code stats-cache 抢救的【日聚合】历史)
    并入 data['daily'],让历史从 03-27 连续。加法:不覆盖真实 ETL 行(messages),不碰 totals/by_project。
    数据真实性:① backfill 标 src='backfill'(日聚合粒度、无逐会话/cache 拆分、口径或略异);
    ② 04-29~05-10 无任何源 → 插 src='gap' 占位(tokens=None,绝不填 0);③ 真实行标 src='messages'。"""
    try:
        daily = data.get("daily")
        if not isinstance(daily, list):
            return data
        import sqlite3
        from datetime import date, timedelta
        dbp = ABDIR / "usage.db"
        if not dbp.exists():
            return data
        con = sqlite3.connect(str(dbp))
        try:
            rows = con.execute("SELECT date,total_tokens,tokens_by_model,messages,sessions,"
                               "tool_calls,source FROM daily_backfill ORDER BY date").fetchall()
        except Exception:
            return data
        finally:
            con.close()
        existing = set()
        for d in daily:
            d.setdefault("src", "messages")
            existing.add(d.get("date"))
        if not rows:
            return data
        bf, bf_dates = [], set()
        for (dt, tot, bym, msgs, sess, tools, source) in rows:
            if dt in existing:           # 真实 ETL 优先,backfill 不覆盖
                continue
            try:
                bymodel = json.loads(bym) if bym else {}
            except Exception:
                bymodel = {}
            bf.append({"date": dt, "src": "backfill", "tokens_total": tot, "tokens_io": None,
                       "cost_usd": None, "calls": None, "messages": msgs, "sessions": sess,
                       "tool_calls": tools, "by_model": bymodel, "source": source})
            bf_dates.add(dt)
        gap = []
        if bf_dates and existing:
            try:
                cur = date.fromisoformat(max(bf_dates)) + timedelta(days=1)
                end = date.fromisoformat(min(existing))
                while cur < end:
                    ds = cur.isoformat()
                    if ds not in bf_dates and ds not in existing:
                        gap.append({"date": ds, "src": "gap", "tokens_total": None,
                                    "tokens_io": None, "cost_usd": None, "calls": None})
                    cur += timedelta(days=1)
            except Exception:
                pass
        merged = bf + gap + daily
        merged.sort(key=lambda x: x.get("date") or "")
        data["daily"] = merged
        data["backfill_meta"] = {
            "n_backfill": len(bf), "n_gap": len(gap),
            "bf_from": (min(bf_dates) if bf_dates else None),
            "bf_to": (max(bf_dates) if bf_dates else None),
            "gap_from": (gap[0]["date"] if gap else None),
            "gap_to": (gap[-1]["date"] if gap else None),
            "source": (rows[0][6] if rows else None),
            "note": "03-27~04-28 为 Claude Code stats-cache 抢救的【日聚合】数据(无逐会话/cache 拆分、"
                    "口径或略异,供趋势参考);04-29~05-10 无任何源,显示为缺口(非0)。",
        }
        return data
    except Exception:
        return data   # 任何异常都不拖垮正常用量返回


def _run_token_stats():
    r = subprocess.run([sys.executable, TOKEN_STATS, "--json"],
                       capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[:300] or "token_stats 失败")
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    return _augment_daily_with_backfill(data)


def _refresh_usage():
    try:
        data = _run_token_stats()
        with _usage_lock:
            _usage_cache["data"] = data
            _usage_cache["ts"] = time.time()
    except Exception as e:
        with _usage_lock:
            if _usage_cache["data"] is None:
                _usage_cache["data"] = {"error": str(e)}
                _usage_cache["ts"] = time.time()
    finally:
        _usage_refreshing["v"] = False


def get_usage(force=False):
    if force:
        # fresh=1:同步跑一次增量 ETL+聚合再返回最新(增量已 ~50ms,可接受)。
        # 不再「立即返回旧缓存 + 后台异步刷新」——那是旧 3s 全扫时代的设计,
        # 正是它导致 fresh=1 当次仍返回旧值、要点两下。失败则兜旧缓存。
        try:
            data = _run_token_stats()
        except Exception as e:
            with _usage_lock:
                return _usage_cache["data"] or {"error": str(e)}
        with _usage_lock:
            _usage_cache["data"] = data
            _usage_cache["ts"] = time.time()
        return data
    with _usage_lock:
        data = _usage_cache["data"]
        age = time.time() - _usage_cache["ts"]
    if data is None:
        # 首次:同步跑一次(增量 ~50ms);之后走缓存 + 到期后台刷新
        try:
            data = _run_token_stats()
        except Exception as e:
            data = {"error": str(e)}
        with _usage_lock:
            _usage_cache["data"] = data
            _usage_cache["ts"] = time.time()
        return data
    # 含今日数据 → 短 TTL 贴近实时;纯历史 → 长 TTL
    ttl = _USAGE_TTL_TODAY if _data_has_today(data) else _USAGE_TTL
    if age >= ttl and not _usage_refreshing["v"]:
        _usage_refreshing["v"] = True
        threading.Thread(target=_refresh_usage, daemon=True).start()
    return data


# =============================================================
#  可恢复会话 (resume 看板)—— ab sessions --json(只读 jsonl 头部,轻量)
#  只展示元数据(标题/项目/时间/id/大小),绝不读会话正文。缓存 60s。
# =============================================================
_SESS_TTL = 60.0
_sess_cache = {"ts": 0.0, "data": None}
_sess_lock = threading.Lock()
# agent 相关 cwd 片段
_AGENT_CWD_HINTS = ("agent_fleet",)


def read_sessions():
    with _sess_lock:
        if _sess_cache["data"] is not None and (time.time() - _sess_cache["ts"]) < _SESS_TTL:
            return _sess_cache["data"]
    try:
        r = subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "sessions", "--json"],
                           capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        raw = r.stdout.decode("utf-8", "replace")  # 防孤 surrogate:replace 落地为 U+FFFD
        rows = json.loads(raw) if raw.strip() else []
    except Exception as e:
        return {"ok": False, "error": str(e), "sessions": []}
    out = []
    for s in rows:
        cwd = s.get("cwd") or ""
        proj = s.get("project") or ""
        is_agent = any(h in cwd or h in proj for h in _AGENT_CWD_HINTS)
        out.append({
            "id": s.get("id"),
            "project": proj,
            "cwd": cwd,
            "title": s.get("title") or "",
            "last": s.get("last") or "",
            "size_kb": s.get("size_kb"),
            "is_agent": is_agent,
            # 数据侧新增字段(commit b38e20f/d70e463):实时状态 + 标签 + 备注
            "state": s.get("state") or "",          # "运行中"/"死"
            "live_role": s.get("live_role"),         # 运行中=所属 role,死=None
            "tags": s.get("tags") or [],
            "note": s.get("note") or "",
            "archived": bool(s.get("archived")),
            # 归属(精确判据:session_id∈presence/退役档 或 cwd∈bus跑活目录→bus,否则个人)
            "is_bus": bool(s.get("is_bus")),
        })
    res = {"ok": True, "sessions": out,
           "agent_count": sum(1 for x in out if x["is_agent"]),
           "live_count": sum(1 for x in out if x["state"] == "运行中"),
           "dead_count": sum(1 for x in out if x["state"] == "死"),
           "tagged_count": sum(1 for x in out if x["tags"]),
           "bus_count": sum(1 for x in out if x["is_bus"]),
           "nonbus_count": sum(1 for x in out if not x["is_bus"]),
           "total": len(out)}
    with _sess_lock:
        _sess_cache["data"] = res
        _sess_cache["ts"] = time.time()
    return res


# =============================================================
#  上下文窗口占用 —— 1M 撞墙预警(SESSION_RELIABILITY 可视化落地)
#  数据源:每个活跃 session 的 jsonl 尾部最后一条 assistant.usage:
#    ctx = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
#    占用% = ctx / 窗口(按模型真实值,opus-4*/sonnet-4=1M、haiku=200K、未知=200K 标注)
#  性能:bounded tail/head 读(不整文件加载),且只在后台刷新线程跑 → 请求路径只读缓存,绝不现读。
#  显示:所有活跃会话如实显 proj(自看控制台无外部方,默认不脱敏);is_agent 仅用于 role 绑定判定,不再据此掩码个人会话。
# =============================================================
_CTX_PROJECTS_DIR = str(Path.home() / ".claude" / "projects")
_CTX_ACTIVE_MIN = 90          # 最近 90 分钟有写入 = 活跃会话
_CTX_TAIL_BYTES = 512 * 1024  # 读尾部 512KB 找最后一条 assistant.usage(够容一条完整消息)
_CTX_COMPACT_FRAC = 0.65      # 实测:占用达窗口 65% 触发 auto-compact(有损摘要→降智)。这是真阈,不是撞 1M。
_CTX_RED = 65.0               # ≥65% 红 = 将被 auto-compact(建议先主动落档重置,别被动挨压)
_CTX_YELLOW = 50.0            # ≥50% 黄(逼近自动压线,留意)
_ctx_cache = {"ts": 0.0, "data": None}
_ctx_lock = threading.Lock()


def _ctx_win_of(model):
    """模型→上下文窗口(真实值)。返回 (window, known):known=False 表示窗口为假定值,UI 需标注。
    切勿按名硬编 200K——opus-4-7/opus-4-8 都是 1M(踩过把 opus-4-7 当 200K 算出假 164%)。"""
    m = (model or "").lower()
    if "opus-4" in m or "haiku" in m:           # 任务确认:opus-4*=1M、haiku=200K
        return (1000000 if "opus-4" in m else 200000), True
    if "sonnet-4" in m:                          # 1M-capable 家族(沿用参考实现,窗口标假定)
        return 1000000, False
    return 200000, False                         # 未知:默认 200K 且标注


def _ctx_tail_text(path, nbytes):
    try:
        sz = os.path.getsize(path)
        with open(path, "rb") as f:
            if sz > nbytes:
                f.seek(sz - nbytes)
            return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


def _ctx_last_usage(path):
    """尾部找最后一条 assistant.usage → (ctx_tokens, model, ts) 或 (None, None, None)。"""
    txt = _ctx_tail_text(path, _CTX_TAIL_BYTES)
    for line in reversed(txt.splitlines()):
        line = line.strip()
        if not line or '"usage"' not in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        m = r.get("message", {})
        u = m.get("usage")
        if u and m.get("role") == "assistant":
            ctx = (u.get("input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0) \
                + (u.get("cache_creation_input_tokens", 0) or 0)
            return ctx, m.get("model", ""), (r.get("timestamp", "") or "")[:19]
    return None, None, None


def _refresh_ctx():
    rows = []
    now = time.time()
    try:
        paths = glob.glob(_CTX_PROJECTS_DIR + "/*/*.jsonl")
    except Exception:
        paths = []
    for p in paths:
        try:
            if now - os.path.getmtime(p) > _CTX_ACTIVE_MIN * 60:
                continue
        except Exception:
            continue
        ctx, model, ts = _ctx_last_usage(p)
        if not ctx:
            continue
        win, known = _ctx_win_of(model)
        pct = round(100.0 * ctx / win, 1) if win else 0.0
        compact_at = int(round(win * _CTX_COMPACT_FRAC)) if win else 0   # 该窗口的 65% 自动压阈(tok)
        margin = compact_at - ctx                                        # 距自动压余量(<0=已过线、将被压)
        proj = os.path.basename(os.path.dirname(p))
        # 项目目录名是 cwd 的 dash 编码(C:\apphome\agent_fleet → C--apphome-agent-fleet),
        # 而 _AGENT_CWD_HINTS 用下划线;两边都归一(_→-、小写)再匹配,否则永远 False。
        nproj = proj.lower()
        is_agent = any(h.replace("_", "-").lower() in nproj for h in _AGENT_CWD_HINTS)
        sid_full = os.path.basename(p)[:-6]   # 会话 jsonl id(= who 暴露的 session_id,用于精确 session→role 绑定)
        rows.append({
            "pct": pct, "ctx": ctx, "win": win, "win_known": known,
            "compact_at": compact_at, "margin": margin,
            "model": (model or "?"), "is_agent": is_agent,
            "proj": proj, "sid": sid_full[:8], "sid_full": sid_full,
            "ts": ts, "level": ("red" if pct >= _CTX_RED else "yellow" if pct >= _CTX_YELLOW else "green"),
        })
    rows.sort(key=lambda x: x["pct"], reverse=True)
    reds = [r for r in rows if r["level"] == "red"]
    data = {
        "ok": True, "rows": rows, "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "active_min": _CTX_ACTIVE_MIN, "red": _CTX_RED, "yellow": _CTX_YELLOW,
        "n": len(rows), "n_red": len(reds), "n_yellow": sum(1 for r in rows if r["level"] == "yellow"),
        "max_pct": rows[0]["pct"] if rows else 0.0,
    }
    with _ctx_lock:
        _ctx_cache["data"] = data
        _ctx_cache["ts"] = time.time()


def get_ctx():
    with _ctx_lock:
        return _ctx_cache["data"]


def start_ctx_refresher(interval=12):
    """唯一的上下文占用刷新者:后台线程每 interval 秒读活跃会话 jsonl 尾部刷新 _ctx_cache。
    bounded tail/head 读 + 后台独立 GIL → 请求路径只读缓存,零现读。首轮立即跑。"""
    stop = threading.Event()

    def loop():
        while True:
            try:
                _refresh_ctx()
            except Exception:
                pass
            if stop.wait(interval):
                break

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[ctx] 后台上下文占用刷新线程已启动,间隔 %ds(bounded jsonl 尾读,请求零现读)" % interval)
    return t


# =============================================================
#  who 缓存 —— 上下文模块仅用它【校验 role 标签是否在岗注册】(role_live ✓/?)。
#  权威源 = `who --json`(presence 心跳 × 实际 claude 进程,与 cmd_who 同口径)。
#  注:liveness/注册/verdict 等健康项归 🛰 运维面板;此处只取在岗 role 集做标签校验。
#  presence 不存 session-jsonl id,故无法精确会话↔role(占用本身逐会话精确,见 build_ctxpanel)。
# =============================================================
_WHO_TTL = 20.0
_who_cache = {"ts": 0.0, "data": None}
_who_lock = threading.Lock()


def _refresh_who():
    try:
        r = subprocess.run([_PYEXE, str(ABDIR / "agentbus.py"), "who", "--json"],
                           capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        raw = r.stdout.decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else None
    except Exception:
        data = None
    if data is not None:
        with _who_lock:
            _who_cache["data"] = data
            _who_cache["ts"] = time.time()


def get_who():
    with _who_lock:
        return _who_cache["data"]


def start_who_refresher(interval=20):
    """唯一的 who 刷新者:后台 20s 跑子进程 who --json 刷新 _who_cache(内含 psutil 全扫,请求路径零扫描)。"""
    def loop():
        while True:
            try:
                _refresh_who()
            except Exception:
                pass
            time.sleep(interval)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[who] 后台 who 刷新线程已启动,间隔 %ds(子进程 who --json,供上下文模块校验 role 在岗,请求零扫描)" % interval)
    return t


def build_ctxpanel():
    """纯上下文预算看板(请求路径只读缓存、零扫描/读盘):
    每行 = 一个活跃会话(窗口)的占用 + 距 65% 自动压余量。
    role 用 who 暴露的 session_id 做【精确 session→role 绑定】(MasterAgent 全解:hello 继承
    CLAUDE_CODE_SESSION_ID→presence.session_id);不再 regex 猜。匹配不上(个人会话/
    daemon 无 session_id / 未 re-hello)→ 标"未绑定",不猜。
    liveness/注册/verdict/告警 等健康项【不在此】——归 🛰 运维面板(去重原则)。"""
    ctx = get_ctx()
    if not ctx:
        return {"loading": True}
    who = get_who() or {}
    # session_id → role(权威精确绑定)。只认带 session_id 的在岗角色(daemon session_id=None 不入表)。
    sid2role = {}
    for r in who.get("roles", []):
        sidv = r.get("session_id")
        if sidv and not r.get("retired"):
            sid2role[sidv] = {"role": r.get("role"), "alive": bool(r.get("alive"))}
    src = ctx.get("rows", []) or []
    n_bound = 0
    rows = []
    for cr in src:
        bind = sid2role.get(cr.get("sid_full"))
        if bind:
            role, role_live, role_src = bind["role"], bind["alive"], "session_id"
            n_bound += 1
        else:
            role, role_live, role_src = None, False, None   # 未绑定:个人会话/daemon/未 re-hello,不猜
        rows.append({
            "is_agent": cr.get("is_agent"), "role": role, "role_live": role_live, "role_src": role_src,
            "pct": cr.get("pct"), "level": cr.get("level"), "ctx": cr.get("ctx"),
            "win": cr.get("win"), "win_known": cr.get("win_known"),
            "compact_at": cr.get("compact_at"), "margin": cr.get("margin"),
            "model": cr.get("model"), "sid": cr.get("sid"), "ts": cr.get("ts"),
            "proj": cr.get("proj"),
        })
    return {
        "ok": True, "as_of": ctx.get("as_of"), "rows": rows, "n_bound": n_bound,
        "red": _CTX_RED, "yellow": _CTX_YELLOW, "compact_frac": _CTX_COMPACT_FRAC,
        "active_min": _CTX_ACTIVE_MIN,
        "max_pct": ctx.get("max_pct", 0.0), "n_red": ctx.get("n_red", 0),
        "n_yellow": ctx.get("n_yellow", 0), "n": ctx.get("n", 0),
    }


# =============================================================
#  提示词审核 —— 只读展示【当前喂给 agent 的提示词原文】供审核措辞。
#  小文件,请求路径直接读(手动刷新,不设后台 poller/缓存)。每节标源路径+mtime。只读不回写。
# =============================================================
def build_prompts():
    sections = []

    def add(key, title, path):
        p = Path(path)
        try:
            st = p.stat()
            content = p.read_text(encoding="utf-8", errors="replace")
            sections.append({"key": key, "title": title, "path": str(p),
                             "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                             "bytes": st.st_size, "lines": content.count("\n") + 1, "content": content,
                             "openable": True})  # 真实文件 → 允许 📂开文件(白名单源见 open_file)
        except Exception as e:
            sections.append({"key": key, "title": title, "path": str(p), "error": str(e)})

    # ① hello onboarding 预览(调 MasterAgent 的 `ab hello --preview`;入口未就绪则诚实占位)
    try:
        r = subprocess.run([_PYEXE, str(ABDIR / "agentbus.py"), "hello", "--preview"],
                           capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        out = r.stdout.decode("utf-8", "replace")
        if r.returncode == 0 and out.strip():
            sections.append({"key": "hello", "title": "① hello onboarding 预览", "path": "ab hello --preview",
                             "mtime": "实时", "bytes": len(out.encode("utf-8")),
                             "lines": out.count("\n") + 1, "content": out})
        else:
            err = (r.stderr.decode("utf-8", "replace") or "")[:200]
            sections.append({"key": "hello", "title": "① hello onboarding 预览", "path": "ab hello --preview",
                             "error": "入口尚未就绪(待 MasterAgent 提供 hello --preview);" + err})
    except Exception as e:
        sections.append({"key": "hello", "title": "① hello onboarding 预览", "path": "ab hello --preview",
                         "error": "待 MasterAgent 提供 hello --preview 入口:" + str(e)})

    add("global", "② 全局 CLAUDE.md", str(Path.home() / ".claude" / "CLAUDE.md"))

    # ③ 项目 CLAUDE.md:命名两份 + 活跃 agent cwd 里有 CLAUDE.md 的
    projset = {str(Path(__file__).resolve().parent.parent): 1}
    who = get_who() or {}
    for rr in who.get("roles", []):
        cwd = rr.get("cwd")
        if cwd:
            projset[cwd] = 1
    for cwd in sorted(projset):
        cp = Path(cwd) / "CLAUDE.md"
        if cp.exists():
            add("proj:" + cwd, "③ 项目 CLAUDE.md · " + cwd, str(cp))

    # ④ 基座文档层
    BASE = Path(__file__).resolve().parent.parent
    for fn in ("AGENTS.md", "CAPABILITIES.md", "BUS_ONBOARDING.md"):
        add("base:" + fn, "④ 基座 · " + fn, str(BASE / fn))

    return {"ok": True, "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"), "sections": sections}


def open_file(path):
    """📂开文件端点后端(POST /api/open-file)。
    ★★安全硬约束(infra 红线):只允许 build_prompts() 当前 openable 的【真实文件 path】白名单内的路径,
    绝不接受任意路径(否则 127.0.0.1 WebUI 变任意文件打开器=攻击面)。os.startfile=Windows 默认程序打开,
    纯本机行为无外发·合规。白名单每次请求现算(跟随当前 sections)。"""
    try:
        allow = {s.get("path") for s in build_prompts().get("sections", []) if s.get("openable")}
    except Exception as e:
        return {"ok": False, "error": "白名单构建失败:" + str(e)}
    if not path:
        return {"ok": False, "error": "缺 path"}
    if path not in allow:
        return {"ok": False, "error": "path 不在白名单(拒绝任意路径打开):" + str(path)}
    try:
        os.startfile(path)  # noqa: S606  # Windows 默认程序;path 已过白名单
        return {"ok": True, "opened": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================
#  长期任务线看板(阶段2·Master拍板·面板侧出面板/数据侧出数据)——读 tasklines.json,
#  按 decision.ref 交叉 todos.json 状态判"待用户决策"。只读·直读小文件·无缓存。
#  数据侧口径由 MasterAgent 维护(ab tasklines);此处仅呈现 + 决策点交叉高亮。
# =============================================================
TASKLINES = ABDIR / "tasklines.json"
_DECISION_ANSWERED = {"完成", "已完成", "done", "Done", "已答", "closed"}

def build_tasklines():
    out = {"ok": True, "now": time.strftime("%H:%M:%S")}
    try:
        data = json.loads(TASKLINES.read_text(encoding="utf-8"))
    except Exception as e:
        out.update({"ok": False, "error": str(e), "tasklines": [], "count": 0, "pending_total": 0})
        return out
    tls = data.get("tasklines", data if isinstance(data, list) else [])
    # decision.ref -> 待办池 status(交叉 todos.json;待用户=非已答)
    ref2status = {}
    try:
        todos = json.loads(TODOS.read_text(encoding="utf-8"))
        items = todos if isinstance(todos, list) else todos.get("todos", [])
        for it in items:
            if isinstance(it, dict) and it.get("ref"):
                ref2status[it["ref"]] = it.get("status")
    except Exception:
        pass
    result = []
    pend_total = 0
    for t in tls:
        decs = []
        pend = 0
        for de in (t.get("decisions") or []):
            ref = de.get("ref")
            st = ref2status.get(ref)
            answered = (st in _DECISION_ANSWERED)
            if not answered:
                pend += 1
            decs.append({
                "ref": ref,
                "q": de.get("q") or de.get("title") or de.get("desc") or "",
                "status": st or "(待办池无此ref)",
                "answered": answered,
            })
        pend_total += pend
        result.append({
            "id": t.get("id"), "title": t.get("title", ""), "owner": t.get("owner", ""),
            "cadence": t.get("cadence", ""), "status": t.get("status", ""),
            "current": t.get("current", ""), "deps": t.get("deps", []) or [],
            "truth_source": t.get("truth_source", ""), "updated": t.get("updated", ""),
            "decisions": decs, "pending_decisions": pend, "total_decisions": len(decs),
        })
    out.update({"tasklines": result, "count": len(result), "pending_total": pend_total,
                "updated": (data.get("updated") if isinstance(data, dict) else "")})
    return out


# =============================================================
#  🧩 工作组看板 —— 池化 bus 的组视图(Phase1-1a:join/leave/send-group/共享inbox/via_group/affinity)。
#  ①成员&激活进度 ②组inbox深度 ⑥via_group溯源 = 本模块读 _groups/<g>/ + events.jsonl 元数据。
#  ③per成员负载 ④有效并行系数 ⑤组内并行率 = 口径归使用方,填入 _group_load_metrics()(待其算法片段)。
#  只读元数据不读会话正文;TTL 缓存后台刷同 archive/健康。single-owner=MasterUI 改 server.py。
# =============================================================
_GROUPS_DIR = ABDIR / "_groups"
_GROUPS_TTL = 30.0
_groups_cache = {"ts": 0.0, "data": None}
_groups_lock = threading.Lock()


_GROUP_METRICS_WINDOW = 24 * 3600     # 默认 24h 窗(使用方口径默认;组活稀疏可后续放 7d)
_group_metrics_fn = None
def _load_group_metrics_fn():
    """import 使用方 的权威口径 group_metrics(单一真相源=agent_fleet/group_metrics_spec.py,
    它 owns 口径、改口径不动 server.py)。import 失败→返回 None、build_groups 降级 metrics=None 不崩。"""
    global _group_metrics_fn
    if _group_metrics_fn is not None:
        return _group_metrics_fn
    try:
        import sys as _sys
        _root = str(Path(__file__).resolve().parent.parent)   # ui/ -> agent_fleet/
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from group_metrics_spec import group_metrics
        _group_metrics_fn = group_metrics
    except Exception:
        _group_metrics_fn = False      # 标记 import 失败,别反复重试
    return _group_metrics_fn or None


def _group_load_metrics(group, members):
    """③④⑤ 负载分布/有效并行系数/组内并行率——口径归使用方。调其权威 group_metrics
    (events.jsonl claim/done busy 区间→时间线并发扫)。失败静默返回 None=前端显"口径未接入"。"""
    fn = _load_group_metrics_fn()
    if not fn:
        return None
    try:
        return fn(str(BUS / "events.jsonl"), group, time.time(), _GROUP_METRICS_WINDOW)
    except Exception:
        return None


def build_groups():
    with _groups_lock:
        if _groups_cache["data"] is not None and (time.time() - _groups_cache["ts"]) < _GROUPS_TTL:
            return _groups_cache["data"]
    out = {"ok": True, "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"), "groups": []}
    try:
        gdirs = [d for d in _GROUPS_DIR.iterdir() if d.is_dir()] if _GROUPS_DIR.exists() else []
    except Exception as e:
        out.update({"ok": False, "error": str(e)})
        return out
    for gd in sorted(gdirs, key=lambda d: d.name):
        g = gd.name
        if g.startswith("__"):          # 过滤测试组(__sg__/__wgtest*)
            continue
        try:
            mj = json.loads((gd / "members.json").read_text(encoding="utf-8"))
        except Exception:
            mj = {}
        members = mj.get("members") or []
        try:
            inbox_depth = len(list((gd / "inbox").glob("*.json")))
        except Exception:
            inbox_depth = 0
        out["groups"].append({
            "group": g,
            "members": members,
            "n_joined": len(members),
            # 成员=运行时动态 join/leave 无固定分母(使用方口径);target_size=可选软目标(组注册 --target·MasterAgent lane),
            # 有则显"2/3"进度、无则只显当前数量。别硬编 roster。
            "target": mj.get("target_size"),
            "caps": mj.get("caps") or [],
            "updated": mj.get("updated", ""),
            "inbox_depth": inbox_depth,
            "metrics": _group_load_metrics(g, members),   # ③④⑤ 待使用方算法
        })
    out["count"] = len(out["groups"])
    with _groups_lock:
        _groups_cache["data"] = out
        _groups_cache["ts"] = time.time()
    return out


# =============================================================
#  🔧 机制注册表 —— bus 提供哪些功能的单一视图(使用方 出数据·MasterUI render)。
#  机制≠能力池:caps=谁被授权做什么(培训师·粗·授权),机制=bus 提供哪些功能(MasterAgent/数分·细·架构盘点)。
#  related_cap 只是"该类机制关联哪个 cap 族"的交叉链,非把机制塞进 caps。静态小文件请求路径直读·只读。
# =============================================================
_MECHANISMS = Path(r"C:\apphome\agent_fleet\bus_mechanisms.json")

def build_mechanisms():
    try:
        d = json.loads(_MECHANISMS.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e), "meta": {}, "categories": []}
    cats = d.get("categories", []) or []
    # 每类补机制数 + 状态分布(供前端计数条,不改数据)
    for c in cats:
        ms = c.get("mechanisms", []) or []
        c["count"] = len(ms)
        sd = {}
        for m in ms:
            sd[m.get("status", "?")] = sd.get(m.get("status", "?"), 0) + 1
        c["status_dist"] = sd
    return {"ok": True, "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "meta": d.get("meta", {}), "categories": cats,
            "n_cats": len(cats),
            "n_mech": sum(c["count"] for c in cats)}


# =============================================================
#  👤 角色注册表(纯静态文档型·读 role_profiles.json·MasterAgent 维护单一真相源·仿 build_mechanisms)。
#  用户令细化管理角色:每角色 layer/one_liner/cwd/mode/caps + 启动流程 + 明式/隐式输入 + 边界。
#  静态读·只读文件·无实时 telemetry·无 refresher 线程·数据不出本机。
# =============================================================
_ROLE_PROFILES = Path(r"C:\apphome\agent_fleet\role_profiles.json")


def build_role_profiles():
    try:
        d = json.loads(_ROLE_PROFILES.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e), "meta": {}, "roles": []}
    return {"ok": True, "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "meta": d.get("meta", {}), "roles": d.get("roles", []) or [],
            "n_roles": len(d.get("roles", []) or [])}


# =============================================================
#  🗂️ 长程任务·锚定生长网看板(P0:脊柱+当前段+进度)——使用方简报/MasterAgent出生长引擎+数据、MasterUI render。
#  数据源=longrange_tasks.json(schema 与 MasterAgent 对齐中·P0提案);文件缺=优雅显"待MasterAgent数据引擎产出"不崩。
#  静态读·只读元数据。P0只渲①脊柱②当前活跃段节点(状态色/blocked-on)④进度%;三角色/决策合批/复杂度曲线留 P1/P2。
# =============================================================
_LONGRANGE = BUS / "longrange_tasks.json"     # MasterAgent 生长引擎产出于规范 bus 路径(非 agent_fleet/)

def build_longrange():
    if not _LONGRANGE.exists():
        return {"ok": True, "pending": True, "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "tasks": [], "note": "P0:待 MasterAgent 生长引擎产出 longrange_tasks.json(schema 对齐中)"}
    try:
        d = json.loads(_LONGRANGE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e), "tasks": []}
    tasks = d.get("tasks", d if isinstance(d, list) else []) or []
    # ★P1 决策合批单:跨任务把"当前待人拍板的点"聚成一张单(一处看/一处批,不零售)。
    # 优先用 MasterAgent/使用方 显式 emit 的 d.pending_decisions;缺则由 MasterUI 从数据派生
    #   —— 派生口径:① 脊柱锚 decision_gate=true 且 status==active(里程碑闸待拍)
    #             ② 活跃段节点 status==await_decision(节点待拍)
    # 派生只做"聚合视图",实际拍板仍走总线(ab ask-user / done)——UI 是汇总面不是审批入口。
    # 优先级:① MasterAgent 显式 task.pending_decision_batch(权威·带 batch_id/autonomy/precheck)
    #        ② 顶层 d.pending_decisions ③ 都无→MasterUI 从锚闸/await_decision 派生。
    pend = list(d.get("pending_decisions") or [])
    batches = []   # 每任务一张待批单(MasterAgent 权威口径)
    for t in tasks:
        tid, ttl = t.get("id", ""), t.get("title", t.get("id", ""))
        b = t.get("pending_decision_batch")
        if b and isinstance(b, dict) and b.get("decisions"):
            batches.append({"task_id": tid, "task_title": ttl, **b})
    if batches:
        derived = False
        # 把 batch 里的决策也铺进 pend(供顶层"共 N 待批"计数与聚合视图)
        for b in batches:
            for dc in b.get("decisions", []):
                pend.append({"task_id": b["task_id"], "task_title": b["task_title"], "kind": "node",
                             "label": dc.get("label", dc.get("node", "")), "question": dc.get("acceptance") or "",
                             "batch_id": b.get("batch_id", ""), "autonomy": dc.get("autonomy", ""),
                             "precheck": dc.get("precheck") or {}, "node": dc.get("node", "")})
    elif not pend:
        derived = True
        for t in tasks:
            tid, ttl = t.get("id", ""), t.get("title", t.get("id", ""))
            for a in (t.get("spine") or []):
                if a.get("decision_gate") and a.get("status") == "active":
                    pend.append({"task_id": tid, "task_title": ttl, "kind": "anchor-gate",
                                 "label": a.get("anchor", ""), "question": a.get("acceptance") or "",
                                 "since": t.get("updated", "")})
            seg = t.get("active_segment") or {}
            for n in (seg.get("nodes") or []):
                if n.get("status") == "await_decision":
                    pend.append({"task_id": tid, "task_title": ttl, "kind": "node",
                                 "label": n.get("label", n.get("id", "")),
                                 "question": n.get("acceptance") or "", "since": t.get("updated", ""),
                                 "owner": n.get("owner") or "", "autonomy": n.get("autonomy", ""),
                                 "precheck": n.get("precheck") or {}})
    else:
        derived = False
    # 透传 MasterAgent 给的颜色口径 + schema 版本(前端据此上色,不自造口径)
    return {"ok": True, "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tasks": tasks, "count": len(tasks),
            "schema": d.get("schema", ""), "updated": d.get("updated", ""),
            "status_colors": d.get("status_colors", {}),
            "anchor_status_colors": d.get("anchor_status_colors", {}),
            "cross_edges": d.get("cross_edges", []),
            "pending_decisions": pend, "decisions_derived": derived,
            "decision_batches": batches}


def resolve_lr_batch(task_id, approvals):
    """★P1 决策合批·写回引擎(MasterAgent 定口径 a·直接 import·同步·无子进程窗口)。
    approvals={node_id:"approve"|"reject"};approve→节点 ready 交执行,reject→failed(verdict=user_rejected),
    未答由引擎默认 reject(保守·宁卡不误做不可逆)。longrange_net.resolve_batch 内部 _save_net→_rebuild_board。"""
    if not task_id or not isinstance(approvals, dict):
        return {"ok": False, "error": "task_id/approvals 必填(approvals={node_id:approve|reject})"}
    import sys as _sys
    p = str(ABDIR)
    if p not in _sys.path:
        _sys.path.insert(0, p)
    try:
        import longrange_net
        applied = longrange_net.resolve_batch(task_id, approvals)
        return {"ok": True, "applied": applied}
    except Exception as e:
        return {"ok": False, "error": str(e)}


