#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多 agent 管理 WebUI — agent bus 的可视化控制台。

本机内部工具,只绑 127.0.0.1。复用 agentbus.py 的数据模型与函数,不另写一套逻辑。

起服务:  python C:/apphome/agent_fleet/ui/server.py
          可选:  --port 8799  --host 127.0.0.1

功能:
  A 舰队总览(同 who):verdict / claude_pid / 心跳age / cwd + 在跑但未认领进程
  B 队列(同 list):每 role inbox/processing/done 计数,可点开看任务 JSON
  C 派活:表单 → 复用 agentbus send --from Master
  D 回填查看:读 <role>/done(默认 Master),展示结果,可清除
  E 自动刷新:watchdog 文件事件 → SSE 推送;客户端可调轮询兜底

隐私模型(用户 2026-07-02 定):【只有显式标记(pid_tags.json private=true)的才藏,其余一律如实显示】。
  - private=true = 用户主动 opt-in 隐藏(如截图/投屏给外部那刻):只显 pid+label🔒,不探 cwd。
  - 未标记 / 已标注非 private:如实显 cwd/proj/标题(自看控制台绑 127.0.0.1、唯一观众=所有者本人,
    无外部方→默认不脱敏;掩码只丢信息,如看不出哪个个人会话吃上下文)。旧「自动掩码个人会话」已去除。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ---- 复用 agentbus.py:它是总线的真相源 ----
# ABDIR 必须在 import agentbus 之前就绪,故不能用 ab.cfg;改为 env AGENTBUS_DIR 或 ~/.claude/agent_bus
# (与 agentbus.BUS 默认一致),不再硬编码用户名路径,换机/换用户即可用。
ABDIR = Path(os.path.expandvars(os.path.expanduser(os.environ.get("AGENTBUS_DIR") or str(Path.home() / ".claude" / "agent_bus"))))
sys.path.insert(0, str(ABDIR))
import agentbus as ab  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BUS = ab.BUS


# =============================================================
#  数据采集 —— 全部复用 agentbus 的 helper,不复制其逻辑
# =============================================================

def _roles():
    """真正的 role 目录 —— 直接复用 agentbus._list_roles()(真相源)。
    它已排除 __pycache__ 及以 . 或 _ 开头的非 role 目录,UI 不再自己判定。"""
    return ab._list_roles()


def _count(role, sub):
    d = BUS / role / sub
    return len(list(d.glob("*.json"))) if d.exists() else 0


def _read_tasks(role, subs=("inbox", "processing", "held", "failed")):
    """读某 role 的待办/待批/毒任务(完整 JSON,供前端点开看)。
    带出 requires_approval 字段:inbox 里 true 的是高风险任务,自动监听者不领,需人工 wait --include-approval。"""
    out = []
    for sub in subs:
        d = BUS / role / sub
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                o = {"id": p.stem, "title": "(无法解析)", "task": ""}
            out.append({
                "sub": sub,
                "id": o.get("id", p.stem),
                "from": o.get("from", "?"),
                "to": o.get("to", role),
                "title": o.get("title", ""),
                "task": o.get("task", ""),
                "status": o.get("status", ""),
                "created": o.get("created", ""),
                "claimed": o.get("claimed", ""),
                "requires_approval": bool(o.get("requires_approval")),
                "requeue_count": o.get("requeue_count", 0),
            })
    return out


def _safe_cwd(pid):
    """探进程 cwd（供如实显示未标记/非private 进程）。探不到回 None，绝不抛。"""
    try:
        return ab.psutil.Process(pid).cwd()
    except Exception:
        return None


def _turn_state(role):
    """读 <role>/turn_state.json(turn_state.py hook 盖的实时 turn 状态)。返回 {state,label,tool,age} 或 None。
    真实性:数据只来自 hook 事件(UserPromptSubmit→thinking / Pre·PostToolUse→tool·thinking / Stop→idle),
    非推断;age=now−ts(秒)供前端显示时长 + 判陈旧(如 thinking 太久=可能漏了 Stop)。"""
    p = BUS / role / "turn_state.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {"state": d.get("state"), "label": d.get("label"),
            "tool": d.get("tool"), "event": d.get("event"),
            "age": ab._age_seconds(d.get("ts", ""))}


def _vclass_of(rec):
    """从 _who_data 的 rec 派生【显示类】vclass(badge 颜色)—— 纯 UI 派生,非 verdict 判定。
    复刻旧 compute_fleet 的 verdict→vclass 映射,保 tray/count 口径不漂(使用方 role-verdict 收敛铁律)。
    tray 计数据此:live/idle/warn=在岗·parked=休眠·dead/none=不计在岗。"""
    if rec.get("pseudo") or rec.get("retired"):
        return "none"          # 伪角色/退役:不判在线掉线,不计在岗
    if rec.get("parked"):
        return "parked"
    v = rec.get("verdict") or ""
    if "损坏" in v:
        return "dead"          # presence 损坏
    if v.startswith("未声明"):
        return "none"
    if v.startswith("✓"):
        return "live"          # ✓worker在听 / ✓daemon在听
    if v.startswith("✗"):
        return "dead"          # ✗进程已退(残留) / ✗daemon已退
    if "静默" in v:
        return "warn"          # ⚠worker静默(超心跳)
    if v.startswith("已声明"):
        return "idle"          # 已声明(hello),未起循环
    if "(无pid)" in v or v == "daemon?":
        return "warn"
    return "idle"              # daemon(心跳)/心跳前(mode=) 等 psutil-off 兜底


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
                             "bytes": st.st_size, "lines": content.count("\n") + 1, "content": content})
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


# =============================================================
#  HTTP
# =============================================================
INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Bus 控制台</title>
<style>
:root{
  --bg:#0f1115; --panel:#171a21; --panel2:#1d212b; --line:#2a2f3a;
  --txt:#e6e9ef; --dim:#9aa3b2; --accent:#5b9dff; --accent2:#2a3a5a;
  --live:#3ecf8e; --warn:#f5a623; --dead:#ff5d5d; --idle:#7d8aa0; --none:#5a6172;
}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 "Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--txt)}
a{color:var(--accent)}
header{display:flex;align-items:center;gap:14px;padding:12px 18px;background:var(--panel);
  border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}
header h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.5px}
.spacer{flex:1}
.pill{font-size:12px;padding:3px 10px;border-radius:20px;background:var(--panel2);
  border:1px solid var(--line);color:var(--dim);display:inline-flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:var(--idle)}
.dot.on{background:var(--live);box-shadow:0 0 6px var(--live)}
.dot.off{background:var(--dead)}
main{padding:18px;max-width:1500px;margin:0 auto;display:grid;gap:18px}
.grid2{display:grid;grid-template-columns:1.7fr 1fr;gap:18px}
@media(max-width:1080px){.grid2{grid-template-columns:1fr}}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.sec-h{padding:11px 16px;border-bottom:1px solid var(--line);font-weight:600;font-size:13px;
  display:flex;align-items:center;gap:10px;background:var(--panel2)}
.sec-h .sub{font-weight:400;color:var(--dim);font-size:12px}
.sec-b{padding:8px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 16px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.6px}
tr:last-child td{border-bottom:none}
tr.role-row{cursor:default}
tr.role-row.clickable{cursor:pointer}
tr.role-row.clickable:hover{background:var(--panel2)}
.badge{font-size:11px;padding:2px 9px;border-radius:6px;white-space:nowrap;font-weight:600}
.badge.vbadge{display:inline-block;max-width:260px;overflow:hidden;text-overflow:ellipsis;vertical-align:bottom}
.badge.live{background:rgba(62,207,142,.15);color:var(--live)}
.badge.warn{background:rgba(245,166,35,.15);color:var(--warn)}
.badge.dead{background:rgba(255,93,93,.15);color:var(--dead)}
.badge.idle{background:rgba(125,138,160,.15);color:var(--idle)}
.badge.none{background:rgba(90,97,114,.15);color:var(--none)}
.badge.parked{background:rgba(167,139,250,.16);color:#a78bfa}
.mono{font-family:"Cascadia Code",Consolas,monospace;font-size:12px}
.cwd{color:var(--dim);font-size:12px;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.qcount{display:inline-block;min-width:30px;text-align:center;padding:1px 8px;border-radius:6px;
  font-variant-numeric:tabular-nums;font-weight:600;font-size:12px}
.qcount.z{color:var(--none);background:transparent}
.qcount.ib{background:rgba(91,157,255,.16);color:var(--accent)}
.qcount.pr{background:rgba(245,166,35,.16);color:var(--warn)}
.qcount.dn{background:rgba(62,207,142,.14);color:var(--live)}
.qcount.held{background:rgba(245,166,35,.22);color:var(--warn);box-shadow:0 0 0 1px rgba(245,166,35,.5)}
.qcount.failed{background:rgba(255,93,93,.22);color:var(--dead);box-shadow:0 0 0 1px rgba(255,93,93,.55)}
.badge.appr{background:rgba(245,166,35,.18);color:var(--warn);border:1px solid rgba(245,166,35,.5)}
.badge.held{background:rgba(245,166,35,.15);color:var(--warn)}
.badge.failed{background:rgba(255,93,93,.15);color:var(--dead)}
.hint{font-size:11px;color:var(--warn);margin-top:4px}
.ev{font-size:12px;padding:5px 16px;border-bottom:1px solid var(--line);display:flex;gap:9px;align-items:baseline;flex-wrap:wrap}
.ev:last-child{border-bottom:none}
.ev .et{font-size:11px;padding:1px 7px;border-radius:5px;font-weight:600}
.ev .et.send{background:rgba(91,157,255,.16);color:var(--accent)}
.ev .et.claim{background:rgba(245,166,35,.16);color:var(--warn)}
.ev .et.done{background:rgba(62,207,142,.16);color:var(--live)}
.ev .ets{color:var(--none);font-variant-numeric:tabular-nums}
.dmn{display:flex;align-items:center;gap:10px;padding:8px 16px;border-bottom:1px solid var(--line);font-size:13px}
.dmn:last-child{border-bottom:none}
.chk{display:flex;align-items:center;gap:7px;margin:10px 16px 4px;font-size:12px;color:var(--dim);cursor:pointer}
.chk input{width:auto;margin:0}
.tasks{background:var(--bg)}
.tasks td{padding:0 16px}
.task-wrap{padding:10px 0}
.task-card{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:6px 0}
.task-card .t-h{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--dim);margin-bottom:5px}
.task-card .t-title{color:var(--txt);font-weight:600}
.task-card pre{margin:6px 0 0;white-space:pre-wrap;word-break:break-word;font-size:12px;color:#cdd4e0;
  background:var(--bg);padding:8px 10px;border-radius:6px;max-height:240px;overflow:auto}
.foot{padding:10px 16px;color:var(--dim);font-size:12px;border-top:1px solid var(--line)}
label{display:block;font-size:12px;color:var(--dim);margin:10px 16px 4px}
input[type=text],textarea,select{width:calc(100% - 32px);margin:0 16px;background:var(--bg);
  color:var(--txt);border:1px solid var(--line);border-radius:7px;padding:8px 10px;font:13px/1.4 inherit}
textarea{min-height:120px;resize:vertical;font-family:"Cascadia Code",Consolas,monospace}
.btn{margin:14px 16px 16px;background:var(--accent);color:#06112a;border:none;border-radius:7px;
  padding:9px 18px;font-weight:600;cursor:pointer;font-size:13px}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:transparent;color:var(--dim);border:1px solid var(--line);font-weight:500}
.btn.danger{background:transparent;color:var(--dead);border:1px solid rgba(255,93,93,.4)}
.btn:disabled{opacity:.5;cursor:default}
.reply{margin:8px 16px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.reply .r-h{display:flex;gap:8px;align-items:center;font-size:12px;margin-bottom:5px;flex-wrap:wrap}
.reply pre{margin:5px 0 0;white-space:pre-wrap;word-break:break-word;font-size:12px;color:#cdd4e0;
  background:var(--bg);padding:8px 10px;border-radius:6px;max-height:200px;overflow:auto}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tag{font-size:12px;padding:3px 9px;border-radius:6px;background:var(--accent2);color:#cfe0ff;margin:2px}
.tag.lock{background:rgba(245,166,35,.16);color:var(--warn)}
.muted{color:var(--dim)}
.toast{position:fixed;right:18px;bottom:18px;background:var(--panel2);border:1px solid var(--line);
  border-left:3px solid var(--live);border-radius:8px;padding:11px 16px;max-width:380px;
  box-shadow:0 8px 30px rgba(0,0,0,.5);opacity:0;transform:translateY(10px);transition:.25s;font-size:13px}
.toast.show{opacity:1;transform:none}
.toast.err{border-left-color:var(--dead)}
.ctrl{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--dim)}
.ctrl select{width:auto;margin:0;padding:4px 8px}
.flash{animation:flash 1s ease}
@keyframes flash{0%{background:rgba(91,157,255,.25)}100%{background:transparent}}
/* tab 分页 */
.tabbar{position:sticky;top:0;z-index:9;display:flex;gap:6px;padding:9px 18px;background:var(--panel);
  border-bottom:1px solid var(--line);flex-wrap:wrap;box-shadow:0 2px 8px rgba(0,0,0,.25)}
.tab{background:var(--panel2);color:var(--dim);border:1px solid var(--line);border-radius:8px;
  padding:7px 16px;font:13px/1 "Segoe UI",system-ui,sans-serif;cursor:pointer;font-weight:500}
.tab:hover{color:var(--txt);border-color:#3a4150}
.tab.active{background:var(--accent);color:#06112a;border-color:var(--accent);font-weight:700}
.tabhide{display:none !important}
.gconfirm{font-size:12px;font-weight:700;padding:3px 11px;border-radius:8px;cursor:pointer;
  background:rgba(154,123,255,.2);color:#c9b3ff;border:1px solid rgba(154,123,255,.6)}
.gconfirm:hover{background:rgba(154,123,255,.34)}
/* bus 健康徽标 */
.hwrap{position:relative;display:inline-block}
.hbadge{font-size:12px;font-weight:700;padding:3px 11px;border-radius:8px;cursor:pointer;border:1px solid;white-space:nowrap}
.hbadge.ok{background:rgba(60,200,120,.16);color:#7ee0a6;border-color:rgba(60,200,120,.55)}
.hbadge.warn{background:rgba(240,190,70,.16);color:#f0cf7a;border-color:rgba(240,190,70,.6)}
.hbadge.loading{background:var(--bg);color:var(--muted);border-color:var(--line)}
.hbadge.danger{background:rgba(255,93,93,.18);color:#ff9b9b;border-color:rgba(255,93,93,.6)}
.pmt-sec{border:1px solid var(--line);border-radius:8px;margin:8px 0;overflow:hidden;background:var(--panel2)}
.pmt-sec.err{border-color:rgba(245,166,35,.5)}
.pmt-h{display:flex;gap:12px;align-items:baseline;padding:9px 12px;cursor:pointer;user-select:none}
.pmt-h:hover{background:rgba(91,157,255,.08)}
.pmt-t{font-weight:600;color:var(--txt)}
.pmt-meta{font-size:11px;color:var(--dim);margin-left:auto;text-align:right;word-break:break-all}
.pmt-b{margin:0;padding:12px 14px;border-top:1px solid var(--line);background:var(--bg);
  font:12px/1.55 Consolas,monospace;color:var(--txt);white-space:pre-wrap;word-break:break-word;max-height:60vh;overflow:auto}
.ctx-kpis{display:flex;gap:10px;flex-wrap:wrap;padding:12px 16px 6px}
.ctx-kpis .k{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:8px 14px;min-width:96px}
.ctx-kpis .k .v{font-size:18px;font-weight:700}
.ctx-kpis .k .l{font-size:11px;color:var(--dim)}
.ctxpct{font-weight:700;font-variant-numeric:tabular-nums}
.ctxpct.green{color:var(--live)} .ctxpct.yellow{color:var(--warn)} .ctxpct.red{color:var(--dead)}
.ctxbar{display:inline-block;height:7px;border-radius:4px;vertical-align:middle;margin-left:7px;min-width:2px;background:currentColor;opacity:.55}
tr.ctx-red td{background:rgba(255,93,93,.08)}
.winassumed{color:var(--dim);font-size:10.5px}
.hpanel{position:absolute;top:30px;left:0;z-index:30;width:360px;max-height:70vh;overflow:auto;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
  box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:12px;line-height:1.5;cursor:default}
.hpanel h4{margin:0 0 6px;font-size:12px;color:var(--txt)}
.hpanel .hwarn{color:#f0cf7a;background:rgba(240,190,70,.1);border-radius:5px;padding:3px 8px;margin:3px 0}
.hpanel .hblk{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-top:1px solid var(--line)}
.hpanel .hblk b{color:var(--txt);font-weight:600}
.hpanel .hk{color:var(--muted)}
.hpanel .hbad{color:var(--dead)} .hpanel .hgood{color:#7ee0a6}
/* 历史用量 BI */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;padding:14px 16px}
.kpi{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px 12px}
.kpi .k{font-size:11px;color:var(--dim)}
.kpi .v{font-size:20px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi .v.good{color:var(--live)} .kpi .v.acc{color:var(--accent)} .kpi .v.warn{color:var(--warn)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:0 4px}
@media(max-width:880px){.grid2{grid-template-columns:1fr}}
.uchart-cap{font-size:12px;color:var(--dim);padding:4px 16px 8px;font-weight:600}
canvas{display:block;width:100%}
.bars{padding:2px 16px 12px}
.bar-row{display:grid;grid-template-columns:130px 1fr 96px;gap:10px;align-items:center;margin:7px 0;font-size:12.5px}
.bar-row .lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{height:15px;background:var(--line);border:1px solid #333a47;border-radius:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,#5b9dff,#8ab8ff);box-shadow:0 0 0 1px rgba(91,157,255,.25)}
.bar-row .num{text-align:right;font-family:var(--mono);font-size:12px;color:var(--dim)}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--dim);padding:6px 16px 14px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
/* 历史用量:范围切换 + 趋势 hover + 项目下钻 */
.urange{display:inline-flex;gap:2px;margin-left:8px;vertical-align:middle}
.urange button{background:var(--panel2);color:var(--dim);border:1px solid var(--line);font:600 11px Consolas,monospace;padding:2px 8px;border-radius:4px;cursor:pointer}
.urange button:hover{color:var(--txt)}
.urange button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.uchart-wrap{position:relative}
.uchart-tip{position:absolute;pointer-events:none;display:none;background:rgba(10,12,16,.96);border:1px solid var(--line);border-radius:5px;padding:5px 8px;font:11px Consolas,monospace;color:#e6ebf2;white-space:nowrap;z-index:5;box-shadow:0 2px 8px rgba(0,0,0,.45)}
.trend-legend{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:4px 16px 2px;font-size:11px;color:var(--dim)}
.trend-legend span{display:inline-flex;align-items:center;gap:5px}
.trend-legend i{width:12px;height:8px;border-radius:2px;display:inline-block}
.trend-legend i.gapi{background:repeating-linear-gradient(45deg,rgba(125,138,160,.5),rgba(125,138,160,.5) 2px,transparent 2px,transparent 4px);border:1px solid var(--line)}
.uproj-row{cursor:pointer}
.uproj-row:hover .lbl{color:var(--accent)}
.uproj-raw{padding:3px 0 7px 10px;margin:-2px 0 5px;border-left:2px solid var(--line)}
.uproj-raw .bar-row{margin:5px 0;font-size:11.5px}
/* 派工/协作效率(仅自动化派工口径)*/
.dm-note{margin:0 16px 4px;padding:8px 12px;font-size:12px;line-height:1.5;color:#f0d9a0;
  background:rgba(245,166,35,.10);border:1px solid rgba(245,166,35,.4);border-radius:8px}
.dm-note b{color:var(--warn)}
.kind-tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;font-weight:600;vertical-align:1px;margin-right:4px}
.kind-tag.daemon{background:rgba(91,157,255,.16);color:var(--accent)}
.kind-tag.dispatch{background:rgba(245,166,35,.16);color:var(--warn)}
/* 可恢复会话 */
.sess-row{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;margin:6px 16px;padding:9px 12px;
  background:var(--panel2);border:1px solid var(--line);border-radius:8px}
.sess-row.agent{border-left:3px solid var(--accent)}
.sess-main{min-width:0}
.sess-title{font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sess-meta{font-size:11.5px;color:var(--dim);margin-top:3px;display:flex;gap:12px;flex-wrap:wrap}
.sess-meta .mono{font-size:11px}
.sess-row .copy{white-space:nowrap}
/* 状态列:运行中(绿▶+role)/ 死(灰) */
.sess-row.live{border-left:3px solid var(--live)}
.sess-row.dead{opacity:.82}
.sess-state{font-size:11px;font-weight:700;padding:2px 9px;border-radius:6px;white-space:nowrap;display:inline-flex;align-items:center;gap:5px}
.sess-state.live{background:rgba(62,207,142,.16);color:var(--live)}
.sess-state.dead{background:rgba(125,138,160,.14);color:var(--idle)}
.sess-state .role{color:var(--live);font-weight:700}
/* 🏷 标签 chip */
.sess-tags{display:inline-flex;gap:5px;flex-wrap:wrap}
.tag-chip{font-size:11px;padding:1px 8px;border-radius:20px;background:rgba(91,157,255,.15);color:var(--accent);border:1px solid rgba(91,157,255,.3)}
/* 会话筛选钮 + 筛选组 */
.sess-filt,.sess-own{margin:0;padding:3px 11px;font-size:12px}
.sess-filt.active,.sess-own.active{background:var(--accent);color:#06112a;border-color:var(--accent);font-weight:600}
.filt-grp{display:inline-flex;align-items:center;gap:4px;margin-left:8px}
.filt-lbl{font-size:11px;color:var(--dim);margin-right:2px}
/* 归属 chip:bus / 个人 */
.own-chip{font-size:11px;padding:1px 8px;border-radius:6px;white-space:nowrap;font-weight:600}
.own-chip.bus{background:rgba(91,157,255,.15);color:var(--accent)}
.own-chip.personal{background:rgba(125,138,160,.14);color:var(--idle)}
/* 待办池 TODO Pool */
.todo-card{margin:8px 16px;background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px 12px;border-left:3px solid var(--line)}
.todo-card.pri-hi{border-left-color:var(--dead)}
.todo-card.pri-mid{border-left-color:var(--warn)}
.todo-card.pri-lo{border-left-color:var(--idle)}
.todo-card.is-done{opacity:.6}
/* 待确认:agent 在等用户拍板,醒目区分 */
.todo-card.pending-confirm{border-left-color:#9a7bff !important;background:rgba(154,123,255,.10);box-shadow:0 0 0 1px rgba(154,123,255,.4)}
.badge.confirm{background:rgba(154,123,255,.20);color:#c9b3ff;border:1px solid rgba(154,123,255,.55)}
.confirm-who{color:#c9b3ff;font-weight:600}
/* 待办池视图切换 tab */
.tview-btn{margin:0;padding:3px 11px;font-size:12px}
.tview-btn.active{background:var(--accent);color:#06112a;border-color:var(--accent);font-weight:600}
.confirm-alert{font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:5px;background:rgba(154,123,255,.18);color:#c9b3ff;border:1px solid rgba(154,123,255,.5)}
.todo-h{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.todo-title{font-weight:600;font-size:13.5px}
.todo-detail{color:#cdd4e0;font-size:12.5px;margin:6px 0 0;white-space:pre-wrap;word-break:break-word}
.todo-meta{color:var(--dim);font-size:11.5px;margin-top:6px;display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.who-pill{background:var(--accent2);color:#cfe0ff;padding:1px 8px;border-radius:5px;font-weight:600}
.dnote{color:#cdd4e0;background:var(--bg);padding:1px 7px;border-radius:5px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.note-area{margin-top:7px;display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap}
.unote{color:#cdd4e0;background:var(--bg);padding:2px 9px;border-radius:5px;font-size:12px;white-space:pre-wrap;word-break:break-word;max-width:560px}
.nbtn{margin:0;padding:2px 9px;font-size:11px}
.note-edit{width:100%;min-height:48px;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:12px/1.45 inherit;resize:vertical}
.answer-area{margin-top:7px;padding:7px 9px;background:rgba(154,123,255,.08);border:1px solid rgba(154,123,255,.35);border-radius:7px;display:flex;gap:7px;align-items:flex-start;flex-wrap:wrap}
.ans-edit{flex:1;min-width:220px;min-height:42px;background:var(--bg);color:var(--txt);border:1px solid rgba(154,123,255,.5);border-radius:6px;padding:6px 8px;font:12px/1.45 inherit;resize:vertical}
.note-btns{display:flex;gap:6px;margin-top:5px}
.todo-acts{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
.todo-acts select,.todo-acts input{width:auto;margin:0;padding:3px 7px;font-size:12px}
.badge.p-hi{background:rgba(255,93,93,.16);color:var(--dead)}
.badge.p-mid{background:rgba(245,166,35,.16);color:var(--warn)}
.badge.p-lo{background:rgba(125,138,160,.16);color:var(--idle)}
.badge.s-todo{background:rgba(125,138,160,.16);color:var(--idle)}
.badge.s-doing{background:rgba(91,157,255,.16);color:var(--accent)}
.badge.s-done{background:rgba(62,207,142,.16);color:var(--live)}
.badge.s-hold{background:rgba(245,166,35,.16);color:var(--warn)}
.overdue{color:var(--dead);font-weight:600}
.linkpill{font-size:11px;padding:1px 7px;border-radius:5px;background:rgba(62,207,142,.13);color:var(--live)}
.btn.mini{margin:0;padding:3px 10px;font-size:12px}
.todo-add{padding:6px 16px 12px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:1fr 1fr;gap:6px 10px}
.todo-add .full{grid-column:1 / -1}
.todo-add input,.todo-add textarea,.todo-add select{width:100%;margin:0}
.todo-add textarea{min-height:48px}
.filt{display:flex;gap:6px;align-items:center}
.filt select{width:auto;margin:0;padding:3px 8px;font-size:12px}
/* 实时视图 live.log */
.livebox{margin:0;white-space:pre-wrap;word-break:break-word;font-family:"Cascadia Code",Consolas,monospace;
  font-size:12px;line-height:1.55;color:#cdd4e0;background:var(--bg);padding:12px 16px;
  height:380px;overflow:auto;border-top:1px solid var(--line)}
.live-meta{font-size:11.5px;color:var(--dim);padding:6px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--idle);display:inline-block}
.live-dot.on{background:var(--live);box-shadow:0 0 6px var(--live);animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.bi-kpis{display:flex;gap:10px;flex-wrap:wrap;padding:12px 16px 6px}
.bi-kpi{background:var(--card,#1a1d24);border:1px solid var(--line);border-radius:8px;padding:9px 14px;min-width:118px}
.bi-kpi .k{font-size:11px;color:var(--dim);margin-bottom:3px}
.bi-kpi .v{font-size:19px;font-weight:700;color:var(--txt)}
.bi-kpi .v small{display:block;font-size:11px;font-weight:400;color:var(--dim);margin-top:3px;margin-left:0}
.bi-kpi.warn .v{color:var(--warn,#e6a23c)}
.bi-run{display:inline-flex;align-items:center;gap:6px;background:rgba(80,200,120,.12);border:1px solid var(--live);border-radius:6px;padding:3px 9px;margin:3px 6px 3px 0;font-size:12px}
table.bi-tbl td,table.bi-tbl th{font-variant-numeric:tabular-nums}
table.bi-tbl td.num,table.bi-tbl thead th:not(:first-child){text-align:right}
table.bi-tbl td.num{font-family:ui-monospace,monospace}
/* 能力池 caps */
.cap-pool{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;padding:12px 16px}
.cap-card{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px 12px;border-left:3px solid var(--accent2)}
.cap-card.edge{border-left-color:var(--warn)} .cap-card.hi{border-left-color:var(--dead)}
.cap-card .cn{font-weight:700;font-size:13px;font-family:"Cascadia Code",Consolas,monospace}
.cap-card .cd{font-size:11.5px;color:var(--dim);margin-top:5px;line-height:1.45}
.cap-card .cf{font-size:11px;color:var(--idle);margin-top:5px}
.cap-card .ch{font-size:12px;margin-top:6px;font-variant-numeric:tabular-nums}
.cap-card .ch b{font-size:16px;color:var(--txt)}
.cap-card.zero .ch b{color:var(--dead)}
.rb{display:inline-block;font-size:10px;padding:1px 7px;border-radius:5px;font-weight:700;vertical-align:1px;margin-left:6px}
.rb.edge{background:rgba(245,166,35,.18);color:var(--warn);border:1px solid rgba(245,166,35,.5)}
.rb.hi{background:rgba(255,93,93,.18);color:var(--dead);border:1px solid rgba(255,93,93,.5)}
/* caps 矩阵 */
table.capmx{width:100%;border-collapse:collapse;font-size:12.5px}
table.capmx th,table.capmx td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:center}
table.capmx th:first-child,table.capmx td:first-child{text-align:left;white-space:nowrap}
table.capmx thead th{color:var(--dim);font-size:10.5px;font-weight:600;text-transform:none;letter-spacing:.3px;
  font-family:"Cascadia Code",Consolas,monospace;writing-mode:initial}
table.capmx thead th.edge{color:var(--warn)} table.capmx thead th.hi{color:var(--dead)}
table.capmx tbody tr:hover{background:var(--panel2)}
table.capmx .yes{color:var(--live);font-weight:700} table.capmx .no{color:var(--none)}
table.capmx td.cnt{color:var(--dim);font-variant-numeric:tabular-nums}
.mx-role{font-weight:600} .mx-role.ret{color:var(--idle);text-decoration:line-through}
.mx-foot{font-size:11px;color:var(--dim);padding:2px 16px 10px}
.gap-box{margin:6px 16px 4px;padding:9px 12px;border-radius:8px;font-size:12.5px;line-height:1.6}
.gap-box.todo{background:rgba(245,166,35,.10);border:1px solid rgba(245,166,35,.4)}
.gap-box.none{background:rgba(255,93,93,.08);border:1px solid rgba(255,93,93,.35)}
.gap-box.ok{background:rgba(62,207,142,.08);border:1px solid rgba(62,207,142,.3)}
.gap-box .gh{font-weight:700;margin-bottom:3px}
.cap-chip{display:inline-block;font-size:11px;padding:1px 8px;border-radius:5px;margin:2px;
  background:var(--accent2);color:#cfe0ff;font-family:"Cascadia Code",Consolas,monospace}
.cap-chip.warn{background:rgba(245,166,35,.18);color:var(--warn)}
.cap-chip.dead{background:rgba(255,93,93,.18);color:var(--dead)}
.cap-chip.live{background:rgba(62,207,142,.16);color:var(--live)}
.cap-chip.idle{background:rgba(125,138,160,.15);color:var(--idle)}
.cap-chip.none{background:rgba(90,97,114,.15);color:var(--none)}
/* 🗂️ 长程任务·锚定生长网 */
.lr-spine{display:flex;flex-wrap:wrap;align-items:center;gap:2px;margin-top:4px}
.lr-anchor{display:inline-flex;flex-direction:column;font-size:11.5px;padding:3px 9px;border-radius:6px;border:1px solid var(--line);line-height:1.3}
.lr-anchor.u{background:rgba(91,157,255,.14);border-color:rgba(91,157,255,.45)}
.lr-anchor.m{background:transparent;border-style:dashed;color:var(--dim)}
.lr-au{font-size:9px;color:var(--dim);margin-top:1px}
.lr-arrow{color:var(--dim);margin:0 3px;font-weight:700}
.lr-net{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.lr-node{font-size:11px;padding:2px 9px;border-radius:6px;border:1px solid var(--line);white-space:nowrap}
.lr-node.idle{background:rgba(125,138,160,.14);color:var(--idle)}
.lr-node.accent{background:rgba(91,157,255,.16);color:var(--accent)}
.lr-node.dead{background:rgba(255,93,93,.16);color:var(--dead)}
.lr-node.live{background:rgba(62,207,142,.16);color:var(--live)}
.lr-node.warn{background:rgba(245,166,35,.18);color:var(--warn)}
/* ⓪ Agent 详情(会哪些内容)*/
.ag-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:11px;padding:12px 16px}
.ag-card{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:11px 13px;border-left:3px solid var(--accent2);display:flex;flex-direction:column;gap:7px}
.ag-card.daemon{border-left-color:var(--idle)} .ag-card.cli{border-left-color:var(--live)}
.ag-card.off{border-left-color:var(--none)} .ag-card.ret{border-left-color:var(--dead);opacity:.82}
.ag-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ag-name{font-weight:700;font-size:14px}
.ag-card.ret .ag-name{text-decoration:line-through;color:var(--idle)}
.ag-form{display:inline-block;font-size:10px;padding:1px 8px;border-radius:5px;font-weight:700;font-family:"Cascadia Code",Consolas,monospace}
.ag-form.cli{background:rgba(62,207,142,.16);color:var(--live);border:1px solid rgba(62,207,142,.45)}
.ag-form.daemon{background:rgba(106,160,255,.14);color:var(--idle);border:1px solid rgba(106,160,255,.4)}
.ag-form.off{background:rgba(150,150,150,.14);color:var(--none);border:1px solid rgba(150,150,150,.35)}
.ag-form.ret{background:rgba(255,93,93,.14);color:var(--dead);border:1px solid rgba(255,93,93,.4)}
.ag-verdict{font-size:11px;color:var(--dim);font-family:"Cascadia Code",Consolas,monospace}
.ag-succ{font-size:11px;color:var(--idle)}
.ag-desc{font-size:12px;color:var(--dim);line-height:1.5}
.ag-caps{display:flex;flex-wrap:wrap;gap:3px;margin-top:1px}
.ag-meta{font-size:10.5px;color:var(--none);margin-top:1px}
.sec-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:980px){.sec-grid{grid-template-columns:1fr}}
</style></head>
<body>
<header>
  <h1>🛰 Agent Bus 控制台</h1>
  <span class="pill"><span id="connDot" class="dot"></span><span id="connTxt">连接中…</span></span>
  <span class="pill" title="在跑 claude=机器上所有活着的 claude 进程;在岗=已认领到某 role(有 presence)的 claude 进程。两者都【不是】fleet.json 里的 daemon 数 —— daemon 看 🩺 健康徽标/舰队页。">在跑 claude <b id="aliveN" style="color:var(--txt)">–</b> · 在岗 <b id="fleetN" style="color:var(--txt)">–</b></span>
  <span id="gConfirm" class="gconfirm" style="display:none" title="点击跳到待办池查看待确认">🙋 <b>0</b> 项待确认</span>
  <span id="healthWrap" class="hwrap">
    <span id="healthBadge" class="hbadge loading" title="bus 健康(点击展开)">🩺 …</span>
    <div id="healthPanel" class="hpanel" style="display:none"></div>
  </span>
  <span id="ctxBadge" class="hbadge loading" title="上下文预算(点击看看板)·≥65%红=将被 auto-compact,建议先主动落档重置">🧠 …</span>
  <span class="spacer"></span>
  <span class="ctrl">兜底轮询
    <select id="pollSel">
      <option value="0">关(仅事件)</option>
      <option value="3000">3s</option>
      <option value="5000">5s</option>
      <option value="10000" selected>10s</option>
      <option value="30000">30s</option>
    </select>
  </span>
  <button class="btn ghost" id="refreshBtn" style="margin:0;padding:6px 14px">手动刷新</button>
  <span class="pill muted" id="updT">–</span>
</header>
<nav class="tabbar" id="tabbar">
  <button class="tab" data-t="fleet">🛰 舰队/运维</button>
  <button class="tab" data-t="bi">📊 运营/资源</button>
  <button class="tab" data-t="usage">📈 历史用量</button>
  <button class="tab" data-t="caps">🧬 能力池</button>
  <button class="tab" data-t="ctx">🧠 上下文</button>
  <button class="tab" data-t="prompts">📝 提示词审核</button>
  <button class="tab" data-t="tasklines">🎯 任务线看板</button>
  <button class="tab" data-t="groups">🧩 工作组</button>
  <button class="tab" data-t="mechanisms">🔧 机制</button>
  <button class="tab" data-t="longrange">🗂️ 长程任务</button>
  <button class="tab" data-t="tasks">📋 任务/协作</button>
  <button class="tab" data-t="live">🔴 实时</button>
  <span class="spacer" style="flex:1"></span>
  <span class="muted" style="font-size:11.5px;align-self:center" id="tabHint"></span>
</nav>

<main>
  <section data-tab="fleet">
    <div class="sec-h">A · 舰队总览 <span class="sub">role 身份 / 心跳 / 队列(同 <span class="mono">who</span>+<span class="mono">list</span>)— 点行展开任务</span></div>
    <div class="sec-b">
      <table>
        <thead><tr><th>Role</th><th>判定</th><th>turn 状态</th><th>pid<span class="sub" style="font-weight:400"> (d:=daemon)</span></th><th>心跳</th><th>inbox</th><th>proc</th><th>done</th><th>held</th><th>failed</th><th>cwd</th></tr></thead>
        <tbody id="fleetBody"></tbody>
      </table>
    </div>
    <div class="foot" id="procFoot"></div>
  </section>

  <section data-tab="bi" style="scroll-margin-top:64px">
    <div class="sec-h">📊 运营 / 资源 <span class="sub">今日消耗 = <span class="mono">usage.db</span>(全量·含前台 cli·Shanghai 日,统一运营口径)· 在跑任务 + 系统资源 <span class="mono">psutil</span> 实时</span>
      <span class="spacer" style="flex:1"></span>
      <span class="pill muted" id="biUpd">–</span>
    </div>
    <div class="sec-b">
      <div class="bi-kpis" id="biKpis"></div>
      <div id="biRunning" style="padding:0 16px 8px"></div>
      <div class="sub" style="padding:12px 16px 0">在岗资源占用(当前进程 · <span class="mono">psutil</span> 实时 · 进程此刻的 CPU/内存 · 按内存降序)</div>
      <table class="bi-tbl">
        <thead><tr><th>角色</th><th>CPU%</th><th>内存MB</th></tr></thead>
        <tbody id="biSysBody"></tbody>
      </table>
    </div>
  </section>

  <section data-tab="tasks">
    <div class="sec-h">B · 待办池 TODO Pool <span class="sub">待确认 + 待办统一队列 · 存 <span class="mono">agent_bus/todos.json</span></span>
      <span id="confirmAlert" class="confirm-alert" style="display:none"></span>
      <span class="spacer" style="flex:1"></span>
      <span class="filt">
        <button class="btn ghost tview-btn active" id="tviewActive" data-view="active">待办池 <b id="tcountActive">0</b></button>
        <button class="btn ghost tview-btn" id="tviewHistory" data-view="history">历史 <b id="tcountHistory">0</b></button>
        <span style="width:8px;display:inline-block"></span>
        优先级
        <select id="filtPri"><option value="">全部</option><option>高</option><option>中</option><option>低</option></select>
        状态
        <select id="filtStatus"><option value="">全部</option><option>待办</option><option>进行中</option><option>搁置</option><option>完成</option></select>
      </span>
    </div>
    <div class="todo-add">
      <input type="text" id="tAddTitle" class="full" placeholder="标题(必填)">
      <textarea id="tAddDetail" class="full" placeholder="详情(可空)"></textarea>
      <input type="text" id="tAddSource" placeholder="来源(谁/哪个群)">
      <input type="text" id="tAddDeadline" placeholder="截止(如 2026-06-23 09:00,可空)">
      <select id="tAddPri"><option>高</option><option selected>中</option><option>低</option></select>
      <select id="tAddStatus"><option selected>待办</option><option>进行中</option><option>搁置</option><option>完成</option></select>
      <button class="btn mini full" id="tAddBtn" style="justify-self:start">+ 新增待办</button>
    </div>
    <div id="todoBox" style="padding:4px 0"></div>
  </section>

  <div class="grid2" data-tab="fleet">
    <section>
      <div class="sec-h">F · daemon 自愈 <span class="sub">fleet.json 纳管的 daemon × 存活(复用 <span class="mono">supervise</span>)</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" id="superviseBtn" style="margin:0;padding:5px 12px">supervise 一次</button>
      </div>
      <div id="daemonBox" style="padding:4px 0"></div>
    </section>

    <section>
      <div class="sec-h">G · 孤儿巡检 <span class="sub">复用 <span class="mono">reap --dry-run</span>(只读,不改队列)</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" id="reapBtn" style="margin:0;padding:5px 12px">巡检一次</button>
      </div>
      <div id="reapBox" style="padding:8px 16px;color:var(--dim);font-size:12px">点「巡检一次」查看 owner 已死/超期的 processing 任务。</div>
    </section>
  </div>

  <section data-tab="fleet">
    <div class="sec-h">H · 最近事件 <span class="sub">审计流 <span class="mono">events.jsonl</span> 末 40 行 · 倒序(send/claim/done)</span></div>
    <div id="eventBox" style="max-height:300px;overflow:auto"></div>
  </section>

  <div class="grid2" data-tab="tasks">
    <section>
      <div class="sec-h">C · 派活 <span class="sub">复用 <span class="mono">agentbus send</span> · from=Master</span></div>
      <label>派给(to role)</label>
      <select id="sendTo"></select>
      <label>标题(title)</label>
      <input type="text" id="sendTitle" placeholder="一句话任务名">
      <label>任务(task — 自包含指令,含产出位置+约束)</label>
      <textarea id="sendTask" placeholder="详细任务描述…"></textarea>
      <label style="margin-bottom:0">完成通知(worker done --notify)</label>
      <select id="sendNotify">
        <option value="auto" selected>auto(error 必弹 / Master 完成弹·节流)</option>
        <option value="always">always(总弹 toast)</option>
        <option value="never">never(不弹)</option>
      </select>
      <label class="chk"><input type="checkbox" id="sendApproval">
        高风险 requires_approval(留 inbox 不被自动领取,需人工 <span class="mono">wait --include-approval</span>)</label>
      <button class="btn" id="sendBtn">发送任务</button>
    </section>

    <section>
      <div class="sec-h">D · 回填结果 <span class="sub">读 <span class="mono">&lt;role&gt;/done</span></span>
        <span class="spacer" style="flex:1"></span>
        <select id="replyRole" style="width:auto;margin:0;padding:3px 8px"></select>
        <button class="btn danger" id="clearAllBtn" style="margin:0;padding:5px 12px">清空</button>
      </div>
      <div id="replyBox" style="padding:8px 0"></div>
    </section>
  </div>

  <section data-tab="live">
    <div class="sec-h">I · 实时视图 <span class="mono">live.log</span> <span class="sub">看 headless daemon/dispatch 实时执行流(🔧工具 / 💬回复 / ■完成)— 选 role 后每 1.5s 轮询</span>
      <span class="spacer" style="flex:1"></span>
      <span class="filt">role
        <select id="liveRole"></select>
        <button class="btn ghost mini" id="liveBottom">滚到底</button>
      </span>
    </div>
    <div class="live-meta"><span id="liveDot" class="live-dot"></span><span id="liveStat">未选 role</span></div>
    <pre id="liveBox" class="livebox">（选一个 role 看它的 live.log）</pre>
  </section>

  <!-- 历史用量 BI (P0) —— 独立 tab(账单级全量,与运营/资源口径分开) -->
  <section data-tab="usage">
    <div class="sec-h">📈 历史用量 <span class="sub">全量 Claude Code 用量 · <span class="mono">token_stats.py</span> · 已去重 · <b>token 消耗为主</b>($ 仅 API 等价参考)· Shanghai 日</span>
      <span class="spacer" style="flex:1"></span>
      <span id="usageMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="usageRefresh">刷新</button>
    </div>
    <div class="kpis" id="usageKpis"></div>
    <div class="grid2">
      <div>
        <div class="uchart-cap">每日 token 趋势 · io <span class="muted">(input+output · 新增有效工作量)</span>
          <span class="urange" id="uRange">
            <button type="button" data-r="7">7d</button><button type="button" data-r="30" class="on">30d</button><button type="button" data-r="90">90d</button><button type="button" data-r="0">全部</button>
          </span>
          <span class="muted" id="trendPeak"></span>
        </div>
        <div class="uchart-wrap" id="uTrendWrap"><canvas id="uTrend" height="190"></canvas><div class="uchart-tip" id="uTrendTip"></div></div>
        <div class="trend-legend" id="trendLegend"></div>
        <div class="uchart-cap" style="margin-top:12px">每日 token 趋势 · 总 <span class="muted">(io+cache读写 · 固定物理量 · 独立纵轴)</span>
          <span class="muted" id="trendPeakTotal"></span>
        </div>
        <div class="uchart-wrap" id="uTrendTotalWrap"><canvas id="uTrendTotal" height="190"></canvas><div class="uchart-tip" id="uTrendTotalTip"></div></div>
      </div>
      <div>
        <div class="uchart-cap">项目 token 消耗 Top N <span class="muted" id="uProjUnit">(全历史)</span></div>
        <div class="bars" id="uProj"></div>
      </div>
    </div>
    <div class="uchart-cap" style="border-top:1px solid var(--line);margin-top:6px;padding-top:12px">模型迁移进度 <span class="muted">(按月堆叠 · 标 4.8 占比)</span></div>
    <canvas id="uModels" height="200"></canvas>
    <div class="legend" id="uModelLegend"></div>
  </section>

  <!-- 能力池 caps —— capabilities.json + agents.json 实时只读(最小授权审视)-->
  <section data-tab="caps">
    <div class="sec-h">🧬 能力池 / caps
      <span class="sub">最小授权:每 agent 只持需要的能力 · 数据源 <span class="mono">capabilities.json</span> + <span class="mono">agents.json</span> 实时只读(不改 caps)</span>
      <span class="spacer" style="flex:1"></span>
      <span id="capsMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="capsRefresh">刷新</button>
    </div>
    <div class="sub" style="padding:8px 16px 0">⓪ Agent 详情 · <b>各 agent 会哪些内容</b> <span style="font-weight:400">— 名 / <b>职责 desc(会什么)</b> / caps / 状态(<span style="color:var(--live)">前台cli</span>·<span style="color:var(--idle)">daemon</span>·<span style="color:var(--none)">离线</span>·<span style="color:var(--dead)">退役→接替者</span>)· 数据源 <span class="mono">agents.json.desc</span> + <span class="mono">retired_roster.json</span> 实时只读</span></div>
    <div class="ag-grid" id="agDetail"></div>
  </section>

  <section data-tab="caps">
    <div class="sub" style="padding:8px 16px 0">① 能力池清单 <span style="font-weight:400">— 6 个 cap · desc / 默认给谁 / 风险标注 · 现持有人数(<b style="color:var(--dead)">红=无持有者</b>)</span></div>
    <div class="cap-pool" id="capPool"></div>
  </section>

  <section data-tab="caps">
    <div class="sec-h">② caps 矩阵 <span class="sub">agent × cap(✓=持有)· 现行 agent 按 caps 数排;退役角色单列在后(<span class="mono">删除线</span>)</span></div>
    <div class="sec-b" style="overflow:auto">
      <table class="capmx" id="capMatrix"></table>
    </div>
    <div class="mx-foot" id="capMxFoot"></div>
  </section>

  <div class="sec-grid" data-tab="caps">
    <section>
      <div class="sec-h">③ 能力分布 <span class="sub">每个 cap 被几个现行 agent 持有(降序)</span></div>
      <div class="bars" id="capDist"></div>
    </section>
    <section>
      <div class="sec-h">⑤ 最小授权 · 高风险能力持有者 <span class="sub">谁握着对外/高风险闸 — 一眼审授权面</span></div>
      <div id="capRisk" style="padding:10px 16px"></div>
    </section>
  </div>

  <section data-tab="caps">
    <div class="sec-h">④ 缺口高亮 · 数据治理 <span class="sub">空 caps 在岗 agent(待补)/ 无持有者能力 / 已过滤的测试角色</span></div>
    <div id="capGaps" style="padding:8px 0"></div>
  </section>

  <!-- 上下文模块 · 1M 预算/auto-compact 预警 -->
  <section data-tab="ctx">
    <div class="sec-h">🧠 上下文 · 1M 预算预警
      <span class="sub">每个活跃会话(窗口)的上下文预算:<span class="mono">ctx=input+cache_read+cache_creation</span> / 模型窗口 — <b>≥65% 将被 auto-compact</b>(实测真阈·有损摘要→降智)、≥50%黄。role 仅标"谁的窗口"(who 校验)。<b>谁活着/在岗</b>看 🛰 运维面板</span>
      <span class="spacer" style="flex:1"></span>
      <span id="ctxMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="ctxRefresh">刷新</button>
    </div>
    <div class="sec-b">
      <div class="ctx-kpis" id="ctxKpis"></div>
      <table>
        <thead><tr><th>占用%</th><th>距自动压(65%)余量</th><th>ctx / 窗口</th><th>role(谁的窗口)</th><th>model</th><th>最后活动 UTC</th></tr></thead>
        <tbody id="ctxBody"></tbody>
      </table>
      <div class="foot muted" id="ctxFoot"></div>
    </div>
  </section>

  <!-- 提示词审核(只读·手动刷新)-->
  <section data-tab="prompts">
    <div class="sec-h">📝 提示词审核
      <span class="sub">当前喂给 agent 的提示词<b>原文</b>(只读·供审核措辞)— hello onboarding / 全局+项目 CLAUDE.md / 基座层文档。手动刷新才重读,每节标源路径+mtime</span>
      <span class="spacer" style="flex:1"></span>
      <span id="promptsMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="promptsRefresh">刷新</button>
    </div>
    <div class="sec-b">
      <div id="promptsBox" style="padding:8px 16px 16px"></div>
    </div>
  </section>

  <!-- 可恢复会话 (resume) -->
  <section data-tab="fleet">
    <div class="sec-h">🗂 可恢复会话 <span class="sub">Claude Code 历史会话 · <span class="mono">ab sessions</span> · 仅元数据</span>
      <span class="spacer" style="flex:1"></span>
      <span class="filt-grp"><span class="filt-lbl">归属</span>
        <button class="btn ghost mini sess-own active" data-o="all">全部</button>
        <button class="btn ghost mini sess-own" data-o="bus">bus</button>
        <button class="btn ghost mini sess-own" data-o="nonbus">个人</button></span>
      <span class="filt-grp"><span class="filt-lbl">状态</span>
        <button class="btn ghost mini sess-filt active" data-f="all" id="sessFAll">全部</button>
        <button class="btn ghost mini sess-filt" data-f="live" id="sessFLive">▶ 运行中</button>
        <button class="btn ghost mini sess-filt" data-f="dead" id="sessFDead">· 死(可 resume)</button>
        <button class="btn ghost mini sess-filt" data-f="tagged" id="sessFTag">🏷 已标记</button></span>
      <button class="btn ghost mini" id="sessRefresh">刷新</button>
    </div>
    <div class="hint" style="padding:8px 16px 0;color:var(--dim)">⚠ 运行中的会话正在跑、别 resume。<b>死的才是真·可恢复</b> —— 点【复制】拿到 <span class="mono">claude --resume &lt;id&gt;</span>,到终端执行续会话(WebUI 跑不了交互式 resume)。</div>
    <div id="sessCount" class="muted" style="font-size:11.5px;padding:6px 16px 0"></div>
    <div id="sessBox" style="padding:8px 0;max-height:560px;overflow:auto"></div>
  </section>

  <!-- 🎯 长期任务线看板(阶段2·Master拍板·面板侧呈现/数据侧供数·决策点交叉todos高亮) -->
  <section data-tab="tasklines">
    <div class="sec-h">🎯 长期任务线看板
      <span class="sub">长期任务线全景 · 数据 <span class="mono">tasklines.json</span>(MasterAgent维护)· ⧗=待用户决策(交叉待办池)· 点卡展开详情</span>
      <span class="spacer" style="flex:1"></span>
      <span id="tlMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="tlRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="tlBox" style="padding:6px 0"></div></div>
  </section>

  <!-- 🧩 工作组看板(池化 bus·使用方出口径/MasterUI出可视化) -->
  <section data-tab="groups">
    <div class="sec-h">🧩 工作组看板
      <span class="sub">池化 bus 的组视图 · 成员激活/组inbox深度/负载分布/并行系数 · 数据 <span class="mono">_groups/</span>+<span class="mono">events.jsonl</span>(使用方口径·只读元数据)</span>
      <span class="spacer" style="flex:1"></span>
      <span id="grpMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="grpRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="grpBox" style="padding:6px 0"></div></div>
  </section>

  <!-- 🔧 机制注册表(bus 提供什么·使用方出数据/MasterUI render) -->
  <section data-tab="mechanisms">
    <div class="sec-h">🔧 机制注册表
      <span class="sub">bus <b>提供哪些功能</b>的单一视图 · 数据 <span class="mono">bus_mechanisms.json</span> · <b>机制≠能力池</b>:caps=谁被授权做什么(粗·授权)、机制=bus提供什么(细·基建);related_cap 只是交叉链</span>
      <span class="spacer" style="flex:1"></span>
      <span id="mechMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="mechRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="mechBox" style="padding:6px 0"></div></div>
  </section>

  <!-- 🗂️ 长程任务·锚定生长网(P0:脊柱+当前段+进度·使用方简报/MasterAgent引擎+数据/MasterUI render) -->
  <section data-tab="longrange">
    <div class="sec-h">🗂️ 长程任务 · 锚定生长网
      <span class="sub">用户钉里程碑脊柱 → 网只长到下一锚点收敛 → 塌缩归档 · <b>P0</b>:脊柱+当前活跃段+进度 · 数据 <span class="mono">longrange_tasks.json</span>(MasterAgent引擎)· 进度分母会生长/可倒退=<b>诚实信号非bug</b></span>
      <span class="spacer" style="flex:1"></span>
      <span id="lrMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="lrRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="lrBox" style="padding:6px 0"></div></div>
  </section>
</main>

<div id="toast" class="toast"></div>

<script>
/* ===== morphdom 2.7.4 (inlined · CSP-safe · 增量DOM diff,替代innerHTML整体重建) ===== */
(function(global,factory){typeof exports==="object"&&typeof module!=="undefined"?module.exports=factory():typeof define==="function"&&define.amd?define(factory):(global=global||self,global.morphdom=factory())})(this,function(){"use strict";var DOCUMENT_FRAGMENT_NODE=11;function morphAttrs(fromNode,toNode){var toNodeAttrs=toNode.attributes;var attr;var attrName;var attrNamespaceURI;var attrValue;var fromValue;if(toNode.nodeType===DOCUMENT_FRAGMENT_NODE||fromNode.nodeType===DOCUMENT_FRAGMENT_NODE){return}for(var i=toNodeAttrs.length-1;i>=0;i--){attr=toNodeAttrs[i];attrName=attr.name;attrNamespaceURI=attr.namespaceURI;attrValue=attr.value;if(attrNamespaceURI){attrName=attr.localName||attrName;fromValue=fromNode.getAttributeNS(attrNamespaceURI,attrName);if(fromValue!==attrValue){if(attr.prefix==="xmlns"){attrName=attr.name}fromNode.setAttributeNS(attrNamespaceURI,attrName,attrValue)}}else{fromValue=fromNode.getAttribute(attrName);if(fromValue!==attrValue){fromNode.setAttribute(attrName,attrValue)}}}var fromNodeAttrs=fromNode.attributes;for(var d=fromNodeAttrs.length-1;d>=0;d--){attr=fromNodeAttrs[d];attrName=attr.name;attrNamespaceURI=attr.namespaceURI;if(attrNamespaceURI){attrName=attr.localName||attrName;if(!toNode.hasAttributeNS(attrNamespaceURI,attrName)){fromNode.removeAttributeNS(attrNamespaceURI,attrName)}}else{if(!toNode.hasAttribute(attrName)){fromNode.removeAttribute(attrName)}}}}var range;var NS_XHTML="http://www.w3.org/1999/xhtml";var doc=typeof document==="undefined"?undefined:document;var HAS_TEMPLATE_SUPPORT=!!doc&&"content"in doc.createElement("template");var HAS_RANGE_SUPPORT=!!doc&&doc.createRange&&"createContextualFragment"in doc.createRange();function createFragmentFromTemplate(str){var template=doc.createElement("template");template.innerHTML=str;return template.content.childNodes[0]}function createFragmentFromRange(str){if(!range){range=doc.createRange();range.selectNode(doc.body)}var fragment=range.createContextualFragment(str);return fragment.childNodes[0]}function createFragmentFromWrap(str){var fragment=doc.createElement("body");fragment.innerHTML=str;return fragment.childNodes[0]}function toElement(str){str=str.trim();if(HAS_TEMPLATE_SUPPORT){return createFragmentFromTemplate(str)}else if(HAS_RANGE_SUPPORT){return createFragmentFromRange(str)}return createFragmentFromWrap(str)}function compareNodeNames(fromEl,toEl){var fromNodeName=fromEl.nodeName;var toNodeName=toEl.nodeName;var fromCodeStart,toCodeStart;if(fromNodeName===toNodeName){return true}fromCodeStart=fromNodeName.charCodeAt(0);toCodeStart=toNodeName.charCodeAt(0);if(fromCodeStart<=90&&toCodeStart>=97){return fromNodeName===toNodeName.toUpperCase()}else if(toCodeStart<=90&&fromCodeStart>=97){return toNodeName===fromNodeName.toUpperCase()}else{return false}}function createElementNS(name,namespaceURI){return!namespaceURI||namespaceURI===NS_XHTML?doc.createElement(name):doc.createElementNS(namespaceURI,name)}function moveChildren(fromEl,toEl){var curChild=fromEl.firstChild;while(curChild){var nextChild=curChild.nextSibling;toEl.appendChild(curChild);curChild=nextChild}return toEl}function syncBooleanAttrProp(fromEl,toEl,name){if(fromEl[name]!==toEl[name]){fromEl[name]=toEl[name];if(fromEl[name]){fromEl.setAttribute(name,"")}else{fromEl.removeAttribute(name)}}}var specialElHandlers={OPTION:function(fromEl,toEl){var parentNode=fromEl.parentNode;if(parentNode){var parentName=parentNode.nodeName.toUpperCase();if(parentName==="OPTGROUP"){parentNode=parentNode.parentNode;parentName=parentNode&&parentNode.nodeName.toUpperCase()}if(parentName==="SELECT"&&!parentNode.hasAttribute("multiple")){if(fromEl.hasAttribute("selected")&&!toEl.selected){fromEl.setAttribute("selected","selected");fromEl.removeAttribute("selected")}parentNode.selectedIndex=-1}}syncBooleanAttrProp(fromEl,toEl,"selected")},INPUT:function(fromEl,toEl){syncBooleanAttrProp(fromEl,toEl,"checked");syncBooleanAttrProp(fromEl,toEl,"disabled");if(fromEl.value!==toEl.value){fromEl.value=toEl.value}if(!toEl.hasAttribute("value")){fromEl.removeAttribute("value")}},TEXTAREA:function(fromEl,toEl){var newValue=toEl.value;if(fromEl.value!==newValue){fromEl.value=newValue}var firstChild=fromEl.firstChild;if(firstChild){var oldValue=firstChild.nodeValue;if(oldValue==newValue||!newValue&&oldValue==fromEl.placeholder){return}firstChild.nodeValue=newValue}},SELECT:function(fromEl,toEl){if(!toEl.hasAttribute("multiple")){var selectedIndex=-1;var i=0;var curChild=fromEl.firstChild;var optgroup;var nodeName;while(curChild){nodeName=curChild.nodeName&&curChild.nodeName.toUpperCase();if(nodeName==="OPTGROUP"){optgroup=curChild;curChild=optgroup.firstChild}else{if(nodeName==="OPTION"){if(curChild.hasAttribute("selected")){selectedIndex=i;break}i++}curChild=curChild.nextSibling;if(!curChild&&optgroup){curChild=optgroup.nextSibling;optgroup=null}}}fromEl.selectedIndex=selectedIndex}}};var ELEMENT_NODE=1;var DOCUMENT_FRAGMENT_NODE$1=11;var TEXT_NODE=3;var COMMENT_NODE=8;function noop(){}function defaultGetNodeKey(node){if(node){return node.getAttribute&&node.getAttribute("id")||node.id}}function morphdomFactory(morphAttrs){return function morphdom(fromNode,toNode,options){if(!options){options={}}if(typeof toNode==="string"){if(fromNode.nodeName==="#document"||fromNode.nodeName==="HTML"||fromNode.nodeName==="BODY"){var toNodeHtml=toNode;toNode=doc.createElement("html");toNode.innerHTML=toNodeHtml}else{toNode=toElement(toNode)}}else if(toNode.nodeType===DOCUMENT_FRAGMENT_NODE$1){toNode=toNode.firstElementChild}var getNodeKey=options.getNodeKey||defaultGetNodeKey;var onBeforeNodeAdded=options.onBeforeNodeAdded||noop;var onNodeAdded=options.onNodeAdded||noop;var onBeforeElUpdated=options.onBeforeElUpdated||noop;var onElUpdated=options.onElUpdated||noop;var onBeforeNodeDiscarded=options.onBeforeNodeDiscarded||noop;var onNodeDiscarded=options.onNodeDiscarded||noop;var onBeforeElChildrenUpdated=options.onBeforeElChildrenUpdated||noop;var skipFromChildren=options.skipFromChildren||noop;var addChild=options.addChild||function(parent,child){return parent.appendChild(child)};var childrenOnly=options.childrenOnly===true;var fromNodesLookup=Object.create(null);var keyedRemovalList=[];function addKeyedRemoval(key){keyedRemovalList.push(key)}function walkDiscardedChildNodes(node,skipKeyedNodes){if(node.nodeType===ELEMENT_NODE){var curChild=node.firstChild;while(curChild){var key=undefined;if(skipKeyedNodes&&(key=getNodeKey(curChild))){addKeyedRemoval(key)}else{onNodeDiscarded(curChild);if(curChild.firstChild){walkDiscardedChildNodes(curChild,skipKeyedNodes)}}curChild=curChild.nextSibling}}}function removeNode(node,parentNode,skipKeyedNodes){if(onBeforeNodeDiscarded(node)===false){return}if(parentNode){parentNode.removeChild(node)}onNodeDiscarded(node);walkDiscardedChildNodes(node,skipKeyedNodes)}function indexTree(node){if(node.nodeType===ELEMENT_NODE||node.nodeType===DOCUMENT_FRAGMENT_NODE$1){var curChild=node.firstChild;while(curChild){var key=getNodeKey(curChild);if(key){fromNodesLookup[key]=curChild}indexTree(curChild);curChild=curChild.nextSibling}}}indexTree(fromNode);function handleNodeAdded(el){onNodeAdded(el);var curChild=el.firstChild;while(curChild){var nextSibling=curChild.nextSibling;var key=getNodeKey(curChild);if(key){var unmatchedFromEl=fromNodesLookup[key];if(unmatchedFromEl&&compareNodeNames(curChild,unmatchedFromEl)){curChild.parentNode.replaceChild(unmatchedFromEl,curChild);morphEl(unmatchedFromEl,curChild)}else{handleNodeAdded(curChild)}}else{handleNodeAdded(curChild)}curChild=nextSibling}}function cleanupFromEl(fromEl,curFromNodeChild,curFromNodeKey){while(curFromNodeChild){var fromNextSibling=curFromNodeChild.nextSibling;if(curFromNodeKey=getNodeKey(curFromNodeChild)){addKeyedRemoval(curFromNodeKey)}else{removeNode(curFromNodeChild,fromEl,true)}curFromNodeChild=fromNextSibling}}function morphEl(fromEl,toEl,childrenOnly){var toElKey=getNodeKey(toEl);if(toElKey){delete fromNodesLookup[toElKey]}if(!childrenOnly){var beforeUpdateResult=onBeforeElUpdated(fromEl,toEl);if(beforeUpdateResult===false){return}else if(beforeUpdateResult instanceof HTMLElement){fromEl=beforeUpdateResult;indexTree(fromEl)}morphAttrs(fromEl,toEl);onElUpdated(fromEl);if(onBeforeElChildrenUpdated(fromEl,toEl)===false){return}}if(fromEl.nodeName!=="TEXTAREA"){morphChildren(fromEl,toEl)}else{specialElHandlers.TEXTAREA(fromEl,toEl)}}function morphChildren(fromEl,toEl){var skipFrom=skipFromChildren(fromEl,toEl);var curToNodeChild=toEl.firstChild;var curFromNodeChild=fromEl.firstChild;var curToNodeKey;var curFromNodeKey;var fromNextSibling;var toNextSibling;var matchingFromEl;outer:while(curToNodeChild){toNextSibling=curToNodeChild.nextSibling;curToNodeKey=getNodeKey(curToNodeChild);while(!skipFrom&&curFromNodeChild){fromNextSibling=curFromNodeChild.nextSibling;if(curToNodeChild.isSameNode&&curToNodeChild.isSameNode(curFromNodeChild)){curToNodeChild=toNextSibling;curFromNodeChild=fromNextSibling;continue outer}curFromNodeKey=getNodeKey(curFromNodeChild);var curFromNodeType=curFromNodeChild.nodeType;var isCompatible=undefined;if(curFromNodeType===curToNodeChild.nodeType){if(curFromNodeType===ELEMENT_NODE){if(curToNodeKey){if(curToNodeKey!==curFromNodeKey){if(matchingFromEl=fromNodesLookup[curToNodeKey]){if(fromNextSibling===matchingFromEl){isCompatible=false}else{fromEl.insertBefore(matchingFromEl,curFromNodeChild);if(curFromNodeKey){addKeyedRemoval(curFromNodeKey)}else{removeNode(curFromNodeChild,fromEl,true)}curFromNodeChild=matchingFromEl;curFromNodeKey=getNodeKey(curFromNodeChild)}}else{isCompatible=false}}}else if(curFromNodeKey){isCompatible=false}isCompatible=isCompatible!==false&&compareNodeNames(curFromNodeChild,curToNodeChild);if(isCompatible){morphEl(curFromNodeChild,curToNodeChild)}}else if(curFromNodeType===TEXT_NODE||curFromNodeType==COMMENT_NODE){isCompatible=true;if(curFromNodeChild.nodeValue!==curToNodeChild.nodeValue){curFromNodeChild.nodeValue=curToNodeChild.nodeValue}}}if(isCompatible){curToNodeChild=toNextSibling;curFromNodeChild=fromNextSibling;continue outer}if(curFromNodeKey){addKeyedRemoval(curFromNodeKey)}else{removeNode(curFromNodeChild,fromEl,true)}curFromNodeChild=fromNextSibling}if(curToNodeKey&&(matchingFromEl=fromNodesLookup[curToNodeKey])&&compareNodeNames(matchingFromEl,curToNodeChild)){if(!skipFrom){addChild(fromEl,matchingFromEl)}morphEl(matchingFromEl,curToNodeChild)}else{var onBeforeNodeAddedResult=onBeforeNodeAdded(curToNodeChild);if(onBeforeNodeAddedResult!==false){if(onBeforeNodeAddedResult){curToNodeChild=onBeforeNodeAddedResult}if(curToNodeChild.actualize){curToNodeChild=curToNodeChild.actualize(fromEl.ownerDocument||doc)}addChild(fromEl,curToNodeChild);handleNodeAdded(curToNodeChild)}}curToNodeChild=toNextSibling;curFromNodeChild=fromNextSibling}cleanupFromEl(fromEl,curFromNodeChild,curFromNodeKey);var specialElHandler=specialElHandlers[fromEl.nodeName];if(specialElHandler){specialElHandler(fromEl,toEl)}}var morphedNode=fromNode;var morphedNodeType=morphedNode.nodeType;var toNodeType=toNode.nodeType;if(!childrenOnly){if(morphedNodeType===ELEMENT_NODE){if(toNodeType===ELEMENT_NODE){if(!compareNodeNames(fromNode,toNode)){onNodeDiscarded(fromNode);morphedNode=moveChildren(fromNode,createElementNS(toNode.nodeName,toNode.namespaceURI))}}else{morphedNode=toNode}}else if(morphedNodeType===TEXT_NODE||morphedNodeType===COMMENT_NODE){if(toNodeType===morphedNodeType){if(morphedNode.nodeValue!==toNode.nodeValue){morphedNode.nodeValue=toNode.nodeValue}return morphedNode}else{morphedNode=toNode}}}if(morphedNode===toNode){onNodeDiscarded(fromNode)}else{if(toNode.isSameNode&&toNode.isSameNode(morphedNode)){return}morphEl(morphedNode,toNode,childrenOnly);if(keyedRemovalList){for(var i=0,len=keyedRemovalList.length;i<len;i++){var elToRemove=fromNodesLookup[keyedRemovalList[i]];if(elToRemove){removeNode(elToRemove,elToRemove.parentNode,false)}}}}if(!childrenOnly&&morphedNode!==fromNode&&fromNode.parentNode){if(morphedNode.actualize){morphedNode=morphedNode.actualize(fromNode.ownerDocument||doc)}fromNode.parentNode.replaceChild(morphedNode,fromNode)}return morphedNode}}var morphdom=morphdomFactory(morphAttrs);return morphdom});
/* ===== end morphdom ===== */
let state=null, pollTimer=null, lastTasksOpen=new Set();

// 增量更新统一入口:把容器 el 的【孩子】patch 成 innerHtml(childrenOnly→容器自身的 class/style 不动)。
//  · getNodeKey 用 id 或 data-key(列表项给稳定业务 key)→ 原地更新、不重建 → 不闪、滚动不跳。
//  · onBeforeElUpdated 跳过【正在输入/选择】的表单控件 → 光标/未提交输入/下拉态不被刷新冲掉。
function _keyOf(n){ if(n.nodeType!==1) return null; return n.id || n.getAttribute('data-key') || null; }
function _onBeforeUpd(f){ if(f===document.activeElement && (f.tagName==='INPUT'||f.tagName==='TEXTAREA'||f.tagName==='SELECT')) return false; return true; }
function morphInto(el, innerHtml){
  if(!el) return;
  const tag=el.tagName.toLowerCase();
  try{ morphdom(el, '<'+tag+'>'+innerHtml+'</'+tag+'>',
    {childrenOnly:true, getNodeKey:_keyOf, onBeforeElUpdated:_onBeforeUpd}); }
  catch(e){ el.innerHTML=innerHtml; }   // 兜底:morph 异常退回整建,绝不白屏
}
// 舰队表展开任务明细(委托 onclick;展开态记入 lastTasksOpen,morph 重渲染后由 render 还原)
function toggleTasks(tr){
  const det=tr.nextElementSibling;
  if(!det || !det.classList.contains('tasks')) return;
  const role=tr.dataset.role; const vis=det.style.display!=='none';
  det.style.display=vis?'none':''; if(vis) lastTasksOpen.delete(role); else lastTasksOpen.add(role);
}

function esc(s){return (s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtAge(a){ if(a==null) return '?'; if(a<60) return a+'s'; if(a<3600) return Math.floor(a/60)+'m'+(a%60)+'s'; return Math.floor(a/3600)+'h'+Math.floor((a%3600)/60)+'m';}
function qc(n,cls){return `<span class="qcount ${n?cls:'z'}">${n}</span>`;}
function swBadge(s,detail){
  const t=esc(detail||'');
  if(s==='armed') return ` <span class="badge live" title="自醒在岗 ${t}">🔔自醒</span>`;
  if(s==='recent') return ` <span class="badge idle" title="有心跳无wait进程·瞬时态(刚fire待重挂/交互中)·非告警 ${t}">◐待确认</span>`;
  if(s==='stale') return ` <span class="badge dead" title="自醒掉线!需重挂 ${t}">⚠自醒掉线</span>`;
  return ` <span class="badge none" title="未自醒 ${t}">—</span>`;
}
// turn 状态(turn_state.py hook 实时盖):thinking=turn内思考 / tool=正跑工具 / idle=空闲。
// 稳健化(2026-07-10 用户「不够灵敏 + 中断后卡住」):
//  ① armed(有 wait 进程=阻塞等消息)→ 权威判空闲待命,覆盖任何陈旧 thinking(此时不可能在思考);
//  ② thinking/tool 但 ts 超 STALE 秒无新事件 → 判「停顿?」——治中断/漏 Stop 卡在思考不回落
//     (PreToolUse/PostToolUse 每步刷新 ts,正常多工具干活 age 一直很小;冻住=中断或超长单工具)。
//  数据只来自 hook 事件 + wait 进程,非推断。
// 分状态陈旧阈:思考态推理很少 >30s,冻这么久多是【中断】(中断不触发任何 hook·官方证实);
// 工具态本可跑很久(长 bash 等),放宽到 300s 才疑,免误报正常长工具。
const TURN_STALE={thinking:30, tool:300};
function turnBadge(tn, sw){
  if(sw==='armed') return `<span class="badge idle" title="有 wait 进程·阻塞等消息=空闲待命(不可能在思考)">⚪ 空闲·待命</span>`;
  if(!tn||!tn.state) return '<span class="badge none" title="无 turn 状态(未装 hook / 非 bus 成员 / 尚无事件)">–</span>';
  const age=tn.age, ag=(age==null?'':' '+fmtAge(age));
  if(tn.state==='idle') return `<span class="badge idle" title="turn 已结束(Stop/idle_prompt)·空闲${ag}">⚪ 空闲${ag}</span>`;
  const stale=TURN_STALE[tn.state]||60;
  if(age!=null && age>stale)
    return `<span class="badge none" title="状态为「${esc(tn.label||tn.state)}」但已 ${fmtAge(age)} 无新 hook 事件 → 判停顿(用户中断不触发任何 hook·官方证实 / 或异常结束)">◌ 停顿?${ag}</span>`;
  if(tn.state==='tool') return `<span class="badge warn" title="正在执行工具 ${esc(tn.tool||'')}${ag}">🔧 ${esc(tn.label)}${ag}</span>`;
  return `<span class="badge live" title="在 turn 内(思考/执行中)${ag}">🟢 ${esc(tn.label||'思考/执行中')}${ag}</span>`;
}

function toast(msg,err){const t=document.getElementById('toast');t.textContent=msg;t.className='toast show'+(err?' err':'');setTimeout(()=>t.className='toast',3200);}

function setConn(on){const d=document.getElementById('connDot'),t=document.getElementById('connTxt');
  d.className='dot '+(on?'on':'off'); t.textContent=on?'事件实时(SSE)':'已断开 · 轮询中';}

function render(){
  if(!state) return;
  const f=state.fleet;
  document.getElementById('aliveN').textContent=f.alive_count;
  document.getElementById('fleetN').textContent=f.fleet_count;
  document.getElementById('updT').textContent='更新 '+state.now;

  // fleet table —— 增量 morph(展开态由 lastTasksOpen 声明式驱动 + 委托 onclick,刷新中展开/滚动不丢)
  const subBadge={inbox:'idle',processing:'warn',held:'held',failed:'failed'};
  let fleetHtml='';
  f.roles.forEach(r=>{
    const hasTasks=(r.inbox+r.processing+(r.held||0)+(r.failed||0))>0;
    const open=lastTasksOpen.has(r.role);
    fleetHtml+=`<tr data-key="fr-${esc(r.role)}" class="role-row${hasTasks?' clickable':''}"${hasTasks?` data-role="${esc(r.role)}" onclick="toggleTasks(this)"`:''}>
      <td><b>${esc(r.role)}</b></td>
      <td><span class="badge vbadge ${r.vclass}" title="${esc(r.verdict)}">${esc(r.verdict)}</span>${swBadge(r.self_wake,r.self_wake_detail)}</td>
      <td>${turnBadge(r.turn, r.self_wake)}</td>
      <td class="mono">${esc(r.pid_display||'–')}</td>
      <td class="mono">${fmtAge(r.age)}</td>
      <td>${qc(r.inbox,'ib')}</td><td>${qc(r.processing,'pr')}</td><td>${qc(r.done,'dn')}</td>
      <td>${qc(r.held||0,'held')}</td><td>${qc(r.failed||0,'failed')}</td>
      <td class="cwd" title="${esc(r.cwd||'')}">${esc(r.cwd||'–')}</td></tr>`;
    if(hasTasks){
      let inner='<div class="task-wrap">';
      (r.tasks||[]).forEach(t=>{
        const appr=(t.sub==='inbox'&&t.requires_approval);
        inner+=`<div class="task-card"><div class="t-h">
          <span class="badge ${subBadge[t.sub]||'idle'}">${esc(t.sub)}</span>
          ${appr?'<span class="badge appr">⚠ requires_approval</span>':''}
          ${(t.requeue_count>0)?'<span class="badge warn">requeue×'+esc(t.requeue_count)+'</span>':''}
          <span class="mono">${esc(t.id)}</span> · from <b>${esc(t.from)}</b>
          <span class="t-title">${esc(t.title)}</span>
          ${t.claimed?'· claimed '+esc(t.claimed):''}</div>
          ${appr?'<div class="hint">高风险任务:自动监听者(serve/daemon/wait)不会领取 → 有人值守的 tab 用 <code>wait --include-approval</code> 显式领取执行</div>':''}
          <pre>${esc(t.task)}</pre></div>`;
      });
      inner+='</div>';
      fleetHtml+=`<tr data-key="fd-${esc(r.role)}" class="tasks" data-role="${esc(r.role)}" style="display:${open?'':'none'}"><td colspan="11">${inner}</td></tr>`;
    }
  });
  morphInto(document.getElementById('fleetBody'), fleetHtml);

  // process footer
  let foot=[];
  if(f.tagged.length) foot.push('已标注(非fleet):'+f.tagged.map(t=>`<span class="tag ${t.private?'lock':''}">${esc(t.label)} pid ${t.pid}${t.private?' 🔒':(t.cwd?' · '+esc(t.cwd):'')}</span>`).join(''));
  foot.push(`<span class="muted">在跑 claude ${f.alive_count} · 在岗(已认领)${f.fleet_count} · 已标注 ${f.tagged.length} · 未声明 ${f.unknown.length}</span>`);
  if(f.unknown.length) foot.push('未声明(非fleet·未标记):'+f.unknown.map(u=>`<span class="tag">pid ${u.pid}${u.cwd?' · '+esc(u.cwd):''}</span>`).join(''));
  document.getElementById('procFoot').innerHTML=foot.join('<br>');

  // daemon 自愈面板(fleet.json × 存活)
  const db=document.getElementById('daemonBox'); const dm=state.daemons||{daemons:[]};
  if(dm.error){ morphInto(db,'<div class="dmn"><span class="badge dead">err</span> '+esc(dm.error)+'</div>'); }
  else if(!dm.daemons.length){ morphInto(db,'<div class="muted" style="padding:8px 16px">fleet.json 无 daemon 条目</div>'); }
  else{ morphInto(db, dm.daemons.map(d=>`<div class="dmn" data-key="dm-${esc(d.role)}">
      <span class="badge ${d.alive?'live':'dead'}">${d.alive?'✓ 存活':'✗ 已死'}</span>
      <b>${esc(d.role)}</b>
      <span class="cwd" title="${esc(d.cwd||'')}">${esc(d.cwd||'')}</span>
      <span class="spacer" style="flex:1"></span>
      <span class="muted">wait ${esc(d.wait_timeout)}s</span></div>`).join('')); }

  // 最近事件(events.jsonl 倒序)
  const eb=document.getElementById('eventBox'); const evs=state.events||[];
  if(!evs.length){ morphInto(eb,'<div class="muted" style="padding:8px 16px">（无事件）</div>'); }
  else{ morphInto(eb, evs.map(e=>{
      const tail=e.event==='send'?('→ '+esc(e.to)+(e.title?' · '+esc(e.title):'')):
                 e.event==='claim'?('by '+esc(e.role)):
                 e.event==='done'?('→ '+esc(e.to)+' · '+esc(e.status||'')):(esc(e.raw||''));
      return `<div class="ev" data-key="ev-${esc(e.id||'')}-${esc(e.ts||'')}-${esc(e.event||'')}"><span class="ets">${esc(e.ts)}</span>
        <span class="et ${esc(e.event)}">${esc(e.event)}</span>
        <span class="mono">${esc(e.id||'')}</span>
        ${e.frm?'<span class="muted">from '+esc(e.frm)+'</span>':''}
        <span>${tail}</span></div>`; }).join('')); }

  // send-to dropdown (preserve selection) —— morph 保选中 + 聚焦时不被刷
  const sel=document.getElementById('sendTo'); const cur=sel.value;
  morphInto(sel, state.all_roles.map(r=>`<option data-key="o-${esc(r)}" ${r==cur?'selected':''}>${esc(r)}</option>`).join(''));

  // reply role dropdown
  const rr=document.getElementById('replyRole'); const rc=rr.value||state.reply_role;
  morphInto(rr, state.all_roles.map(r=>`<option data-key="o-${esc(r)}" ${r==rc?'selected':''}>${esc(r)}</option>`).join(''));

  // replies
  const rb=document.getElementById('replyBox');
  if(!state.replies.length){ morphInto(rb,'<div class="muted" style="padding:6px 16px">（无回填）</div>'); }
  else{ morphInto(rb, state.replies.map(r=>`<div class="reply" data-key="rp-${esc(r.file||r.id)}">
      <div class="r-h"><span class="badge ${r.status==='ok'?'live':'dead'}">${esc(r.status||'?')}</span>
        <span class="mono">${esc(r.id)}</span> · from <b>${esc(r.from)}</b>
        <span>${esc(r.title)}</span> <span class="muted">${esc(r.finished)}</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" style="margin:0;padding:2px 9px" onclick="clearOne('${esc(r.file)}')">清除</button></div>
      <pre>${esc(r.result)}</pre></div>`).join('')); }

  renderTodos();
  fillLiveRoles();
}

// 实时视图 live.log —— .log 不走 SSE,选中 role 后独立轮询(1.5s)
let livePoll=null;
function fillLiveRoles(){
  const sel=document.getElementById('liveRole'); if(!sel) return;
  const cur=sel.value;
  morphInto(sel, ['<option value="" data-key="o-">（选 role）</option>']
    .concat((state.all_roles||[]).map(r=>`<option data-key="o-${esc(r)}" ${r==cur?'selected':''}>${esc(r)}</option>`)).join(''));
}
async function pollLive(){
  const role=document.getElementById('liveRole').value;
  const box=document.getElementById('liveBox');
  const stat=document.getElementById('liveStat');
  if(!role){ box.textContent='（未选 role）'; stat.textContent='未选 role'; return; }
  try{
    const r=await fetch('/api/live?role='+encodeURIComponent(role));
    const j=await r.json();
    const atBottom=box.scrollTop+box.clientHeight>=box.scrollHeight-40;
    if(!j.ok){ box.textContent='读取失败:'+esc(j.error||'?'); stat.textContent='错误'; return; }
    if(!j.exists){ box.textContent='（'+esc(role)+' 暂无 live.log —— 该 role 可能不是 headless 执行体,或还没跑过任务）'; stat.textContent='无 live.log'; }
    else{ box.textContent=j.content||'（空）'; stat.textContent='更新 '+esc(j.mtime||'')+' · 末尾'+Math.min(j.size,12288)+'B/共'+j.size+'B'; if(atBottom) box.scrollTop=box.scrollHeight; }
  }catch(e){ stat.textContent='拉取异常'; }
}
function setLivePoll(){
  if(livePoll){clearInterval(livePoll);livePoll=null;}
  const role=document.getElementById('liveRole').value;
  const dot=document.getElementById('liveDot');
  if(role){ dot.className='live-dot on'; pollLive(); livePoll=setInterval(pollLive,1500); }
  else{ dot.className='live-dot'; document.getElementById('liveBox').textContent='（选一个 role 看它的 live.log）';
        document.getElementById('liveStat').textContent='未选 role'; }
}

const PRI_CLS={'高':'p-hi','中':'p-mid','低':'p-lo'};
const PRI_CARD={'高':'pri-hi','中':'pri-mid','低':'pri-lo'};
const ST_CLS={'待办':'s-todo','进行中':'s-doing','完成':'s-done','搁置':'s-hold'};
function isOverdue(t){
  if(!t.deadline||t.status==='完成') return false;
  const d=new Date(t.deadline.replace(' ','T'));
  if(isNaN(d)) return false;
  return d.getTime() < Date.now();
}
let todoView='active';   // active=待办池(未完成) | history=已完成历史
const roleSel={};        // todoId → 用户为该卡选的「派给」role;重渲染后回填,防自动刷新冲掉选择(误派根因)
function roleOptsFor(t){
  const sel=roleSel[t.id]||'';
  let h='<option value="" '+(sel?'':'selected')+' disabled>派给…</option>';
  (state.all_roles||[]).forEach(r=>{ h+='<option value="'+esc(r)+'" '+(sel===r?'selected':'')+'>'+esc(r)+'</option>'; });
  return h;
}
// 响应式重构(morphdom):卡内【临时 UI 态】全部存 JS、由 cardHtml 渲染出来 → morphdom 增量 patch DOM 与之对齐,
// 刷新时天然不丢(不再靠 dirtyCards 手工保护整卡)。临时态有两处:
//   · roleSel[id]  —— 用户为该卡选的「派给」role(下拉选择)。
//   · openEditors[id] —— 该卡的补充说明编辑器草稿文本(打开中);undefined=未打开。
const openEditors={};
const answerDrafts={};   // todoId → 🙋待确认卡回复框草稿(响应式,morphdom 刷新不丢)
// 待确认:agent 通过 ask-user 投池、在等用户拍板的项(source 以「确认·」开头 或 带 confirm_from,且 status=待办)
function isPendingConfirm(t){
  return t.status==='待办' && (t.confirm_from || (t.source||'').indexOf('确认·')===0);
}
function cardHtml(t){
  const od=isOverdue(t);
  const pc=isPendingConfirm(t);
  const stSel=['待办','进行中','搁置','完成'].map(s=>`<option ${s===t.status?'selected':''}>${s}</option>`).join('');
  const link=t.task_id?`<span class="linkpill" title="已派给 ${esc(t.dispatched_to||'')}">🔗 ${esc(t.dispatched_to||'')} · ${esc(t.task_id)}</span>`:'';
  const confirmBadge=pc?`<span class="badge confirm">🙋 待确认</span>`:'';
  const confirmWho=pc&&t.confirm_from?`<span class="confirm-who">等 ${esc(t.confirm_from)} 拍板</span>`:'';
  const who=t.confirm_from||t.creator||t.source||'用户';   // 谁发的(创建者/来源)
  // 补充说明区:由 openEditors 状态驱动(打开=渲染 textarea+草稿;关闭=渲染按钮)→ morphdom 刷新天然保留
  const editing = openEditors[t.id] !== undefined;
  const noteHtml = editing
    ? `<textarea class="note-edit" id="ne-${t.id}" placeholder="给这条待办加补充说明/上下文(随时改)" oninput="openEditors['${t.id}']=this.value">${esc(openEditors[t.id])}</textarea>`
      + `<div class="note-btns"><button class="btn mini" onclick="tNoteSave('${t.id}')">保存</button>`
      + `<button class="btn mini ghost" onclick="tNoteCancel('${t.id}')">取消</button></div>`
    : (t.note
        ? `<span class="unote">💬 补充:${esc(t.note)}</span><button class="btn mini ghost nbtn" onclick="tNoteEdit('${t.id}')">改</button>`
        : `<button class="btn mini ghost nbtn" onclick="tNoteEdit('${t.id}')">+ 补充说明</button>`);
  return `<div class="todo-card ${PRI_CARD[t.priority]||''} ${pc?'pending-confirm':''} ${t.status==='完成'?'is-done':''}" id="todocard-${t.id}" data-id="${t.id}">
      <div class="todo-h">
        ${confirmBadge}
        <span class="badge ${PRI_CLS[t.priority]||'p-mid'}">${esc(t.priority)}</span>
        <span class="badge ${ST_CLS[t.status]||'s-todo'}">${esc(t.status)}</span>
        <span class="todo-title">${esc(t.title)}</span>
        ${confirmWho}
        ${link}
      </div>
      ${t.detail?`<div class="todo-detail">${esc(t.detail)}</div>`:''}
      <div class="todo-meta">
        <span class="who-pill" title="谁发的(创建者/来源)">👤 ${esc(who)}</span>
        ${t.source&&t.source!==who?`<span>来源:${esc(t.source)}</span>`:''}
        ${t.deadline?`<span class="${od?'overdue':''}">截止:${esc(t.deadline)}${od?' ⚠逾期':''}</span>`:''}
        <span>建:${esc(t.created)}</span>
        ${t.dispatch_note?`<span class="dnote" title="${esc(t.dispatch_note_by||'')} @ ${esc(t.dispatch_note_at||'')}">📝 附加:${esc(t.dispatch_note)}</span>`:''}
        ${t.ignored?`<span class="badge failed">🚫 已忽略</span>`:''}
        ${t.dispatch_result?`<span class="dnote" title="派活结果 status=${esc(t.dispatch_result_status||'')}">✅ 结果:${esc(t.dispatch_result)}</span>`:''}
        ${t.updated&&todoView==='history'?`<span>完成/更新:${esc(t.updated)}</span>`:''}
      </div>
      <div class="note-area" id="na-${t.id}">${noteHtml}</div>
      ${(pc && t.confirm_from && t.confirm_from!=='bus健康' && t.confirm_from!=='unknown') ? `<div class="answer-area">
        <textarea class="ans-edit" id="ans-${t.id}" placeholder="回复 ${esc(t.confirm_from)} 的提问 → 发送后自动路由回它、本项标完成" oninput="answerDrafts['${t.id}']=this.value">${esc(answerDrafts[t.id]||'')}</textarea>
        <button class="btn mini" onclick="tAnswer('${t.id}')">↩ 回复 ${esc(t.confirm_from)}</button>
      </div>` : ''}
      <div class="todo-acts">
        <span class="muted">状态</span>
        <select onchange="tStatus('${t.id}',this.value)">${stSel}</select>
        <span class="muted" style="margin-left:6px">派给</span>
        <select id="trole-${t.id}" onchange="roleSel['${t.id}']=this.value">${roleOptsFor(t)}</select>
        <button class="btn mini" onclick="tDispatch('${t.id}')">派活</button>
        ${t.status!=='完成'?`<button class="btn mini ghost" onclick="tIgnore('${t.id}')" title="标记了结:不派活、不发任何 bus 任务">忽略</button>`:''}
        <button class="btn mini ghost" onclick="tEdit('${t.id}')">编辑</button>
        <button class="btn mini danger" onclick="tDelete('${t.id}')">删除</button>
      </div>
    </div>`;
}
function renderTodos(){
  const box=document.getElementById('todoBox'); if(!box) return;
  const all=(state.todos||[]);
  // 计数/角标/tab 高亮 —— 始终更新(不碰卡片 DOM,不受脏卡影响)
  const activeAll=all.filter(t=>t.status!=='完成');
  const doneAll=all.filter(t=>t.status==='完成');
  const pendN=activeAll.filter(isPendingConfirm).length;
  document.getElementById('tcountActive').textContent=activeAll.length;
  document.getElementById('tcountHistory').textContent=doneAll.length;
  const ca=document.getElementById('confirmAlert');
  if(pendN){ ca.style.display=''; ca.textContent='🙋 '+pendN+' 项待你确认'; } else { ca.style.display='none'; }
  const gc=document.getElementById('gConfirm');
  if(gc){ if(pendN){ gc.style.display=''; gc.innerHTML='🙋 <b>'+pendN+'</b> 项待确认'; } else { gc.style.display='none'; } }
  document.getElementById('tviewActive').classList.toggle('active',todoView==='active');
  document.getElementById('tviewHistory').classList.toggle('active',todoView==='history');

  let items=(todoView==='history'?doneAll:activeAll).slice();
  const fp=document.getElementById('filtPri').value;
  const fs=document.getElementById('filtStatus').value;
  if(fp) items=items.filter(t=>t.priority===fp);
  if(fs) items=items.filter(t=>t.status===fs);
  if(todoView==='active'){ items.sort((a,b)=>(isPendingConfirm(b)?1:0)-(isPendingConfirm(a)?1:0)); }
  else{ items.sort((a,b)=>String(b.updated||'').localeCompare(String(a.updated||''))); }

  const inner = items.length
    ? items.map(cardHtml).join('')
    : '<div class="muted" style="padding:8px 16px">'+(todoView==='history'?'（暂无已完成）':'（无未完成待办,或被筛选）')+'</div>';
  // 增量 DOM diff:morphdom 把 #todoBox 的孩子 patch 成新 html,只动变化的节点。
  //  · getNodeKey 用 id(卡片 id=todocard-<id>)→ 卡片按身份匹配、原地更新,不重建 → 不闪、滚动位不跳。
  //  · onBeforeElUpdated:用户正在输入/选择的表单控件(textarea/select/input)直接跳过 patch →
  //    光标、未提交的下拉态、草稿绝不被刷新冲掉。其余卡内态(草稿/派给选择)已由 openEditors/roleSel 渲进 html,morphdom 自然对齐。
  morphdom(box, '<div id="todoBox">'+inner+'</div>', {
    getNodeKey: el => (el.nodeType===1 && el.id) ? el.id : null,
    onBeforeElUpdated(fromEl, toEl){
      if(fromEl===document.activeElement &&
         (fromEl.tagName==='TEXTAREA'||fromEl.tagName==='SELECT'||fromEl.tagName==='INPUT')) return false;
      return true;
    }
  });
}

async function tPost(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
async function tStatus(id,val){ const j=await tPost('/api/todos/update',{id,status:val}); if(j.ok)toast('状态已改为 '+val); else toast('改状态失败:'+(j.error||'?'),true); load(); }
async function tDelete(id){ if(!confirm('删除这条待办?')) return; const j=await tPost('/api/todos/delete',{id}); toast(j.ok?'已删除':'删除失败',!j.ok); load(); }
async function tDispatch(id){
  const to=document.getElementById('trole-'+id).value;
  if(!to){ toast('先选「派给…」要发给哪个 role',true); return; }   // 占位未选 → 不派,防误触
  // 先明确确认派活(发 bus 任务),再问可选附加说明 —— 别让随手点确定=派活
  const t=(state.todos||[]).find(x=>x.id===id);
  const hasNote=!!(t&&t.note&&t.note.trim());
  const carry=hasNote?'\n这条待办的 💬补充说明 会【自动带给】'+to+',无需重写。':'';
  if(!confirm('确定把这条待办【派活】给 '+to+'?\n会给 '+to+' 发一个 bus 任务。'+carry+'\n(只想了结不处理→点取消,改用「忽略」)')){ return; }
  // 派活额外说明 = 只填派活当下的额外话;待办常驻💬补充说明由后端自动带上,不在此重复
  const extra=prompt((hasNote?'💬补充说明已自动带上。':'')+'【派活额外说明】(可空)——只填派活当下要补的额外话,不用重复补充说明。\n点【确定】即派给 '+to+';点【取消】则不派:','');
  if(extra===null){ return; }   // 取消 = 不派
  const j=await tPost('/api/todos/dispatch',{id,to,note:extra.trim()});
  if(j.ok){delete roleSel[id];toast('✅ 已派给 '+to+(hasNote?'(补充说明已带上)':'')+(extra.trim()?'(+额外说明)':'')+' · task '+j.task_id);}else{toast('派活失败:'+(j.error||'?'),true);}
  load();
}
async function tIgnore(id){
  if(!confirm('忽略这条?将标记为【已了结】(状态置完成+已忽略),不派活、不发任何任务,移入历史。')) return;
  const j=await tPost('/api/todos/update',{id,status:'完成',ignored:true});
  toast(j.ok?'已忽略(移入历史)':'操作失败:'+(j.error||'?'),!j.ok); load();
}
// 常驻可编辑【补充说明】(note):inline 编辑,草稿存 openEditors[id](响应式)→ 刷新中天然不丢。
function tNoteEdit(id){
  const t=(state.todos||[]).find(x=>x.id===id); if(!t) return;
  openEditors[id]=t.note||'';   // 进入编辑态(草稿入 state)→ cardHtml 渲染出 textarea
  renderTodos();
  const ta=document.getElementById('ne-'+id); if(ta){ ta.focus(); try{ta.setSelectionRange(ta.value.length,ta.value.length);}catch(e){} }
}
async function tNoteSave(id){
  const val=(openEditors[id]||'').trim();
  let j; try{ j=await tPost('/api/todos/update',{id,note:val}); }catch(e){ toast('保存失败:'+e,true); return; }
  delete openEditors[id];   // 关闭编辑态
  toast(j&&j.ok?'补充说明已保存':'保存失败:'+((j&&j.error)||'?'),!(j&&j.ok));
  load();
}
function tNoteCancel(id){ delete openEditors[id]; renderTodos(); }
// 🙋待确认卡回复 → 闭环 ask-user:把答复路由回提问 agent(confirm_from)+ 标本项完成
async function tAnswer(id){
  const reply=(answerDrafts[id]||'').trim();
  if(!reply){ toast('回复内容不能为空',true); return; }
  const t=(state.todos||[]).find(x=>x.id===id); const who=t&&t.confirm_from||'提问方';
  let j; try{ j=await tPost('/api/todos/answer',{id,reply}); }catch(e){ toast('回复失败:'+e,true); return; }
  if(j&&j.ok){ delete answerDrafts[id]; toast('↩ 已回复 '+who+',答复已路由、本项标完成'); }
  else{ toast('回复失败:'+((j&&j.error)||'?'),true); }
  load();
}
function tEdit(id){
  const t=(state.todos||[]).find(x=>x.id===id); if(!t) return;
  const title=prompt('标题',t.title); if(title===null) return;
  const detail=prompt('详情',t.detail||''); if(detail===null) return;
  const source=prompt('来源',t.source||''); if(source===null) return;
  const deadline=prompt('截止(如 2026-06-23 09:00,留空清除)',t.deadline||''); if(deadline===null) return;
  const priority=prompt('优先级(高/中/低)',t.priority)||t.priority;
  tPost('/api/todos/update',{id,title,detail,source,deadline,priority}).then(j=>{toast(j.ok?'已保存':'保存失败:'+(j.error||'?'),!j.ok);load();});
}

let _loadFails=0;
async function load(role, fresh){
  const rr=role||document.getElementById('replyRole').value||'Master';
  const ctrl=new AbortController(); const tid=setTimeout(()=>ctrl.abort(),15000);  // 15s 超时,防卡死
  try{
    const res=await fetch('/api/state?reply_role='+encodeURIComponent(rr)+(fresh?'&fresh=1':''),{signal:ctrl.signal});
    if(!res.ok) throw new Error('HTTP '+res.status);
    const d=await res.json();
    if(!d || typeof d!=='object' || !d.fleet) throw new Error('payload 不完整');  // 半截/空响应不覆盖
    // 舰队还在后台子进程算(冷启/刷新中)→ 用上次的舰队数据,别闪空表(其余区照常用新数据)
    if(d.fleet.loading && state && state.fleet && !state.fleet.loading && state.fleet.roles && state.fleet.roles.length){
      d.fleet=state.fleet;
    }
    state=d; _loadFails=0; render();
    const ut=document.getElementById('updT'); ut.classList.remove('flash');void ut.offsetWidth; ut.classList.add('flash');
  }catch(e){
    // 韧性:后端偶发慢/超时/半截 —— 【绝不清空页面】,保留上次 state 与已渲染 DOM,标记重试;下次轮询/SSE 自动再拉
    _loadFails++;
    console.warn('load 失败(保留上次看板,自动重试):',e&&e.message);
    const ut=document.getElementById('updT'); if(ut) ut.textContent='⚠ 刷新慢/重试中('+_loadFails+')· 显示上次数据';
    if(_loadFails<=2){ setTimeout(()=>load(role,fresh), 1500); }   // 轻量自动重试两次
  }finally{ clearTimeout(tid); }
}

function setPoll(){
  if(pollTimer){clearInterval(pollTimer);pollTimer=null;}
  const v=+document.getElementById('pollSel').value;
  if(v>0) pollTimer=setInterval(()=>load(),v);
}

// SSE — watchdog 文件事件实时推送;断了走轮询兜底
function connectSSE(){
  try{
    const es=new EventSource('/api/events');
    es.onopen=()=>setConn(true);
    es.onmessage=()=>load(null,true);   // 真事件(任务/完成)→ 强制新算,不吃缓存延迟
    es.onerror=()=>{setConn(false); es.close(); setTimeout(connectSSE,3000);};
  }catch(e){ setConn(false); }
}

async function clearOne(file){
  const role=document.getElementById('replyRole').value;
  await fetch('/api/clear_replies',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({role,file})});
  toast('已清除 '+file); load();
}

document.getElementById('refreshBtn').onclick=()=>load(null,true);
document.getElementById('pollSel').onchange=setPoll;
document.getElementById('replyRole').onchange=()=>load(document.getElementById('replyRole').value);
document.getElementById('clearAllBtn').onclick=async()=>{
  const role=document.getElementById('replyRole').value;
  if(!confirm('清空 '+role+'/done 全部回填?')) return;
  const r=await fetch('/api/clear_replies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role})});
  const j=await r.json(); toast('已清空 '+j.cleared+' 条'); load();
};
document.getElementById('sendBtn').onclick=async()=>{
  const to=document.getElementById('sendTo').value;
  const title=document.getElementById('sendTitle').value.trim();
  const task=document.getElementById('sendTask').value.trim();
  const notify=document.getElementById('sendNotify').value;
  const requires_approval=document.getElementById('sendApproval').checked;
  if(!task){toast('任务内容不能为空',true);return;}
  const btn=document.getElementById('sendBtn');btn.disabled=true;
  try{
    const r=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({to,title,task,notify,requires_approval})});
    const j=await r.json();
    if(j.ok){toast('✅ 已派给 '+to+(requires_approval?'(高风险·留 inbox 待批)':'')+' · id '+j.id);
      document.getElementById('sendTitle').value='';document.getElementById('sendTask').value='';
      document.getElementById('sendApproval').checked=false;load();}
    else toast('发送失败:'+(j.error||'?'),true);
  }catch(e){toast('发送失败:'+e,true);}
  btn.disabled=false;
};

// 孤儿巡检(reap --dry-run)
document.getElementById('reapBtn').onclick=async()=>{
  const box=document.getElementById('reapBox');const btn=document.getElementById('reapBtn');
  btn.disabled=true;box.textContent='巡检中…';
  try{
    const r=await fetch('/api/reap');const j=await r.json();
    if(!j.ok&&j.error){box.innerHTML='<span style="color:var(--dead)">巡检失败:'+esc(j.error)+'</span>';}
    else if(!j.lines.length){box.textContent='(无孤儿任务)';}
    else{box.innerHTML='<pre style="margin:0;white-space:pre-wrap;color:var(--warn);background:var(--bg);padding:8px 10px;border-radius:6px">'+j.lines.map(esc).join('\n')+'</pre>';}
  }catch(e){box.innerHTML='<span style="color:var(--dead)">巡检失败:'+esc(e)+'</span>';}
  btn.disabled=false;
};

// daemon 自愈(supervise 一次)
document.getElementById('superviseBtn').onclick=async()=>{
  if(!confirm('立即跑一次 supervise?会重启 fleet.json 里【已死】的 daemon。')) return;
  const btn=document.getElementById('superviseBtn');btn.disabled=true;
  try{
    const r=await fetch('/api/supervise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
    const j=await r.json();
    toast((j.ok?'supervise 完成':'supervise 出错')+(j.lines&&j.lines.length?':'+j.lines.join(' / '):'')+(j.error?' ['+j.error+']':''),!j.ok);
    load();
  }catch(e){toast('supervise 失败:'+e,true);}
  btn.disabled=false;
};

// 待办池:新增 + 筛选
document.getElementById('tAddBtn').onclick=async()=>{
  const title=document.getElementById('tAddTitle').value.trim();
  if(!title){toast('标题不能为空',true);return;}
  const body={title,
    detail:document.getElementById('tAddDetail').value.trim(),
    source:document.getElementById('tAddSource').value.trim(),
    deadline:document.getElementById('tAddDeadline').value.trim(),
    priority:document.getElementById('tAddPri').value,
    status:document.getElementById('tAddStatus').value};
  const j=await tPost('/api/todos/add',body);
  if(j.ok){toast('已新增待办');
    document.getElementById('tAddTitle').value='';document.getElementById('tAddDetail').value='';
    document.getElementById('tAddSource').value='';document.getElementById('tAddDeadline').value='';
    load();}
  else toast('新增失败:'+(j.error||'?'),true);
};
document.getElementById('filtPri').onchange=renderTodos;
document.getElementById('filtStatus').onchange=renderTodos;
document.getElementById('tviewActive').onclick=()=>{todoView='active';renderTodos();};
document.getElementById('tviewHistory').onclick=()=>{todoView='history';renderTodos();};

// 实时视图 live.log
document.getElementById('liveRole').onchange=setLivePoll;
document.getElementById('liveBottom').onclick=()=>{const b=document.getElementById('liveBox');b.scrollTop=b.scrollHeight;};

// ===== BI · 资源消耗 =====
function fmtN(n){ n=n||0; if(n>=1e6)return (n/1e6).toFixed(2)+'M'; if(n>=1e3)return (n/1e3).toFixed(1)+'k'; return ''+n; }
function fmtDur(s){ s=s||0; if(s>=60){const m=Math.floor(s/60);return m+'m'+Math.round(s%60)+'s';} return s+'s'; }
function renderBI(bi){
  if(!bi) return;
  document.getElementById('biUpd').textContent=bi.now+(bi.psutil?'':' · 无psutil(系统资源不可用)');
  const tu=bi.today_usage||{};
  const st=bi.sys_total;
  const fmtMem=mb=>mb>=1024?(mb/1024).toFixed(2)+' GB':Math.round(mb)+' MB';
  const kpi=[
    ['bus 总内存', st?fmtMem(st.rss_mb):'—', st?(st.roles+' 角色 · '+st.nproc+' 进程 · '+st.cpu+'% CPU'):'无psutil', false],
    ['今日 token(总)', fmtN(tu.tokens_total||0), (tu.date?(tu.is_today?tu.date:('库存至 '+tu.date)):'usage.db'), false],
    ['今日 io(in+out)', fmtN(tu.tokens_io||0), 'in '+fmtN(tu.input||0)+' / out '+fmtN(tu.output||0), false],
    ['今日成本($参考)', usd(tu.cost_usd||0), '', false],
    ['今日 calls', fmtN(tu.calls||0), '', false],
    ['在跑任务', bi.running.length, '', false]
  ];
  morphInto(document.getElementById('biKpis'), kpi.map(k=>
    `<div class="bi-kpi${k[3]?' warn':''}" data-key="k-${esc(k[0])}"><div class="k">${esc(k[0])}</div><div class="v">${k[1]}${k[2]?'<small>'+esc(k[2])+'</small>':''}</div></div>`).join(''));
  morphInto(document.getElementById('biRunning'), bi.running.length? bi.running.map(r=>
    `<span class="bi-run" data-key="run-${esc(r.role)}"><span class="live-dot on"></span><b>${esc(r.role)}</b> ${esc(r.title||r.task)}</span>`).join('')
    : '<span class="muted" style="font-size:12px">当前无在跑任务</span>');
  // 在岗资源占用:仅列有活进程(sys)的 role,按内存降序(metrics 任务消耗口径已随 daemon 移除退役,成本看「历史用量」)
  const live=(bi.per_role||[]).filter(r=>r.sys).sort((a,b)=>(b.sys.rss_mb||0)-(a.sys.rss_mb||0));
  morphInto(document.getElementById('biSysBody'), !live.length
    ? '<tr><td colspan="3" class="muted" style="padding:10px 16px">无在岗进程资源数据（psutil 不可用或无活进程）</td></tr>'
    : live.map(r=>`<tr data-key="bs-${esc(r.role)}">
      <td><b>${esc(r.role)}</b></td>
      <td class="num">${r.sys.cpu}</td>
      <td class="num">${r.sys.rss_mb}</td></tr>`).join(''));
}
async function loadBI(){ try{ const r=await fetch('/api/bi'); renderBI(await r.json()); }catch(e){} }

// ===== 历史用量 BI (P0) =====
function _fit(cv,cssH){
  const dpr=window.devicePixelRatio||1;
  const cssW=cv.clientWidth||(cv.parentElement&&cv.parentElement.clientWidth)||600;
  cv.width=Math.round(cssW*dpr); cv.height=Math.round(cssH*dpr); cv.style.height=cssH+'px';
  const ctx=cv.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); return {ctx,w:cssW,h:cssH};
}
const UC={bg:'#0f1115',line:'#2a2f3a',dim:'#9aa3b2',acc:'#5b9dff',live:'#3ecf8e',warn:'#f5a623',dead:'#ff5d5d',idle:'#7d8aa0',none:'#5a6172'};
function usd(n){ return '$'+(n||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); }
// token 口径(用户定:token 才是固定物理量;$ 会随 plan/定价浮动)。总=io+cache_read+cache_write;io=新增有效工作量。
function _tokTotals(t){ t=t||{};
  const io=(t.input||0)+(t.output||0);
  const cache=(t.cache_read||0);
  const cw=(t.cache_write_5m||0)+(t.cache_write_1h||0);
  const total=io+cache+cw;
  return {io,cache,cw,total,cachePct: total?Math.round(cache/total*100):0};
}
function modelColor(m){ if(m.indexOf('opus-4-8')>=0)return UC.acc; if(m.indexOf('opus-4-7')>=0)return UC.idle; if(m.indexOf('haiku')>=0)return UC.live; return UC.none; }
function modelShort(m){ return m.replace('claude-','').replace('-20251001',''); }
function projShort(p){ return p ? p.replace(/^([A-Za-z])--/, '$1:/').replace(/-/g, '/') : ''; }

let _usageData=null, _uRange=30;
function sliceDaily(daily,r){
  // 按"日历范围"切片(以数据最后一天为锚,非系统今天 — 与数据对齐、免时区抖动)
  if(!r||!daily||!daily.length) return (daily||[]).slice();
  const last=daily[daily.length-1].date;
  const d=new Date(last+'T00:00:00'); d.setDate(d.getDate()-(r-1));
  const y=d.getFullYear(), m=('0'+(d.getMonth()+1)).slice(-2), dd=('0'+d.getDate()).slice(-2);
  const cut=y+'-'+m+'-'+dd;
  return daily.filter(x=>x.date>=cut);
}
function renderUsage(d){
  const kp=document.getElementById('usageKpis');
  if(!d || d.error){ kp.innerHTML='<div class="muted" style="padding:6px 12px">用量数据读取失败:'+esc(d&&d.error||'?')+'</div>'; return; }
  _usageData=d;
  const mt=d.meta||{};
  const cfg=mt.config_active||{alias_count:mt.alias_count,merge_groups:mt.merge_groups,ignore_projects:mt.ignore_projects};
  document.getElementById('usageMeta').textContent='刷新于 '+esc(d.as_of||'')
    +' · 跟踪 '+(mt.tracked_jsonl_files!=null?mt.tracked_jsonl_files:'?')+' jsonl'
    +(cfg?(' · alias '+(cfg.alias_count||0)+'/merge '+(cfg.merge_groups||0)+'/ignore '+(cfg.ignore_projects||0)):'');
  // 范围相关 KPI + 趋势:交给 applyRange;范围按钮绑定一次
  const rg=document.getElementById('uRange');
  if(rg && !rg._bound){ rg._bound=1; rg.querySelectorAll('button').forEach(b=>b.onclick=()=>applyRange(+b.dataset.r)); }
  _bindTrendHover('uTrend','uTrendTip'); _bindTrendHover('uTrendTotal','uTrendTotalTip');
  applyRange(_uRange);
  // 项目 Top N —— token 为主(用量统计侧已补 by_project token 分项):按 tokens_total 排;并列 io + $(参考),与 KPI 同口径
  let projs=(d.by_project||[]).slice();
  const tokOf=p=>p.tokens_total!=null?p.tokens_total:(p.input!=null?((p.input||0)+(p.output||0)+(p.cache_read||0)+(p.cache_write_5m||0)+(p.cache_write_1h||0)):null);
  const ioOf=p=>p.tokens_io!=null?p.tokens_io:(p.input!=null?((p.input||0)+(p.output||0)):null);
  const hasTok=projs.some(p=>tokOf(p)!=null);
  const valOf=p=>hasTok?(tokOf(p)||0):(p.cost_usd||0);
  projs.sort((a,b)=>valOf(b)-valOf(a)); projs=projs.slice(0,8);
  const max=projs.length?valOf(projs[0]):1;
  // 对数刻度:小项目彼此差几十倍,线性会全钳到 min 看不出;log 把它们拉开(条长仅视觉相对,绝对值看右侧数字)
  const lmin=projs.length?Math.log(Math.max(1,valOf(projs[projs.length-1]))):0;
  const lmax=Math.log(Math.max(1,max)), lspan=(lmax-lmin)||1;
  const unit=document.getElementById('uProjUnit');
  if(unit) unit.textContent=hasTok?'(全历史 · 按 tokens_total 排 · 条=对数刻度 · io/$ 对照)':'(全历史 · $ API等价·参考 — token 分项待 token_stats 迁移补齐)';
  document.getElementById('uProj').innerHTML=projs.map((p,i)=>{
    const v=valOf(p); const pct=(8+(Math.log(Math.max(1,v))-lmin)/lspan*92).toFixed(1);
    const num=hasTok
      ? `${fmtN(tokOf(p))} tok <span style="color:var(--dim)">· io ${fmtN(ioOf(p))} · ${usd(p.cost_usd)}参考</span>`
      : usd(p.cost_usd);
    return `<div class="bar-row uproj-row" data-proj="${esc(p.project)}" title="点击展开原始目录构成">
      <span class="lbl">${esc(p.project)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${pct}%"></span></span>
      <span class="num">${num}</span></div>
      <div class="uproj-raw" id="uraw-${i}" style="display:none"></div>`;
  }).join('')||'<div class="muted" style="padding:6px 0">无数据</div>';
  bindProjDrill();
  // 模型迁移
  drawModels(d.by_month_model||[]);
}
function applyRange(r){
  _uRange=r;
  const rg=document.getElementById('uRange');
  if(rg) rg.querySelectorAll('button').forEach(b=>b.classList.toggle('on',+b.dataset.r===r));
  const d=_usageData; if(!d) return;
  const daily=sliceDaily(d.daily||[],r);
  const t=d.totals||{};
  const rangeCost=daily.reduce((s,x)=>s+(x.cost_usd||0),0);
  const rl=r?(r+'d'):'全历史';
  const cacheDenom=(t.cache_read||0)+(t.input||0)+(t.cache_write_5m||0)+(t.cache_write_1h||0);
  const cacheHit=cacheDenom?(Math.floor((t.cache_read||0)/cacheDenom*1000)/10).toFixed(1)+'%':'–';
  // 用户定 BI 口径:token 消耗提主位;总 token + io(有效工作量)并列对照 + cache_read 单列标注;$ 标(参考)
  const tk=_tokTotals(t);
  morphInto(document.getElementById('usageKpis'), [
    ['总 token(全历史)', fmtN(tk.total),'acc','input+output+cache_read+cache_write,固定物理量(不随 plan/定价浮动)'],
    ['io 有效工作量', fmtN(tk.io),'','input+output —— 真正的新 work / 有效工作量(不含 cache 重复读)'],
    ['cache_read 重复读', fmtN(tk.cache),'','重复读上下文 · 占总 token ~'+tk.cachePct+'%(总 token 被它灌水,故单列)'],
    ['cache 命中', cacheHit,'good','cache_read /(cache_read + cache_creation + input),输入侧命中占比'],
    ['API calls(去重)', fmtN(t.calls),''],
    ['API 等价成本(参考)', usd(t.cost_usd),'warn','$ 随 plan/定价浮动、plan 下还会下沉,仅作 API 等价参考;token 才是固定量'],
    ['范围成本('+rl+'·参考)', usd(rangeCost),'','daily 暂无 token 分项(token_stats 迁移后补),范围仍按 $ 参考'],
  ].map(k=>`<div class="kpi" data-key="uk-${esc(k[0])}"${k[3]?` title="${esc(k[3])}"`:''}><div class="k">${esc(k[0])}</div><div class="v ${k[2]}">${k[1]}</div></div>`).join(''));
  drawTrend(daily,{cv:'uTrend',mode:'io',peak:'trendPeak',tip:'uTrendTip',legend:'trendLegend'});
  drawTrend(daily,{cv:'uTrendTotal',mode:'total',peak:'trendPeakTotal',tip:'uTrendTotalTip'});
}
// 每日 token 趋势:口径混合(真实段=io=input+output;backfill 段=stats-cache 日聚合 total,口径或略异·供趋势参考);
// gap=无源缺口(不画点、line 断开 + 阴影带,绝不当 0)。横轴含 gap 槽 → 缺口在视觉上是真断层。
// 口径:mode='io'(input+output·新增有效工作量) | 'total'(io+cache读写·固定物理量总口径)
// backfill 段只有日聚合 total(无 io 分项),两模式都用 total(标注供参考);gap=无源缺口(断开·不当0)。
function _trendVal(x, mode){
  if(!x||x.src==='gap') return null;
  if(x.src==='backfill') return (x.tokens_total!=null?x.tokens_total:null);
  if(mode==='total') return (x.tokens_total!=null?x.tokens_total:null);
  return (x.tokens_io!=null?x.tokens_io:null);
}
// opt={cv,mode,peak,tip,legend?} —— 参数化:io/总两图各调一次,各自独立纵轴
function drawTrend(daily, opt){
  const cv=document.getElementById(opt.cv); if(!cv) return;
  const f=_fit(cv,190); const ctx=f.ctx,w=f.w,h=f.h;
  const pts=[]; const tip=opt.tip?document.getElementById(opt.tip):null; if(tip)tip.style.display='none';
  const peakEl=opt.peak?document.getElementById(opt.peak):null;
  const lg=opt.legend?document.getElementById(opt.legend):null;
  const padL=54,padR=14,padT=14,padB=22, gw=w-padL-padR, gh=h-padT-padB;
  const all=daily||[];
  const vals=all.map(x=>_trendVal(x,opt.mode));
  const has=vals.filter(v=>v!=null);
  if(!has.length){ ctx.fillStyle=UC.dim;ctx.fillText('无数据',padL,padT+20); if(peakEl)peakEl.textContent=''; if(lg)lg.innerHTML=''; cv._pts=[]; return; }
  const maxV=Math.max.apply(null,has)*1.12||1;
  const n=all.length;
  const X=i=>padL+gw*(n<2?0.5:i/(n-1)), Y=v=>padT+gh*(1-v/maxV);
  ctx.strokeStyle=UC.line;ctx.fillStyle=UC.dim;ctx.lineWidth=1;ctx.font='10px Consolas';
  for(let g=0;g<=4;g++){const gv=maxV*g/4,yy=Y(gv);ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();ctx.fillText(fmtN(Math.round(gv)),2,yy+3);}
  let gi=0; while(gi<n){ if(all[gi].src==='gap'){ let gj=gi; while(gj+1<n && all[gj+1].src==='gap') gj++;
      const x0=X(gi)-(gw/(n-1||1))*0.5, x1=X(gj)+(gw/(n-1||1))*0.5;
      ctx.fillStyle='rgba(125,138,160,.10)'; ctx.fillRect(Math.max(padL,x0),padT,Math.min(w-padR,x1)-Math.max(padL,x0),gh);
      ctx.fillStyle=UC.none; ctx.textAlign='center'; ctx.fillText('无源', (Math.max(padL,x0)+Math.min(w-padR,x1))/2, padT+gh/2); ctx.textAlign='left';
      gi=gj+1; } else gi++; }
  const lineColor=opt.mode==='total'?UC.live:UC.acc;   // 总口径用绿、io用蓝,两图一眼区分
  (function seg(){ let prev=null;
    all.forEach((p,i)=>{ const v=vals[i];
      if(v==null){ prev=null; return; }
      const px=X(i),py=Y(v);
      if(prev!=null){ ctx.beginPath(); ctx.moveTo(prev.x,prev.y); ctx.lineTo(px,py);
        const bf=(p.src==='backfill'); ctx.setLineDash(bf?[4,3]:[]); ctx.strokeStyle=bf?UC.idle:lineColor; ctx.lineWidth=2; ctx.stroke(); ctx.setLineDash([]); }
      prev={x:px,y:py};
    }); })();
  let pk=-1, pkv=-1;
  all.forEach((p,i)=>{ const v=vals[i]; if(v==null) return; if(v>pkv){pkv=v;pk=i;}
    const px=X(i),py=Y(v); ctx.beginPath(); ctx.arc(px,py,2.5,0,7); ctx.fillStyle=p.src==='backfill'?UC.idle:lineColor; ctx.fill();
    pts.push({x:px,y:py,date:p.date,v:v,src:p.src,cost:p.cost_usd,calls:p.calls}); });
  if(pk>=0){ const px=X(pk),py=Y(pkv); ctx.beginPath(); ctx.arc(px,py,4,0,7); ctx.fillStyle=UC.dead; ctx.fill(); }
  ctx.fillStyle=UC.dim;ctx.fillText(all[0].date.slice(5),padL-2,h-8);ctx.textAlign='right';ctx.fillText(all[n-1].date.slice(5),w-padR,h-8);ctx.textAlign='left';
  if(peakEl) peakEl.textContent='峰值 '+fmtN(pkv)+' tok · '+all[pk].date.slice(5)+(all[pk].src==='backfill'?'(backfill·口径异)':'')+' · '+n+'天';
  cv._pts=pts; cv._mode=opt.mode;
  if(lg){ const bm=_usageData&&_usageData.backfill_meta;
    lg.innerHTML='<span><i style="background:'+UC.acc+'"></i>io(蓝)</span>'
      +'<span><i style="background:'+UC.live+'"></i>总(绿)</span>'
      +'<span><i style="background:'+UC.idle+'"></i>backfill·日聚合</span>'
      +'<span><i class="gapi"></i>无源缺口</span>'
      +(bm?'<span class="muted" style="margin-left:auto">backfill '+esc(bm.bf_from||'')+'~'+esc(bm.bf_to||'')+' · stats-cache 日聚合,口径或略异·供参考;'+esc(bm.gap_from||'')+'~'+esc(bm.gap_to||'')+' 无源(非0)</span>':''); }
}
function _bindTrendHover(cvId, tipId){
  const cv=document.getElementById(cvId); if(!cv||cv._hov)return; cv._hov=1;
  const tip=document.getElementById(tipId);
  cv.addEventListener('mousemove',e=>{
    const pts=cv._pts||[]; if(!pts.length||!tip){if(tip)tip.style.display='none';return;}
    const rect=cv.getBoundingClientRect(); const mx=e.clientX-rect.left;
    let best=pts[0],bd=1e9;
    pts.forEach(p=>{const dd=Math.abs(p.x-mx); if(dd<bd){bd=dd;best=p;}});
    const lbl=cv._mode==='total'?'tok 总':'tok io';
    const tag = best.src==='backfill' ? ' <span style="color:'+UC.idle+'">·backfill日聚合</span>' : '';
    let line = best.src==='backfill'
      ? fmtN(best.v)+' tok <span class="muted">(stats-cache·口径或异)</span>'
      : fmtN(best.v)+' '+lbl+(best.cost!=null?' · '+usd(best.cost)+' 参考':'')+(best.calls!=null?' · '+fmtN(best.calls)+' calls':'');
    tip.innerHTML='<b>'+esc(best.date)+'</b>'+tag+'<br>'+line;
    tip.style.display='block';
    let lx=best.x+10; if(lx>rect.width-130)lx=best.x-122;
    tip.style.left=Math.max(2,lx)+'px'; tip.style.top=Math.max(2,best.y-36)+'px';
  });
  cv.addEventListener('mouseleave',()=>{if(tip)tip.style.display='none';});
}
function rawFor(logical){
  // 启发式把 by_project_raw 归到 logical(alias 多为同名;merge 组无法纯前端映射 → 由调用方兜底)
  const raws=(_usageData&&_usageData.by_project_raw)||[];
  const norm=s=>projShort(String(s)).toLowerCase().replace(/[^a-z0-9]/g,'');
  const l=String(logical).toLowerCase().replace(/[^a-z0-9]/g,'');
  return raws.filter(r=>{const n=norm(r.project); return n===l||(n&&l&&(n.indexOf(l)>=0||l.indexOf(n)>=0));});
}
function bindProjDrill(){
  const up=document.getElementById('uProj'); if(!up)return;
  up.querySelectorAll('.uproj-row').forEach((row,i)=>{
    row.onclick=()=>{
      const box=document.getElementById('uraw-'+i); if(!box)return;
      if(box.style.display!=='none'){ box.style.display='none'; return; }
      let raws=rawFor(row.dataset.proj), note='';
      if(!raws.length){ raws=((_usageData&&_usageData.by_project_raw)||[]).slice(0,12); note='<div class="muted" style="font-size:11px;padding:1px 0 3px">未能自动映射,列出全部原始目录:</div>'; }
      const rmax=raws.length?Math.max.apply(null,raws.map(x=>x.cost_usd))||1:1;
      box.innerHTML=note+raws.map(rr=>{const pct=Math.max(2,rr.cost_usd/rmax*100).toFixed(1);
        return `<div class="bar-row" style="grid-template-columns:150px 1fr 90px"><span class="lbl" title="${esc(rr.project)}">${esc(projShort(rr.project))}</span><span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:var(--idle)"></span></span><span class="num">${usd(rr.cost_usd)} · ${fmtN(rr.calls)}</span></div>`;
      }).join('')||'<div class="muted" style="font-size:11px;padding:2px 0">无</div>';
      box.style.display='block';
    };
  });
}
function drawModels(bmm){
  const cv=document.getElementById('uModels'); const f=_fit(cv,200); const ctx=f.ctx,w=f.w,h=f.h;
  const rows=bmm.filter(r=>r.model.indexOf('synthetic')<0);
  const months=[...new Set(rows.map(r=>r.month))].sort();
  const models=[...new Set(rows.map(r=>r.model))];
  if(!months.length){ ctx.fillStyle=UC.dim;ctx.fillText('无数据',20,30); return; }
  const padL=18,padR=14,padT=14,padB=26, gw=w-padL-padR, gh=h-padT-padB;
  const byMM={}; rows.forEach(r=>{byMM[r.month+'|'+r.model]=r.calls;});
  const tot=months.map(m=>models.reduce((s,md)=>s+(byMM[m+'|'+md]||0),0));
  const maxV=Math.max.apply(null,tot)*1.12||1; const bw=gw/months.length*0.5;
  ctx.font='10px Consolas';ctx.textAlign='center';
  months.forEach((m,mi)=>{
    const cx=padL+gw*(mi+0.5)/months.length-bw/2; let yCur=padT+gh; let t=tot[mi];
    models.forEach(md=>{const v=byMM[m+'|'+md]||0; if(!v)return; const bh=gh*v/maxV; yCur-=bh; ctx.fillStyle=modelColor(md); ctx.fillRect(cx,yCur,bw,bh);});
    ctx.fillStyle=UC.dim;ctx.fillText(m.slice(2),cx+bw/2,h-10);
    const v48=models.filter(md=>md.indexOf('opus-4-8')>=0).reduce((s,md)=>s+(byMM[m+'|'+md]||0),0);
    if(t){const pt=Math.round(v48/t*100)+'%';ctx.font='bold 10px Consolas';
      ctx.lineWidth=3;ctx.strokeStyle='rgba(15,17,21,.92)';ctx.strokeText(pt,cx+bw/2,yCur-5);
      ctx.fillStyle='#fff';ctx.fillText(pt,cx+bw/2,yCur-5);ctx.font='10px Consolas';}
  });
  ctx.textAlign='left';
  morphInto(document.getElementById('uModelLegend'), models.map(md=>`<span data-key="ml-${esc(md)}"><i style="background:${modelColor(md)}"></i>${esc(modelShort(md))}</span>`).join(''));
}
async function loadUsage(force){
  const kp=document.getElementById('usageKpis'); if(!kp.innerHTML) kp.innerHTML='<div class="muted" style="padding:6px 12px">加载中…</div>';
  try{ const r=await fetch('/api/usage'+(force?'?fresh=1':'')); renderUsage(await r.json()); }
  catch(e){ kp.innerHTML='<div class="muted" style="padding:6px 12px">加载失败:'+esc(e)+'</div>'; }
}

// ===== 派工/协作效率(metrics.jsonl · 仅自动化派工口径,非总账)=====
function renderDispatch(d){
  if(!d) return;
  const t=d.total||{};
  document.getElementById('dmMeta').textContent='刷新于 '+esc(d.now||'')+(d.span?(' · '+esc(d.span.from)+'→'+esc(d.span.to)):'');
  const ratio=(t.daemon||0)+':'+(t.dispatch||0);
  morphInto(document.getElementById('dmKpis'), [
    ['派工总成本', usd(t.cost), 'acc'],
    ['任务数', fmtN(t.tasks)+(t.errors?' ('+t.errors+'e)':''), ''],
    ['总 turns', fmtN(t.turns), ''],
    ['平均 $/任务', usd(t.avg_cost), ''],
    ['平均时长', fmtDur(t.avg_dur_s), ''],
    ['daemon:dispatch', ratio, 'warn'],
  ].map(k=>`<div class="kpi" data-key="dk-${esc(k[0])}"><div class="k">${esc(k[0])}</div><div class="v ${k[2]}">${k[1]}</div></div>`).join(''));
  // 各角色成本(降序条形 + $/turn 效率)
  const roles=d.per_role||[]; const rmax=roles.length?(roles[0].cost||1):1;
  morphInto(document.getElementById('dmRoles'), roles.map(r=>{
    const pct=Math.max(2,(r.cost/(rmax||1))*100).toFixed(1);
    return `<div class="bar-row" data-key="dr-${esc(r.role)}" style="grid-template-columns:120px 1fr auto"><span class="lbl" title="${esc(r.role)}">${esc(r.role)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${pct}%"></span></span>
      <span class="num">${usd(r.cost)} <span style="color:var(--dim)">· ${r.tasks}任务 · $${(r.per_turn||0).toFixed(3)}/t</span></span></div>`;
  }).join('')||'<div class="muted" style="padding:6px 16px">无数据</div>');
  // daemon vs dispatch 拆分
  const bk=d.by_kind||{}; const kinds=[['daemon',bk.daemon],['dispatch',bk.dispatch]];
  const kmax=Math.max(1,...kinds.map(k=>k[1]?(k[1].cost||0):0));
  morphInto(document.getElementById('dmKind'), kinds.map(([name,k])=>{
    k=k||{tasks:0,cost:0,per_task:0,avg_dur_s:0};
    const pct=Math.max(2,((k.cost||0)/kmax)*100).toFixed(1);
    const col=name==='dispatch'?'var(--warn)':'var(--accent)';
    return `<div class="bar-row" data-key="kind-${name}" style="grid-template-columns:90px 1fr auto"><span class="lbl"><span class="kind-tag ${name}">${name}</span></span>
      <span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:${col}"></span></span>
      <span class="num">${usd(k.cost)} <span style="color:var(--dim)">· ${k.tasks}任务 · ${usd(k.per_task)}/任务 · ${fmtDur(k.avg_dur_s)}</span></span></div>`;
  }).join(''));
  // Top 耗时任务(title 标注)
  const top=d.top_dur||[];
  morphInto(document.getElementById('dmTop'), top.length? top.map((x,i)=>{
    const secs=Math.round((x.dur_ms||0)/1000);
    const err=x.status==='error'?' <span style="color:var(--dead)">err</span>':'';
    return `<div class="bar-row" data-key="dt-${esc(x.id||(x.role+'-'+i))}" style="grid-template-columns:1fr auto"><span class="lbl" title="${esc(x.title||'(无标题)')}"><span class="kind-tag ${esc(x.kind)}">${esc(x.kind)}</span>${esc(x.role)} · ${esc(x.title||'(无标题)')}${err}</span>
      <span class="num">${fmtDur(secs)} · ${x.turns}t · ${usd(x.cost)}</span></div>`;
  }).join(''):'<div class="muted" style="padding:6px 16px">无数据</div>');
}
async function loadDispatch(force){
  try{ const r=await fetch('/api/dispatch_metrics'+(force?'?fresh=1':'')); renderDispatch(await r.json()); }
  catch(e){}
}

// ===== 能力池 caps =====
let _caps=null;
function renderCaps(d){
  _caps=d; if(!d) return;
  const s=d.summary||{};
  const meta=document.getElementById('capsMeta');
  if(meta) meta.textContent=(d.now||'')+' · '+(s.n_caps||0)+' 能力 / '+(s.n_active||0)+' 现行 agent / '+(s.n_empty||0)+' 待补'+(s.n_retired?(' / '+s.n_retired+' 退役'):'');
  // ⓪ Agent 详情 — 各 agent 会哪些内容(desc)+ caps + 状态
  const agCap=(c)=>'<span class="cap-chip">'+esc(c)+'</span>';
  const agCard=(a)=>{
    const fcls=esc(a.form_cls||'off');
    const form='<span class="ag-form '+fcls+'">'+esc(a.form||'?')+'</span>';
    const verdict=a.retired?'':('<span class="ag-verdict" title="'+esc(a.verdict||'')+'">'+esc(a.verdict||'')+'</span>');
    const succ=(a.retired&&a.succeeded_by)?('<span class="ag-succ">→ 接替:<b>'+esc(a.succeeded_by)+'</b></span>'):'';
    const caps=(a.caps&&a.caps.length)?('<div class="ag-caps">'+a.caps.map(agCap).join('')+'</div>'):'<div class="ag-caps"><span class="muted" style="font-size:11px">(无 caps)</span></div>';
    const desc=a.desc?esc(a.desc):'<span class="muted">（未填 desc / 职责）</span>';
    let metaLine='';
    if(a.retired){ metaLine='<div class="ag-meta">退役 '+esc(a.retired_at||'')+(a.reason?(' · '+esc(a.reason)):'')+'</div>'; }
    else if(a.registered){ metaLine='<div class="ag-meta">注册 '+esc(a.registered)+'</div>'; }
    return '<div class="ag-card '+fcls+'" data-key="ag-'+esc(a.role)+'">'+
      '<div class="ag-head"><span class="ag-name">'+esc(a.role)+'</span>'+form+succ+'</div>'+
      (verdict?('<div>'+verdict+'</div>'):'')+
      '<div class="ag-desc">'+desc+'</div>'+caps+metaLine+'</div>';
  };
  const agAll=(d.agents||[]).map(agCard).join('')+(d.agents_retired||[]).map(agCard).join('');
  morphInto(document.getElementById('agDetail'), agAll||'<div class="muted" style="padding:8px">（无 agent）</div>');
  // ① 能力池清单卡片
  const pool=(d.caps||[]).map(c=>{
    const rk=c.risk?('<span class="rb '+esc(c.risk_cls)+'">'+esc(c.risk)+'</span>'):'';
    const cls='cap-card'+(c.risk_cls?(' '+c.risk_cls):'')+(c.count===0?' zero':'');
    const hold=c.count?('<span class="muted" style="font-size:11px"> · '+c.holders.map(esc).join('、')+'</span>'):'<span class="muted" style="font-size:11px"> · 无持有者</span>';
    return '<div class="'+cls+'"><div class="cn">'+esc(c.name)+rk+'</div>'+
      '<div class="cd">'+esc(c.desc)+'</div>'+
      '<div class="cf">默认给:'+esc(c.default_for||'-')+'</div>'+
      '<div class="ch">持有 <b>'+c.count+'</b>'+hold+'</div></div>';
  }).join('');
  morphInto(document.getElementById('capPool'), pool);
  // ② caps 矩阵
  const order=d.cap_order||[];
  const riskCls={}; (d.caps||[]).forEach(c=>{ if(c.risk_cls) riskCls[c.name]=c.risk_cls; });
  let head='<thead><tr><th>agent ＼ cap</th>'+order.map(n=>'<th class="'+(riskCls[n]||'')+'" title="'+esc(n)+'">'+esc(n)+'</th>').join('')+'<th>计</th></tr></thead>';
  const rowHtml=(r,ret)=>{
    const set=new Set(r.caps||[]);
    const cells=order.map(n=>set.has(n)?'<td class="yes">✓</td>':'<td class="no">·</td>').join('');
    const rcls='mx-role'+(ret?' ret':'')+(r.empty?'':'');
    const tail=ret&&r.succeeded_by?'<span class="muted" style="font-size:10.5px"> →'+esc(r.succeeded_by)+'</span>':'';
    return '<tr><td><span class="'+rcls+'">'+esc(r.role)+'</span>'+tail+(r.empty?' <span class="rb edge" title="caps 为空,待补">待补</span>':'')+'</td>'+cells+'<td class="cnt">'+(r.caps||[]).length+'</td></tr>';
  };
  let body='<tbody>'+(d.matrix||[]).map(r=>rowHtml(r,false)).join('');
  body+=(d.retired||[]).map(r=>rowHtml({role:r.role,caps:r.caps,succeeded_by:r.succeeded_by,empty:false},true)).join('')+'</tbody>';
  morphInto(document.getElementById('capMatrix'), head+body);
  document.getElementById('capMxFoot').textContent='✓=持有该能力 · 现行 '+(d.matrix||[]).length+' 个 agent · 退役 '+(d.retired||[]).length+' 个(存档 caps,不计入分布)';
  // ③ 能力分布(条形)
  const dist=(d.caps||[]).slice().sort((a,b)=>b.count-a.count);
  const maxc=Math.max(1,...dist.map(c=>c.count));
  morphInto(document.getElementById('capDist'), dist.map(c=>{
    const pct=Math.round(c.count/maxc*100);
    const fill=c.count===0?'background:var(--dead)':'';
    return '<div class="bar-row"><div class="lbl mono" title="'+esc(c.name)+'">'+esc(c.name)+'</div>'+
      '<div class="bar-track"><div class="bar-fill" style="width:'+(c.count?pct:3)+'%;'+fill+'"></div></div>'+
      '<div class="num">'+c.count+' 人</div></div>';
  }).join(''));
  // ⑤ 高风险能力持有者
  morphInto(document.getElementById('capRisk'), (d.high_risk_view||[]).map(v=>{
    const has=v.holders&&v.holders.length;
    const who=has?v.holders.map(h=>'<span class="cap-chip '+(v.cls==='hi'?'dead':'warn')+'">'+esc(h)+'</span>').join(''):'<span class="muted">无持有者(无人握此闸)</span>';
    return '<div style="margin-bottom:10px"><div class="mono" style="font-size:12.5px"><span class="rb '+esc(v.cls)+'">'+esc(v.level)+'</span> '+esc(v.name)+'</div>'+
      '<div style="margin-top:5px">'+who+'</div></div>';
  }).join('')+'<div class="muted" style="font-size:11px;margin-top:4px">⚠ 持有者为空=能力闸当前无人可执行;要写库/外发或操作外部云文档须先补授权。</div>');
  // ④ 缺口高亮
  const g=d.gaps||{};
  let gh='';
  const ec=g.empty_caps_agents||[];
  gh+='<div class="gap-box '+(ec.length?'todo':'ok')+'"><div class="gh">(a) 空 caps 的在岗 agent — 待补'+(ec.length?(' ('+ec.length+')'):'(无)')+'</div>'+
      (ec.length?ec.map(r=>'<span class="cap-chip warn">'+esc(r)+'</span>').join(''):'<span class="muted">现行 agent 均已配 caps ✓</span>')+
      (ec.length?'<div class="muted" style="font-size:11px;margin-top:4px">这些角色在册在岗但 agents.json 无 caps,培训师 onboarding 时按其专长补最小授权。</div>':'')+'</div>';
  const nh=g.no_holder_caps||[];
  gh+='<div class="gap-box '+(nh.length?'none':'ok')+'"><div class="gh">(b) 无人持有的能力 — 无持有者'+(nh.length?(' ('+nh.length+')'):'(无)')+'</div>'+
      (nh.length?nh.map(n=>'<span class="cap-chip dead">'+esc(n)+'</span>').join(''):'<span class="muted">每个能力都至少有一个持有者 ✓</span>')+'</div>';
  const tr=g.test_roles||[];
  gh+='<div class="gap-box"><div class="gh">(c) 已过滤的测试角色 ('+tr.length+')</div>'+
      (tr.length?tr.map(r=>'<span class="cap-chip">'+esc(r)+'</span>').join(''):'<span class="muted">无</span>')+
      '<div class="muted" style="font-size:11px;margin-top:4px">zz* / __* / ? 测试角色不计入分布与缺口(_is_test_role 过滤)。</div></div>';
  morphInto(document.getElementById('capGaps'), gh);
}
async function loadCaps(){
  try{ const r=await fetch('/api/caps'); renderCaps(await r.json()); }catch(e){}
}
document.getElementById('capsRefresh').onclick=()=>loadCaps();

// ===== 🧠 上下文窗口占用 · 1M 撞墙预警 =====
let _ctx=null;
function fmtWin(w){ return w>=1000000 ? (w/1000000)+'M' : (w/1000)+'K'; }
function marginCell(r){
  if(r.margin==null||r.compact_at==null) return '<span class="muted">—</span>';
  if(r.margin>0) return '<span class="ctxpct '+(r.level==='yellow'?'yellow':'green')+'">+'+fmtN(r.margin)+'</span> <span class="muted" style="font-size:11px">tok 余量</span>';
  return '<span class="ctxpct red">已超 '+fmtN(-r.margin)+'</span> <span class="muted" style="font-size:11px">tok·将被压</span>';
}
function renderCtx(){
  const d=_ctx; if(!d) return;
  const meta=document.getElementById('ctxMeta'); const kp=document.getElementById('ctxKpis');
  const body=document.getElementById('ctxBody'); const foot=document.getElementById('ctxFoot');
  if(!body) return;
  if(d.loading){ meta.textContent='后台扫描中…'; morphInto(body,'<tr><td colspan="6" class="muted">加载中（后台 jsonl 尾读）…</td></tr>'); return; }
  meta.textContent='活跃 '+d.n+' · 精确绑定 '+(d.n_bound||0)+'/'+d.n+' · 最近 '+d.active_min+'min · '+esc(d.as_of||'');
  kp.innerHTML='<div class="k"><div class="v ctxpct '+(d.max_pct>=d.red?'red':d.max_pct>=d.yellow?'yellow':'green')+'">'+d.max_pct+'%</div><div class="l">峰值占用</div></div>'
    +'<div class="k"><div class="v ctxpct '+(d.n_red>0?'red':'green')+'">'+d.n_red+'</div><div class="l">≥65% 将被自动压</div></div>'
    +'<div class="k"><div class="v ctxpct '+(d.n_yellow>0?'yellow':'green')+'">'+d.n_yellow+'</div><div class="l">≥50% 注意</div></div>'
    +'<div class="k"><div class="v">'+d.n+'</div><div class="l">活跃会话</div></div>';
  if(!d.rows||!d.rows.length){ morphInto(body,'<tr><td colspan="6" class="muted">（最近 '+d.active_min+' 分钟无活跃会话）</td></tr>'); foot.textContent=''; return; }
  morphInto(body, d.rows.map(r=>{
    const barW=Math.max(2,Math.min(100,r.pct))*0.6;
    let who;
    if(r.role) who='<b>'+esc(r.role)+'</b> <span style="color:var(--live)" title="session_id 精确绑定(who 暴露的 presence.session_id ⨝ 会话 jsonl id)">✓</span>';
    else if(!r.is_agent) who='<span class="muted" title="'+esc(r.proj||'')+'">'+esc(r.proj?projShort(r.proj):'个人会话')+'</span>';
    else who='<span class="winassumed" title="who 无匹配 session_id:daemon(无此 env)/ 尚未 re-hello / 非注册会话 — 不猜">未绑定</span>';
    return '<tr class="'+(r.level==='red'?'ctx-red':'')+'" data-key="cx-'+esc(r.sid)+'">'
      +'<td><span class="ctxpct '+r.level+'">'+r.pct+'%</span><span class="ctxbar '+r.level+'" style="width:'+barW+'px"></span></td>'
      +'<td>'+marginCell(r)+'</td>'
      +'<td class="muted mono" style="font-size:11px">'+fmtN(r.ctx)+' / '+fmtWin(r.win)+(r.win_known?'':'·假定')+'</td>'
      +'<td>'+who+'</td>'
      +'<td class="mono" title="'+esc(r.model)+'">'+esc((r.model||'?').replace('claude-',''))+'</td>'
      +'<td class="muted mono">'+esc(r.ts||'')+'</td></tr>';
  }).join(''));
  foot.textContent='占用% =(input+cache_read+cache_creation)/模型窗口;自动压阈=窗口×65%(实测 auto-compact 真阈),余量=阈−ctx(<0=已过线将被压)。role✓=session_id 精确绑定(who 暴露 presence.session_id ⨝ 会话 jsonl id,MasterAgent 全解;不再 regex 猜);未绑定=daemon(无此 env)/尚未 re-hello/非注册会话,不猜。占用逐会话精确。窗口:opus-4*/sonnet-4=1M、haiku/未知=200K(假定标注)。所有会话如实显 proj(自看控制台默认不脱敏,能看清哪个个人会话在吃上下文/逼近压线);liveness/注册看 🛰 运维面板。';
}
async function loadCtx(){
  try{ const r=await fetch('/api/ctxpanel'); _ctx=await r.json(); }catch(e){ /* 保留上次 */ }
  renderCtx(); renderCtxBadge();
}
function renderCtxBadge(){
  const b=document.getElementById('ctxBadge'); if(!b) return; const d=_ctx;
  if(!d || d.loading){ b.className='hbadge loading'; b.textContent='🧠 …'; return; }
  if(d.n_red>0){ b.className='hbadge danger'; b.textContent='🧠 ⚠ '+d.n_red+' 将压 '+d.max_pct+'%'; b.title=d.n_red+' 个会话 ≥65%(峰值 '+d.max_pct+'%)— 将被 auto-compact,建议先主动落档重置'; }
  else if(d.n_yellow>0){ b.className='hbadge warn'; b.textContent='🧠 '+d.max_pct+'%'; b.title='峰值占用 '+d.max_pct+'%(有 ≥50% 会话)'; }
  else{ b.className='hbadge ok'; b.textContent='🧠 '+d.max_pct+'%'; b.title='峰值占用 '+d.max_pct+'% — 全部安全'; }
}
document.getElementById('ctxRefresh').onclick=()=>loadCtx();
document.getElementById('ctxBadge').onclick=()=>showTab('ctx');

// ===== 📝 提示词审核(只读·手动刷新)=====
let _promptsLoaded=false;
function renderPrompts(d){
  const box=document.getElementById('promptsBox'); const meta=document.getElementById('promptsMeta');
  if(!box) return;
  if(!d||!d.ok){ box.innerHTML='<div class="muted">读取失败:'+esc((d&&d.error)||'?')+'</div>'; return; }
  meta.textContent='共 '+d.sections.length+' 节 · 读取于 '+esc(d.as_of||'');
  box.innerHTML=d.sections.map((s,i)=>{
    const head='<div class="pmt-h" onclick="togglePrompt('+i+')"><span class="pmt-t">'+esc(s.title)+'</span>'
      +'<span class="pmt-meta mono">'+esc(s.path||'')+(s.mtime?(' · '+esc(s.mtime)):'')
      +(s.bytes!=null?(' · '+fmtN(s.bytes)+'B/'+ (s.lines||0) +'行'):'')+'</span></div>';
    const body = s.error
      ? '<div class="pmt-b muted" id="pmt-'+i+'" style="display:none">⚠ '+esc(s.error)+'</div>'
      : '<pre class="pmt-b" id="pmt-'+i+'" style="display:none">'+esc(s.content||'')+'</pre>';
    return '<div class="pmt-sec'+(s.error?' err':'')+'">'+head+body+'</div>';
  }).join('')||'<div class="muted">无内容</div>';
}
function togglePrompt(i){ const el=document.getElementById('pmt-'+i); if(el) el.style.display = el.style.display==='none'?'block':'none'; }
async function loadPrompts(){
  const box=document.getElementById('promptsBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted">读取中…</div>';
  try{ const r=await fetch('/api/prompts'); renderPrompts(await r.json()); _promptsLoaded=true; }
  catch(e){ if(box) box.innerHTML='<div class="muted">读取失败:'+esc(e)+'</div>'; }
}
document.getElementById('promptsRefresh').onclick=()=>loadPrompts();

// ===== 可恢复会话 =====
let _sessData=null, _sessFilter='all', _sessOwner='all';
function renderSessions(){
  const box=document.getElementById('sessBox'); if(!_sessData) return;
  if(!_sessData.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc(_sessData.error||'?')+'</div>'); return; }
  let list=_sessData.sessions.slice();
  // 归属筛选(is_bus 字段:bus agent会话 / 个人会话)
  if(_sessOwner==='bus')    list=list.filter(s=>s.is_bus);
  else if(_sessOwner==='nonbus') list=list.filter(s=>!s.is_bus);
  // 状态/标记筛选(--json 已按 运行中置顶再最近 排序,保持原序)· 与归属可组合
  if(_sessFilter==='live')   list=list.filter(s=>s.state==='运行中');
  else if(_sessFilter==='dead')   list=list.filter(s=>s.state==='死');
  else if(_sessFilter==='tagged') list=list.filter(s=>s.tags&&s.tags.length);
  const oname={all:'全部',bus:'bus',nonbus:'个人'}[_sessOwner]||'全部';
  const fname={all:'全部',live:'运行中',dead:'死',tagged:'已标记'}[_sessFilter]||'全部';
  document.getElementById('sessCount').textContent=
    '显示 '+list.length+' 条(归属:'+oname+' · 状态:'+fname+')· bus '+(_sessData.bus_count||0)
    +' / 个人 '+(_sessData.nonbus_count||0)+' · ▶运行中 '+(_sessData.live_count||0)
    +' / ·死 '+(_sessData.dead_count||0)+' / 🏷已标记 '+(_sessData.tagged_count||0)
    +' · 共 '+_sessData.total;
  if(!list.length){ morphInto(box,'<div class="muted" style="padding:8px 16px">（当前筛选无会话）</div>'); return; }
  morphInto(box, list.map(s=>{
    const live = s.state==='运行中';
    const st = live
      ? `<span class="sess-state live">▶ 运行中<span class="role">·${esc(s.live_role||'?')}</span></span>`
      : `<span class="sess-state dead">· 死</span>`;
    const own = s.is_bus
      ? `<span class="own-chip bus">bus</span>`
      : `<span class="own-chip personal">个人</span>`;
    const tags = (s.tags&&s.tags.length)
      ? `<span class="sess-tags">`+s.tags.map(t=>`<span class="tag-chip">🏷${esc(t)}</span>`).join('')+`</span>` : '';
    // 死的才给 resume;运行中的它在跑,不给(点了会撞正在跑的会话)
    const act = live
      ? `<span class="badge live" title="该会话正在运行,勿 resume">在跑</span>`
      : `<button class="btn mini copy" onclick="copyResume('${esc(s.id)}',this)">复制 resume</button>`;
    return `<div class="sess-row ${live?'live':'dead'} ${s.is_agent?'agent':''}" data-key="ss-${esc(s.id)}">
      <div class="sess-main">
        <div class="sess-title" title="${esc(s.title)}">${st} ${own} ${esc(s.title||'(无标题)')} ${tags}</div>
        <div class="sess-meta">
          <span title="${esc(s.cwd)}">${esc(projShort(s.project||s.cwd))}</span>
          <span>最近 ${esc(s.last)}</span>
          ${s.size_kb!=null?`<span>${esc(''+s.size_kb)}KB</span>`:''}
          ${s.archived?'<span title="jsonl 已移出 projects/">📦已归档</span>':''}
          ${s.note?`<span class="dnote" title="${esc(s.note)}">📝${esc(s.note)}</span>`:''}
          <span class="mono">${esc((s.id||'').slice(0,8))}…</span>
        </div>
      </div>
      ${act}
    </div>`;
  }).join(''));
}
function setSessFilter(f){
  _sessFilter=f;
  document.querySelectorAll('.sess-filt').forEach(b=>b.classList.toggle('active', b.getAttribute('data-f')===f));
  renderSessions();
}
function setSessOwner(o){
  _sessOwner=o;
  document.querySelectorAll('.sess-own').forEach(b=>b.classList.toggle('active', b.getAttribute('data-o')===o));
  renderSessions();
}
function copyResume(id,btn){
  const cmd='claude --resume '+id;
  const done=()=>{const o=btn.textContent;btn.textContent='已复制✓';setTimeout(()=>btn.textContent=o,1500);toast('已复制:'+cmd);};
  if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(cmd).then(done,()=>fallback()); }
  else fallback();
  function fallback(){const ta=document.createElement('textarea');ta.value=cmd;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');done();}catch(e){toast('复制失败,手动复制:'+cmd,true);}document.body.removeChild(ta);}
}
async function loadSessions(){
  const box=document.getElementById('sessBox'); if(!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">加载中…</div>';
  try{ const r=await fetch('/api/sessions'); _sessData=await r.json(); renderSessions(); }
  catch(e){ box.innerHTML='<div class="muted" style="padding:8px 16px">加载失败:'+esc(e)+'</div>'; }
}
document.getElementById('usageRefresh').onclick=()=>loadUsage(true);
document.getElementById('sessRefresh').onclick=()=>loadSessions();
{const _tlr=document.getElementById('tlRefresh'); if(_tlr) _tlr.onclick=()=>loadTasklines();}
document.querySelectorAll('.sess-filt').forEach(b=>b.onclick=()=>setSessFilter(b.getAttribute('data-f')));
document.querySelectorAll('.sess-own').forEach(b=>b.onclick=()=>setSessOwner(b.getAttribute('data-o')));
{const _gr=document.getElementById('grpRefresh'); if(_gr) _gr.onclick=()=>loadGroups();}

// ===== 🧩 工作组看板(使用方出口径·MasterUI出可视化)=====
async function loadGroups(){
  const box=document.getElementById('grpBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/groups'); renderGroups(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderGroups(d){
  const box=document.getElementById('grpBox'); const meta=document.getElementById('grpMeta'); if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc((d&&d.error)||'?')+'</div>'); if(meta)meta.textContent=''; return; }
  const gs=d.groups||[];
  if(meta) meta.textContent=(d.count||0)+' 个组 · 读取于 '+esc(d.as_of||'');
  if(!gs.length){ morphInto(box,'<div class="muted" style="padding:8px 16px">（暂无工作组·_groups/ 空)</div>'); return; }
  morphInto(box, gs.map(g=>{
    const exp=(g.target!=null)?('/'+esc(''+g.target)):'';
    const actCls=(g.target!=null && g.n_joined<g.target)?'warn':'live';
    const act='<span class="cap-chip '+actCls+'" title="当前在组成员数'+(g.target!=null?'/软目标target_size':'·成员运行时动态join无固定分母')+'">'+esc(''+g.n_joined)+exp+' 成员在组</span>';
    const ibCls=g.inbox_depth>0?'warn':'';
    const ib='<span class="cap-chip '+ibCls+'" title="组inbox待领深度=积压">组inbox '+esc(''+g.inbox_depth)+'</span>';
    const caps=(g.caps&&g.caps.length)?('<span style="margin-left:2px">'+g.caps.map(c=>'<span class="cap-chip">'+esc(c)+'</span>').join('')+'</span>'):'';
    const mem=(g.members&&g.members.length)?g.members.map(m=>'<span class="cap-chip">'+esc(m)+'</span>').join(''):'<span class="muted" style="font-size:11px">(无成员·未join)</span>';
    // ③④⑤ 负载/并行(使用方口径 group_metrics)
    let metricsHtml;
    const m=g.metrics;
    if(!m){ metricsHtml='<div class="muted" style="font-size:11.5px;padding:4px 0">③④⑤ 负载/并行口径未接入(group_metrics import 失败或无数据)</div>'; }
    else{
      const ld=m.members_load||[];
      const maxsh=Math.max(1,...ld.map(x=>x.share_pct||0));
      const bars=ld.length? ld.map(x=>{
        const w=Math.max(3,Math.round((x.share_pct||0)/maxsh*100));
        return '<div class="bar-row"><div class="lbl" title="'+esc(x.role)+'">'+esc(x.role)+'</div>'
          +'<div class="bar-track"><div class="bar-fill" style="width:'+w+'%"></div></div>'
          +'<div class="num">'+esc(''+x.share_pct)+'% · '+esc(''+x.claims)+'活/'+Math.round(x.busy_s/60)+'m</div></div>';
      }).join('') : '<div class="muted" style="font-size:11px">窗口内无 send_group 组路由(成员活动走直发·见舰队面板)</div>';
      const ep=m.effective_parallelism, ipr=m.intra_parallel_rate;
      const noAct=!(m.tasks_in_window>0);
      const epHtml= noAct ? '<b class="mono muted" title="窗口内无组任务·无并行可算">N/A</b>' : '<b class="mono" style="color:var(--'+(ep>=2?'live':ep>1?'warn':'dim')+')">'+esc(''+ep)+'×</b>';
      const iprHtml= noAct ? '<b class="mono muted" title="窗口内无组任务·无并行可算">N/A</b>' : '<b class="mono" style="color:var(--'+(ipr>=40?'live':ipr>0?'warn':'dim')+')">'+esc(''+ipr)+'%</b>';
      const kpis='<div style="display:flex;gap:16px;flex-wrap:wrap;margin:6px 0">'
        +'<span title="Σ成员busy/≥1busy墙钟;=1串行,→成员数理想">'+epHtml+' <span class="muted" style="font-size:11px">有效并行系数</span></span>'
        +'<span title="≥2成员同时busy占≥1busy时长">'+iprHtml+' <span class="muted" style="font-size:11px">组内并行率</span></span>'
        +'<span class="muted" style="font-size:11px">窗口内组任务 '+esc(''+(m.tasks_in_window||0))+'(done '+esc(''+(m.done_in_window||0))+')· 活跃墙钟 '+Math.round((m.active_wall_s||0)/60)+'m</span></div>';
      metricsHtml=kpis+'<div class="bars">'+bars+'</div>';
    }
    return '<div class="tl-card" data-key="grp-'+esc(g.group)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:var(--accent)">🧩 '+esc(g.group)+'</b>'+act+ib
      +'<span class="spacer" style="flex:1"></span><span class="muted" style="font-size:11px" title="成员名单最后变更时间(members.json·非数据新鲜度)">成员更新于 '+esc(g.updated||'')+'</span></div>'
      +'<div style="margin-top:6px;font-size:12px"><b class="muted">成员:</b> '+mem+' <b class="muted" style="margin-left:6px">caps:</b> '+caps+'</div>'
      +'<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">③负载分布 / ④有效并行 / ⑤组内并行率(使用方口径·仅组任务·24h窗):</b>'+metricsHtml+'</div>'
      +'</div>';
  }).join(''));
}

// ===== 🔧 机制注册表(使用方出数据·MasterUI render;机制≠能力池·related_cap交叉链跳能力池)=====
{const _mr=document.getElementById('mechRefresh'); if(_mr) _mr.onclick=()=>loadMechanisms();}
const _MECH_STCLS={'在用':'idle','新上':'live','计划':'warn','退役':'none'};
async function loadMechanisms(){
  const box=document.getElementById('mechBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/mechanisms'); renderMechanisms(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderMechanisms(d){
  const box=document.getElementById('mechBox'); const meta=document.getElementById('mechMeta'); if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc((d&&d.error)||'?')+'</div>'); if(meta)meta.textContent=''; return; }
  const cats=d.categories||[], m=d.meta||{};
  if(meta) meta.textContent=(d.n_mech||0)+' 机制 / '+(d.n_cats||0)+' 类 · 读取于 '+esc(d.as_of||'');
  // 顶部:purpose + 机制≠caps 区分 + 状态图例
  const leg=m.status_legend||{};
  const legHtml=Object.keys(leg).map(k=>'<span class="cap-chip '+(_MECH_STCLS[k]||'')+'" title="'+esc(leg[k])+'">'+esc(k)+'</span>').join('');
  let head='<div style="padding:2px 16px 8px">'
    +(m.purpose?'<div class="muted" style="font-size:12px">'+esc(m.purpose)+'</div>':'')
    +(m.distinction_vs_caps?'<div style="font-size:11.5px;margin-top:4px;background:rgba(91,157,255,.06);border-left:2px solid var(--accent);padding:4px 8px;border-radius:4px">🔧机制 vs 🧬caps:'+esc(m.distinction_vs_caps)+'</div>':'')
    +'<div style="margin-top:6px">状态图例:'+legHtml+' <span class="muted" style="font-size:11px">· 点 related_cap 标签跳🧬能力池</span></div></div>';
  morphInto(box, head+cats.map(c=>{
    const relCap=c.related_cap||'';
    const capChip=(relCap && relCap.charAt(0)!=='(')
      ? '<span class="cap-chip" style="cursor:pointer" title="跳能力池看此cap" onclick="event.stopPropagation();showTab(\'caps\')">🧬'+esc(relCap)+'</span>'
      : '<span class="muted" style="font-size:11px">'+esc(relCap)+'</span>';
    const sd=c.status_dist||{};
    const sdChips=Object.keys(sd).map(k=>'<span class="cap-chip '+(_MECH_STCLS[k]||'')+'" style="font-size:10px">'+esc(k)+' '+sd[k]+'</span>').join('');
    const rows=(c.mechanisms||[]).map(x=>{
      const st='<span class="cap-chip '+(_MECH_STCLS[x.status]||'')+'" style="font-size:10px">'+esc(x.status||'?')+'</span>';
      return '<tr><td style="font-weight:600">'+esc(x.name)+' '+st+'</td>'
        +'<td style="font-size:12px">'+esc(x.does||'')+'</td>'
        +'<td class="mono muted" style="font-size:11px">'+esc(x.carrier||'')+'</td></tr>';
    }).join('');
    return '<div class="tl-card" data-key="mech-'+esc(''+c.id)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:0">'
      +'<div style="cursor:pointer;display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 14px" onclick="var b=this.parentNode.querySelector(\'.mech-body\');b.style.display=b.style.display===\'none\'?\'block\':\'none\';">'
      +'<b>'+esc(''+c.id)+'. '+esc(c.name)+'</b><span class="cap-chip" style="background:rgba(91,157,255,.14);color:var(--accent)">'+c.count+' 机制</span>'+capChip
      +'<span class="spacer" style="flex:1"></span>'+sdChips+'<span class="muted" style="font-size:11px">▸展开</span></div>'
      +'<div class="mech-body" style="display:none;padding:0 14px 10px;overflow:auto">'
      +'<table style="width:100%"><thead><tr><th>机制</th><th>作用</th><th>载体</th></tr></thead><tbody>'+rows+'</tbody></table></div>'
      +'</div>';
  }).join(''));
}

// ===== 🗂️ 长程任务·锚定生长网(P0:脊柱+当前段+进度·使用方简报/MasterAgent数据/MasterUI render)=====
{const _lr=document.getElementById('lrRefresh'); if(_lr) _lr.onclick=()=>loadLongrange();}
// 节点状态→CSS(MasterAgent longrange_v0 口径:pending灰/ready浅蓝/running蓝/blocked红/await_decision黄/done绿/failed深红)
const _LR_NODE={pending:'idle',ready:'accent',running:'accent',blocked:'dead',await_decision:'warn',done:'live',failed:'dead'};
// 锚点状态→CSS(upcoming灰/active蓝/reached绿)
const _LR_ANCH={upcoming:'idle',active:'accent',reached:'live'};
async function loadLongrange(){
  const box=document.getElementById('lrBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/longrange'); renderLongrange(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
// P1 ③ 活跃段 fork/merge DAG:按拓扑深度分层成列,标注分叉(出度>1)/合并(入度>1)节点。
//   edges=[{from,to}](节点 id);无边→退回扁平一列(即 P0 行为)。纯 JS 计算,不依赖 MasterAgent 预排版。
function _lrLayout(nodes,edges){
  const byId={}; nodes.forEach(n=>byId[n.id]=n);
  const indeg={}, outdeg={}, succ={}, pred={};
  nodes.forEach(n=>{indeg[n.id]=0;outdeg[n.id]=0;succ[n.id]=[];pred[n.id]=[];});
  (edges||[]).forEach(e=>{ if(byId[e.from]&&byId[e.to]){ outdeg[e.from]++; indeg[e.to]++; succ[e.from].push(e.to); pred[e.to].push(e.from);} });
  // 最长路径深度(拓扑)——环保护:迭代上限
  const depth={}; nodes.forEach(n=>depth[n.id]=0);
  for(let it=0;it<nodes.length+2;it++){ let ch=false;
    (edges||[]).forEach(e=>{ if(byId[e.from]&&byId[e.to]&&depth[e.to]<depth[e.from]+1){depth[e.to]=depth[e.from]+1;ch=true;} });
    if(!ch) break; }
  const cols={}; nodes.forEach(n=>{const dcol=depth[n.id]||0;(cols[dcol]=cols[dcol]||[]).push(n);});
  return {cols,indeg,outdeg,maxDepth:Math.max(0,...nodes.map(n=>depth[n.id]||0))};
}
// P1 ④ 复杂度成长曲线:series=[{t,size}] → 内联 SVG sparkline(净规模随时间)。无 series→null。
function _lrSpark(series){
  if(!series||series.length<2) return '';
  const W=180,H=30,pad=2; const xs=series.map((_,i)=>i), ys=series.map(p=>+p.size||0);
  const ymin=Math.min(...ys), ymax=Math.max(...ys), yr=(ymax-ymin)||1, xr=(xs.length-1)||1;
  const pts=series.map((p,i)=>(pad+i/xr*(W-2*pad)).toFixed(1)+','+(H-pad-((+p.size||0)-ymin)/yr*(H-2*pad)).toFixed(1)).join(' ');
  const last=ys[ys.length-1], first=ys[0], up=last>=first;
  return '<svg width="'+W+'" height="'+H+'" style="vertical-align:middle"><polyline points="'+pts+'" fill="none" stroke="'+(up?'#5b9dff':'#f5a623')+'" stroke-width="1.5"/></svg>'
    +'<span class="mono" style="font-size:11px;margin-left:6px">'+first+'→'+last+' ('+(up?'+':'')+(last-first)+')</span>';
}
// ★P1 决策合批·交互写回(MasterAgent 口径:approve→ready执行 / reject→failed / 未答默认reject)。
window._lrDec=window._lrDec||{};
function lrPick(tid,node,act){
  _lrDec[tid+'::'+node]=act;
  document.querySelectorAll('button[data-dec="'+CSS.escape(tid+'::'+node)+'"]').forEach(b=>{
    const on=b.dataset.act===act;
    b.style.background=on?(act==='approve'?'rgba(62,207,142,.28)':'rgba(255,93,93,.28)'):'transparent';
    b.style.fontWeight=on?'700':'400';
  });
  const c=document.getElementById('lrsub-'+CSS.escape(tid)); if(c){ const ns=JSON.parse(c.dataset.nodes||'[]'); c.textContent='✅ 一次批完('+ns.filter(n=>_lrDec[tid+'::'+n]).length+'/'+ns.length+' 已选·未选按reject)'; }
}
async function lrSubmit(tid){
  const c=document.getElementById('lrsub-'+CSS.escape(tid)); const nodes=JSON.parse((c&&c.dataset.nodes)||'[]');
  const ap={}; nodes.forEach(n=>{ ap[n]=_lrDec[tid+'::'+n]||'reject'; });
  const nAns=nodes.filter(n=>_lrDec[tid+'::'+n]).length;
  if(!window.confirm('提交本批 '+nodes.length+' 条裁决?\n已选 '+nAns+' 条,未选的按 reject 处理(保守·宁卡不误做不可逆)。\napprove→节点交执行(可能含不可逆DDL),reject→标 failed。')) return;
  if(c){ c.disabled=true; c.textContent='提交中…'; }
  try{
    const r=await fetch('/api/lr-decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:tid,approvals:ap})});
    const j=await r.json();
    if(!j.ok){ alert('写回失败:'+esc(j.error||'?')); if(c){c.disabled=false;} return; }
    nodes.forEach(n=>delete _lrDec[tid+'::'+n]); loadLongrange();
  }catch(e){ alert('写回异常:'+esc(''+e)); if(c){c.disabled=false;} }
}
function renderLongrange(d){
  const box=document.getElementById('lrBox'); const meta=document.getElementById('lrMeta'); if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc((d&&d.error)||'?')+'</div>'); if(meta)meta.textContent=''; return; }
  if(d.pending){ if(meta)meta.textContent='P0·待数据'; morphInto(box,'<div class="muted" style="padding:10px 16px">🗂️ P0 骨架已就位。'+esc(d.note||'')+'</div>'); return; }
  const ts=d.tasks||[];
  if(meta) meta.textContent=(d.count||0)+' 条 · schema '+esc(d.schema||'')+' · 更新 '+esc(d.updated||'');
  if(!ts.length){ morphInto(box,'<div class="muted" style="padding:8px 16px">（暂无长程任务)</div>'); return; }
  // ★P1 决策合批单(置顶·跨任务聚合):当前待人拍板的点聚成一张单,一处看。空=当前无待拍(诚实)。
  const pd=d.pending_decisions||[]; const nb=(d.decision_batches||[]).length;
  const _AUTt={auto:'<span style="color:var(--live)">●auto</span>',notify:'<span style="color:var(--accent)">●notify</span>',ask:'<span style="color:var(--warn)">🔒ask</span>'};
  const _PCt=pc=>{if(!pc||!pc.status)return '';const m={pass:'✓预检pass',pending:'…预检pending',fail:'⚠预检fail'};return ' <span class="muted" style="font-size:10px" title="'+((pc.findings&&pc.findings.length)?esc(pc.findings.join(' / ')):'')+'">'+(m[pc.status]||esc(pc.status))+((pc.status==='fail'&&pc.findings&&pc.findings.length)?(':'+esc(pc.findings[0])):'')+'</span>';};
  const decSrc=nb?('MasterAgent 权威 pending_decision_batch × '+nb+' 单'):(d.decisions_derived?'MasterUI 派生(锚闸active+await_decision)':'MasterAgent/使用方 emit');
  const nbatch=d.decision_batches||[];
  // 决策行(带交互按钮 or 只读):inter=true 时接 lrPick/lrSubmit 写回;false=派生只读
  const decRow=(p,inter,tid,node)=>'<div class="lr-node warn" style="white-space:normal;display:block;padding:6px 10px">'
    +'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>'+(p.kind==='anchor-gate'?'⚑里程碑闸':'●节点')+'</b> '+esc(p.label||'')
    +(p.autonomy?(' '+(_AUTt[p.autonomy]||esc(p.autonomy))):'')+_PCt(p.precheck)
    +(p.task_title?(' <span class="muted" style="font-size:10px">@ '+esc(p.task_title)+'</span>'):'')+(p.owner?(' <span class="muted" style="font-size:10px">owner '+esc(p.owner)+'</span>'):'')
    +'<span class="spacer" style="flex:1"></span>'
    +(inter?(
        '<button data-dec="'+esc(tid+'::'+node)+'" data-act="approve" onclick="lrPick(\''+esc(tid)+'\',\''+esc(node)+'\',\'approve\')" title="批准→节点交执行" style="font-size:10px;padding:1px 8px;border:1px solid var(--live);color:var(--live);background:transparent;border-radius:4px;cursor:pointer">✓批</button>'
       +'<button data-dec="'+esc(tid+'::'+node)+'" data-act="reject" onclick="lrPick(\''+esc(tid)+'\',\''+esc(node)+'\',\'reject\')" title="驳回→标 failed" style="font-size:10px;padding:1px 8px;border:1px solid var(--dead);color:var(--dead);background:transparent;border-radius:4px;cursor:pointer">✗驳</button>'
      ):'<span class="muted" style="font-size:9px">(派生·只读·无batch写回)</span>')
    +'</div>'+(p.question?('<div class="muted" style="font-size:11px;margin-top:2px">验收:'+esc(p.question)+'</div>'):'')+'</div>';
  let decBody;
  if(nbatch.length){
    // MasterAgent 权威:每任务一张 pending_decision_batch → 交互写回(一次批完)
    decBody=nbatch.map(b=>{
      const ds=b.decisions||[]; const nodeIds=ds.map(dc=>dc.node||dc.label||'');
      return '<div style="margin-top:8px;border-top:1px dashed var(--line);padding-top:6px">'
        +'<div style="font-size:11px" class="muted">📋 <b>'+esc(b.task_title||b.task_id||'')+'</b> · batch '+esc(b.batch_id||'')+(b.segment!=null?(' · 段'+esc(''+b.segment)):'')+'</div>'
        +'<div style="margin-top:6px;display:flex;flex-direction:column;gap:6px">'+ds.map(dc=>decRow(dc,true,b.task_id,dc.node||dc.label||'')).join('')+'</div>'
        +'<button id="lrsub-'+esc(b.task_id)+'" data-nodes=\''+esc(JSON.stringify(nodeIds))+'\' onclick="lrSubmit(\''+esc(b.task_id)+'\')" style="margin-top:8px;font-size:11px;padding:4px 14px;border:1px solid var(--warn);color:var(--warn);background:rgba(245,166,35,.1);border-radius:5px;cursor:pointer;font-weight:700">✅ 一次批完(0/'+ds.length+' 已选·未选按reject)</button>'
        +'</div>';
    }).join('');
  } else if(pd.length){
    decBody='<div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">'+pd.map(p=>decRow(p,false)).join('')+'</div>';
  } else {
    decBody='<div class="muted" style="font-size:11.5px;margin-top:6px">当前无待拍板点 —— 无 pending_decision_batch、无 active 里程碑闸/await_decision 节点。(空=诚实,不是漏读)</div>';
  }
  const decCard='<div class="tl-card" data-key="lr-decisions" style="border:1px solid '+(pd.length?'var(--warn)':'var(--line)')+';border-radius:8px;margin:8px 16px;padding:10px 14px;'+(pd.length?'background:rgba(245,166,35,.06)':'')+'">'
    +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:'+(pd.length?'var(--warn)':'var(--dim)')+'">⚖ 决策合批单</b>'
    +'<span class="cap-chip '+(pd.length?'warn':'idle')+'" style="font-size:10px">'+pd.length+' 待拍</span>'
    +'<span class="muted" style="font-size:10px">'+decSrc+' · 一处看一次批不零售'+(nbatch.length?' · approve→执行/reject→failed/未答默认reject(保守)':' · 派生视图·拍板走总线')+'</span></div>'
    +decBody
    +'</div>';
  morphInto(box, decCard + ts.map(t=>{
    // ① 锚点脊柱:起→中…→终;author(👤user实心/🤖machine虚框)× status色(upcoming灰/active蓝/reached绿)
    const sp=(t.spine||[]).map((a,i)=>{
      const isU=a.author==='user';
      const stc=_LR_ANCH[a.status]||'idle';
      const gate=a.decision_gate?' <span title="决策闸·人拍板">⚖</span>':'';
      const tp={start:'起',mid:'中',end:'终'}[a.type]||'·';
      const stTag=a.status?('<span class="lr-node '+stc+'" style="font-size:9px;padding:0 5px">'+esc(a.status)+'</span>'):'';
      return (i>0?'<span class="lr-arrow">→</span>':'')
        +'<span class="lr-anchor '+(isU?'u':'m')+'" title="'+esc(a.author||'')+' 锚点'+(a.acceptance?(' · 验收:'+esc(a.acceptance)):'')+'">'
        +'<span><b>'+tp+'</b> '+esc(a.anchor||'')+gate+' '+stTag+'</span><span class="lr-au">'+(isU?'👤user':'🤖machine')+'</span></span>';
    }).join('');
    // ② 当前活跃段:节点状态色 + blocked-on + verdict + author + fork/merge 标注
    const seg=t.active_segment||{};
    const segNodes=seg.nodes||[]; const lay=_lrLayout(segNodes,seg.edges);
    const nodeHtml=n=>{
      const cls=_LR_NODE[n.status]||'idle';
      const bo=(n.blocked_on&&n.blocked_on.length)?' <span class="muted" style="font-size:10px">⛔'+n.blocked_on.map(esc).join(','):'';
      const bc=(n.blocked_on&&n.blocked_on.length)?'</span>':'';
      const vd=n.verdict?' <span title="验收结论" style="font-size:10px">✓'+esc(n.verdict)+'</span>':'';
      const au=n.author==='machine'?' 🤖':'';
      const fk=(lay.outdeg[n.id]>1)?' <span title="分叉 fork" style="font-size:10px">⑃</span>':'';
      const mg=(lay.indeg[n.id]>1)?' <span title="合并 merge" style="font-size:10px">⑂</span>':'';
      // P1 自主度徽标:auto●绿(自动跑)/notify●蓝(跑完知会)/ask🔒黄(要人拍)
      const AUT={auto:'<span style="color:var(--live)" title="auto·自动执行">●</span>',notify:'<span style="color:var(--accent)" title="notify·执行后知会">●</span>',ask:'<span style="color:var(--warn)" title="ask·须人拍板">🔒</span>'};
      const aut=n.autonomy?(' '+(AUT[n.autonomy]||esc(n.autonomy))):'';
      // P1 预检:pass✓/pending…/fail⚠(fail 时节点已 blocked·hover 看 findings 为啥拦)
      const pc=n.precheck||{}; const PCE={pass:'✓',pending:'…',fail:'⚠'};
      const pcHtml=pc.status?(' <span title="预检:'+esc(pc.status)+((pc.findings&&pc.findings.length)?(' — '+pc.findings.map(esc).join(' / ')):'')+'" style="font-size:10px">'+(PCE[pc.status]||'')+'</span>'):'';
      return '<span class="lr-node '+cls+'" title="'+esc(n.owner||'无owner')+(n.acceptance?(' · 验收:'+esc(n.acceptance)):'')+'"><b>'+esc(n.status||'?')+'</b> '+esc(n.label||n.id||'')+au+aut+pcHtml+fk+mg+bo+bc+vd+'</span>';
    };
    let nodes;
    if((seg.edges||[]).length){
      // 有边→分层 DAG:每列=同拓扑深度,列间以 ⇒ 分隔,天然显分叉/合并
      const colKeys=Object.keys(lay.cols).map(Number).sort((a,b)=>a-b);
      nodes='<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
        +colKeys.map(c=>'<div style="display:flex;flex-direction:column;gap:5px">'+lay.cols[c].map(nodeHtml).join('')+'</div>').join('<span class="lr-arrow">⇒</span>')
        +'</div>';
    } else {
      nodes=segNodes.map(nodeHtml).join('')||'<span class="muted" style="font-size:11px">（当前段无活节点)</span>';
    }
    // 塌缩归档段(简报核心:达锚→塌缩归档不占活内存)
    const arch=(t.archived_segments||[]).map(s=>'<span class="lr-node idle" title="'+esc(s.summary||'')+'" style="opacity:.7">📦段'+esc(''+s.seg_index)+' 塌缩('+((s.collapsed&&s.collapsed.nodes)||s.node_count||0)+'节点)→'+esc(s.to_anchor||'')+'</span>').join('');
    // ④ 进度%(分母生长/可倒退=诚实,不报警)
    const pg=t.progress||{}; const pct=(pg.pct!=null)?pg.pct:0;
    const pbar='<div class="bar-track" style="flex:1"><div class="bar-fill" style="width:'+Math.max(2,Math.min(100,pct))+'%"></div></div>';
    const gw=pg.growable?' <span class="muted" style="font-size:10px" title="分母会生长/可倒退=诚实信号非bug">📈可生长</span>':'';
    // ★P1 价值徽标:统一读 task.efficiency(使用方 confirmation_savings_from_events 原始输出·单一源·MasterAgent 每 task 实时刷)。
    //   saved=confirmation_turns_saved;breakdown{batch:roundtrips_saved_by_batching, precheck:precheck_fail_avoided}。saved>0 才显(0=无价值·不显噪音)。
    const _ef=t.efficiency||{}; let savedBadge=''; const _sv=+_ef.confirmation_turns_saved||0;
    if(_sv>0){ const _b=+_ef.roundtrips_saved_by_batching||0, _p=+_ef.precheck_fail_avoided||0;
      savedBadge='<span class="cap-chip live" style="font-size:10px" title="'+esc(_ef.note||'合批+precheck 少打断用户的确认次数(使用方 events 口径)')+'">💡已省 '+_sv+' 次确认 (batch'+_b+'+precheck'+_p+')</span>'; }
    // ★P1 ⑤ 三角色(拆解者/执行者/跟踪者):读 t.roles;缺→—(诚实,不编)
    const rl=t.roles||{};
    const roleChip=(k,lab)=>'<span class="cap-chip '+(rl[k]?'live':'none')+'" style="font-size:10px" title="'+lab+'">'+lab+':'+esc(rl[k]||'—')+'</span>';
    const rolesHtml='<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">⑤三角色:</b>'
      +roleChip('decomposer','拆解者')+roleChip('executor','执行者')+roleChip('tracker','跟踪者')+'</div>';
    // ★P1 🔭 跟踪者活动面板(MasterAgent task.tracker_status·null=未指派·不编造):谁在盯/盯啥/待督办/最近督办动作。
    const trk=t.tracker_status; let trackerHtml;
    if(!trk){ trackerHtml='<div style="margin-top:6px" class="muted"><b style="font-size:11.5px">🔭 跟踪者:</b> 未指派跟踪者</div>'; }
    else {
      const _ago=(iso)=>{ if(!iso) return '?'; const s=Math.floor((Date.now()-Date.parse(iso))/1000); return isNaN(s)?esc(iso):(s<0?'刚刚':fmtAge(s)+'前'); };
      const watch=(trk.watching||[]).map(w=>'<li>'+esc(w)+'</li>').join('');
      const pend=(trk.pending||[]).map(p=>'<li>'+esc(p)+'</li>').join('');
      const acts=(trk.recent_actions||[]).slice(-4).reverse().map(a=>'<div style="font-size:10.5px" class="muted">· <span class="mono">'+esc((a.ts||'').replace('T',' ').slice(5,16))+'</span> '+esc(a.action||'')+'</div>').join('');
      trackerHtml='<div style="margin-top:6px;border-left:2px solid var(--accent);padding-left:8px">'
        +'<div style="font-size:11.5px"><b>🔭 跟踪者 '+esc(trk.tracker||'?')+'</b> <span class="muted">· 上次巡 '+_ago(trk.last_scan)+'</span></div>'
        +(trk.summary?('<div class="muted" style="font-size:11px;margin-top:2px">'+esc(trk.summary)+'</div>'):'')
        +(watch?('<div style="font-size:11px;margin-top:3px"><b class="muted">关注:</b><ul style="margin:2px 0 0 16px;padding:0">'+watch+'</ul></div>'):'')
        +(pend?('<div style="font-size:11px;margin-top:3px"><b style="color:var(--warn)">待督办:</b><ul style="margin:2px 0 0 16px;padding:0">'+pend+'</ul></div>'):'')
        +(acts?('<div style="margin-top:3px"><b class="muted" style="font-size:10.5px">最近督办:</b>'+acts+'</div>'):'')
        +'</div>';
    }
    // ⑥ 复杂度:raw 数值 + P1 成长曲线(t.complexity_series=[{t,size}]·MasterAgent emit;缺→仅 raw)
    const cx=t.complexity||{};
    const spark=_lrSpark(t.complexity_series);
    const cxHtml=Object.keys(cx).length?('<div style="margin-top:6px;font-size:11px" class="muted">⑥复杂度:节点'+esc(''+(cx.nodes_total||0))+'/边'+esc(''+(cx.edges_total||0))+' · 活节点'+esc(''+(cx.nodes_live||0))+' · 宽'+esc(''+(cx.width_max||0))+'/深'+esc(''+(cx.depth||0))+' · 合并'+esc(''+(cx.merges||0))+'/跨任务边'+esc(''+(cx.cross_task_edges||0))+(cx.note?(' <i>['+esc(cx.note)+']</i>'):'')
      +(spark?('<div style="margin-top:4px">📈成长曲线(净规模/时间):'+spark+'</div>'):'<div style="margin-top:2px;font-size:10px">📈成长曲线:待 MasterAgent emit t.complexity_series(读 longrange_events.jsonl 成长史)</div>')+'</div>'):'';
    return '<div class="tl-card" data-key="lr-'+esc(''+t.id)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:var(--accent)">🗂️ '+esc(t.title||t.id)+'</b>'
      +(t.status?'<span class="cap-chip" style="font-size:10px">'+esc(t.status)+'</span>':'')
      +savedBadge
      +'<span class="spacer" style="flex:1"></span><span class="muted" style="font-size:11px">更新 '+esc(t.updated||'')+'</span></div>'
      +rolesHtml
      +trackerHtml
      +'<div style="margin-top:8px"><b class="muted" style="font-size:11.5px">①锚点脊柱(👤user方向里程碑/🤖machine收敛检查点·状态upcoming灰/active蓝/reached绿):</b><div class="lr-spine">'+(sp||'<span class="muted">(无脊柱)</span>')+'</div></div>'
      +(arch?'<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">已塌缩归档段:</b><div class="lr-net">'+arch+'</div></div>':'')
      +'<div style="margin-top:8px"><b class="muted" style="font-size:11.5px">②当前活跃段'+(seg.from_anchor?(' ['+esc(seg.from_anchor)+'→'+esc(seg.to_anchor||'')+']'):'')+'(pending灰/ready浅蓝/running蓝/blocked红/await_decision黄/done绿):</b><div class="lr-net">'+nodes+'</div></div>'
      +'<div style="margin-top:8px;display:flex;gap:8px;align-items:center"><b class="muted" style="font-size:11.5px">④进度:</b>'+pbar+'<span class="mono" style="font-size:12px">'+esc(''+pct)+'%</span><span class="muted" style="font-size:10px">段 '+(pg.done_segments||0)+'/'+(pg.total_segments||0)+' · 段内节点 '+(pg.seg_done_nodes||0)+'/'+(pg.seg_total_nodes||0)+'</span>'+gw+'</div>'
      +cxHtml
      +'</div>';
  }).join(''));
}

// ===== tab 分页 =====
const TAB_NAMES={fleet:'🛰 舰队/运维',bi:'📊 运营/资源','usage':'📈 历史用量',caps:'🧬 能力池',ctx:'🧠 上下文',prompts:'📝 提示词审核',tasklines:'🎯 任务线看板',groups:'🧩 工作组',mechanisms:'🔧 机制',longrange:'🗂️ 长程任务',tasks:'📋 任务/协作',live:'🔴 实时'};
let curTab='fleet'; try{ curTab=localStorage.getItem('busTab')||'fleet'; }catch(e){}
if(!TAB_NAMES[curTab]) curTab='fleet';
function showTab(t){
  if(!TAB_NAMES[t]) t='fleet';
  curTab=t; try{ localStorage.setItem('busTab',t); }catch(e){}
  document.querySelectorAll('#tabbar .tab').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
  document.querySelectorAll('main > [data-tab]').forEach(el=>el.classList.toggle('tabhide',el.getAttribute('data-tab')!==t));
  window.scrollTo({top:0});
  // 刷新门控(P1):只在进入对应 tab 时拉它的重数据 —— 不再无视可见性后台全刷(省 psutil 子进程+token)
  if(t==='bi'){ loadBI(); }
  else if(t==='usage') loadUsage(true);
  else if(t==='caps') loadCaps();
  else if(t==='ctx') loadCtx();
  else if(t==='prompts'){ if(!_promptsLoaded) loadPrompts(); }
  else if(t==='tasklines') loadTasklines();
  else if(t==='groups') loadGroups();
  else if(t==='mechanisms') loadMechanisms();
  else if(t==='longrange') loadLongrange();
  else if(t==='fleet') load();
}
// 🎯 长期任务线看板:卡片 + ⧗待决策徽标 + 展开 current/deps/decisions(待用户决策红/黄高亮)
async function loadTasklines(){
  const box=document.getElementById('tlBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/tasklines'); renderTasklines(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderTasklines(d){
  const box=document.getElementById('tlBox'); const meta=document.getElementById('tlMeta'); if(!box) return;
  if(!d||!d.ok){ box.innerHTML='<div class="muted" style="padding:8px 16px">tasklines 读取失败:'+esc((d&&d.error)||'?')+'</div>'; if(meta)meta.textContent=''; return; }
  const tls=d.tasklines||[];
  if(meta) meta.textContent='刷新 '+esc(d.now||'')+' · '+(d.count||0)+' 条任务线 · ⧗待决策 '+(d.pending_total||0)+' · 数据更新 '+esc(d.updated||'');
  morphInto(box, tls.map(t=>{
    const pend=t.pending_decisions||0, tot=t.total_decisions||0;
    const badge = tot? ('<span class="cap-chip" style="color:var(--'+(pend?'warn':'live')+')" title="待用户决策/总决策">'+(pend?'⧗待决策 '+pend+'/'+tot:'✓决策全清 '+tot)+'</span>') : '';
    const decRows=(t.decisions||[]).map(de=>{
      const cls=de.answered?'live':'warn';
      const tag=de.answered?'✓已答':'⧗待你拍板';
      return '<div class="tl-dec" style="border-left:3px solid var(--'+cls+');padding:4px 8px;margin:4px 0;background:rgba(255,255,255,.02)">'
        +'<span class="cap-chip" style="color:var(--'+cls+')">'+tag+'</span> '
        +'<span style="font-size:12px">'+esc(de.q)+'</span> '
        +'<span class="muted" style="font-size:11px">· 待办池 ref='+esc(de.ref||'?')+' status='+esc(de.status)+'</span></div>';
    }).join('')||'<div class="muted" style="font-size:11.5px;padding:2px 8px">无决策点</div>';
    const deps=(t.deps||[]).length? ('<div style="font-size:11.5px;margin-top:4px"><b class="muted">依赖:</b> '+(t.deps.map(x=>'<span class="cap-chip">'+esc(typeof x==="string"?x:(x.title||x.id||JSON.stringify(x)))+'</span>').join(''))+'</div>') : '';
    return '<div class="tl-card" data-key="tl-'+esc(t.id)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px;'+(pend?'border-left:3px solid var(--warn)':'')+'">'
      +'<div class="tl-head" style="cursor:pointer;display:flex;gap:10px;align-items:center;flex-wrap:wrap" onclick="var b=this.parentNode.querySelector(\'.tl-body\');b.style.display=b.style.display===\'none\'?\'block\':\'none\';">'
      +'<b style="color:var(--accent)">'+esc(t.id)+'</b> <b>'+esc(t.title)+'</b> '+badge
      +'<span class="muted" style="font-size:11.5px">owner '+esc(t.owner)+' · '+esc(t.status)+'</span>'
      +'<span class="spacer" style="flex:1"></span><span class="muted" style="font-size:11px">▸ '+esc(t.cadence||'')+' · 更新 '+esc(t.updated||'')+'</span></div>'
      +'<div class="tl-body" style="display:none;margin-top:8px">'
      +(t.current?'<div style="font-size:12.5px;margin-bottom:4px"><b class="muted">当前:</b> '+esc(t.current)+'</div>':'')
      +deps
      +'<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">决策点(交叉待办池):</b>'+decRows+'</div>'
      +(t.truth_source?'<div class="muted" style="font-size:11px;margin-top:6px">truth_source: '+esc(t.truth_source)+'</div>':'')
      +'</div></div>';
  }).join('')||'<div class="muted" style="padding:8px 16px">无任务线</div>');
}
function syncTabTop(){ const hd=document.querySelector('header'); if(hd) document.getElementById('tabbar').style.top=hd.offsetHeight+'px'; }
document.querySelectorAll('#tabbar .tab').forEach(b=>b.onclick=()=>showTab(b.dataset.t));
document.getElementById('gConfirm').onclick=()=>{ showTab('tasks');
  const tp=[...document.querySelectorAll('main > [data-tab="tasks"]')].find(x=>x.querySelector('#todoBox'));
  if(tp) setTimeout(()=>tp.scrollIntoView({behavior:'smooth',block:'start'}),60); };

// 🩺 bus 健康徽标 —— 吃 /api/health(后台子进程刷新的缓存,请求零扫描),25s 轮询一次
let _health=null;
function hRow(k,v,bad){ return '<div class="hblk"><span class="hk">'+k+'</span><b class="'+(bad?'hbad':'')+'">'+v+'</b></div>'; }
function renderHealth(){
  const b=document.getElementById('healthBadge'); const pn=document.getElementById('healthPanel'); if(!b) return;
  const d=_health;
  if(!d || d.loading){ b.className='hbadge loading'; b.textContent='🩺 …'; b.title='健康数据加载中（后台扫描）'; return; }
  const warns=d.warns||[];
  if(d.healthy){ b.className='hbadge ok'; b.textContent='🩺 健康'; b.title='bus 全绿'; }
  else{ b.className='hbadge warn'; b.textContent='🩺 ⚠ '+warns.length+' 项'; b.title=warns.join(' / '); }
  // 展开明细(六块)
  const sw=d.selfwake||{}, q=d.queues||{}, ls=d.loss||{}, fl=d.fleet||{}, w=d.webui||{};
  const el=d.edge_lock;
  let h='<h4>🩺 bus 健康 · '+esc(d.as_of||'')+'</h4>';
  h+= warns.length? warns.map(x=>'<div class="hwarn">⚠ '+esc(x)+'</div>').join('')
                  : '<div class="hblk hgood"><b>✓ 全绿,无警告</b></div>';
  // 自醒四态:✓armed / ◐recent(待确认·瞬时非告警) / ⚠stale(掉线) / —off。bad 只由 stale 触发,recent 不飘红。
  h+=hRow('自醒 ✓armed/◐待确认/⚠掉线/—off',
          (sw.armed||0)+' / '+(sw.recent||0)+' / '+(sw.stale||0)+' / '+(sw.off||0), (sw.stale||0)>0)
    + ((sw.recent||0)>0?'<div class="hk" style="padding-left:8px">◐待确认 '+(sw.recent||0)+':有心跳无wait·瞬时态(刚fire待重挂/交互中)·非告警</div>':'')
    + ((sw.stale_roles&&sw.stale_roles.length)?'<div class="hk hbad" style="padding-left:8px">⚠掉线:'+esc(sw.stale_roles.join('、'))+'</div>':'');
  h+=hRow('队列 in/proc/held/failed', (q.inbox||0)+' / '+(q.processing||0)+' / '+(q.held||0)+' / '+(q.failed||0), (q.held||0)+(q.failed||0)>0);
  h+=hRow('消息丢失 孤儿/卡死/久滞', (ls.orphans||0)+' / '+((ls.stuck||[]).length)+' / '+((ls.stale_inbox||[]).length), ((ls.orphans||0)+(ls.stuck||[]).length+(ls.stale_inbox||[]).length)>0);
  (ls.stuck||[]).forEach(s=>{ h+='<div class="hk" style="padding-left:8px">卡死 '+esc(s.role)+' · '+esc(s.title||s.id||'')+' ('+esc(''+s.age_min)+'min)</div>'; });
  (ls.stale_inbox||[]).forEach(s=>{ h+='<div class="hk" style="padding-left:8px">久滞 '+esc(s.role)+' ('+esc(''+s.age_min)+'min)</div>'; });
  h+=hRow('daemon 期望/在跑', (fl.alive||0)+' / '+(fl.expected||0), (fl.dead&&fl.dead.length)>0 || (fl.orphans&&fl.orphans.length)>0);
  if(fl.dead&&fl.dead.length) h+='<div class="hk hbad" style="padding-left:8px">已死:'+esc(fl.dead.join('、'))+'</div>';
  h+=hRow('WebUI owner pid / 陈旧实例', (w.owner_pid||'?')+' / '+(w.stale_dupes||0), (w.stale_dupes||0)>0);
  h+=hRow('Edge 调试锁', el?('被占:'+esc(el.who||'?')):'空闲', !!el);
  pn.innerHTML=h;
}
async function loadHealth(){
  try{ const r=await fetch('/api/health'); _health=await r.json(); }catch(e){ /* 保留上次 */ }
  renderHealth();
}
document.getElementById('healthBadge').onclick=(e)=>{
  e.stopPropagation();
  const pn=document.getElementById('healthPanel');
  pn.style.display = pn.style.display==='none' ? 'block' : 'none';
};
document.addEventListener('click',(e)=>{ // 点外部收起
  const wrap=document.getElementById('healthWrap');
  if(wrap && !wrap.contains(e.target)) document.getElementById('healthPanel').style.display='none';
});
window.addEventListener('resize',syncTabTop);
syncTabTop(); showTab(curTab);

// 首屏顺序很关键:/api/bi 与 /api/state 都吃 psutil 进程扫,ThreadingHTTPServer 在 GIL 下
// 无法真正并行跑这两段 CPU 密集逻辑 —— 若并发,/api/state 的冷启全进程扫(~2s,机器忙时更久)
// 会把本应 ~220ms 的 /api/bi 一起拖到数秒。故【先让 /api/bi 跑完再发 /api/state】:
// BI 表格先出(首屏快),随后 /api/state 慢扫只影响舰队表(本就慢,晚 ~220ms 无感)。
// 配合服务端启动预热 _proc_cache,首个 /api/bi 即 ~220ms。loadBI 已从 load() 解耦,
// 下面 10s 定时单独刷 BI,/api/state 的 SSE/轮询刷新不再连带重拉 BI。
loadBI().finally(load); connectSSE(); setPoll();
// 刷新门控(P1):BI 定时刷只在【BI tab 可见】时跑,不再无视可见性每 10s 扫 psutil + 拉 /api/bi
setInterval(()=>{ if(curTab==='bi') loadBI(); }, 10000);
// 能力池兜底轮询:caps tab 可见时每 10s 重拉 /api/caps —— agents.json/capabilities.json 改了(如培训师加 cap)
// 不必再手动刷新即可反映。门控同 BI:不在 caps tab 不跑。/api/caps 只读 JSON、零 psutil 扫,轮询开销极小。
setInterval(()=>{ if(curTab==='caps') loadCaps(); }, 10000);
// 首屏一次性预热(各自独立懒加载,不拖首屏);之后按 tab 进入再刷(见 showTab)
setTimeout(()=>loadUsage(true), 800); setTimeout(loadSessions, 1200);
loadHealth(); setInterval(loadHealth, 25000);   // 🩺 健康徽标:25s 轮询(后端缓存,零扫描)
loadCtx(); setInterval(loadCtx, 15000);          // 🧠 上下文占用徽标+看板:15s 轮询(后端缓存,请求零现读)— 全局告警,不分 tab
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # 静默,避免刷屏

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/" or path == "/index.html":
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/api/state":
            q = parse_qs(u.query)
            role = (q.get("reply_role", ["Master"])[0]) or "Master"
            fresh = (q.get("fresh", ["0"])[0]) in ("1", "true")
            self._send(200, build_state(role, fresh=fresh))
        elif path == "/api/events":
            self._sse()
        elif path == "/api/health":
            # 只读后台缓存(子进程刷新);冷启未就绪回 loading 占位,绝不现扫
            d = get_health()
            self._send(200, d if d is not None else {"loading": True})
        elif path == "/api/tray":
            # 运转指示计数(bus_tray.py 系统托盘 + WebUI 共用·单一源=fleet 缓存口径)
            self._send(200, bus_tray_counts())
        elif path == "/api/reap":
            # 孤儿巡检:只读 dry-run,复用 agentbus reap --dry-run
            self._send(200, reap_dryrun())
        elif path == "/api/todos":
            self._send(200, {"ok": True, "todos": _sorted_todos()})
        elif path == "/api/live":
            q = parse_qs(u.query)
            self._send(200, read_live(role=(q.get("role", [None])[0]),
                                      dispatch_id=(q.get("id", [None])[0])))
        elif path == "/api/bi":
            self._send(200, build_bi())
        elif path == "/api/caps":
            # 能力池/caps:capabilities.json + agents.json 实时只读(不改 caps)
            self._send(200, build_caps())
        elif path == "/api/usage":
            q = parse_qs(u.query)
            fresh = (q.get("fresh", ["0"])[0]) in ("1", "true")
            self._send(200, get_usage(force=fresh))
        elif path == "/api/dispatch_metrics":
            q = parse_qs(u.query)
            fresh = (q.get("fresh", ["0"])[0]) in ("1", "true")
            self._send(200, get_dispatch_metrics(force=fresh))
        elif path == "/api/sessions":
            self._send(200, read_sessions())
        elif path == "/api/ctx":
            # 只读后台缓存(bounded jsonl 尾读);冷启未就绪回 loading 占位,绝不现读
            d = get_ctx()
            self._send(200, d if d is not None else {"loading": True})
        elif path == "/api/ctxpanel":
            # 纯上下文预算看板:内存 ctx_cache(+who_cache 仅校验 role 标签),请求路径零扫描/零读盘
            self._send(200, build_ctxpanel())
        elif path == "/api/tasklines":
            self._send(200, build_tasklines())
        elif path == "/api/groups":
            self._send(200, build_groups())
        elif path == "/api/mechanisms":
            self._send(200, build_mechanisms())
        elif path == "/api/longrange":
            self._send(200, build_longrange())
        elif path == "/api/prompts":
            # 提示词审核:手动刷新时直接读小文件(无缓存/无 poller);只读
            self._send(200, build_prompts())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/send":
            b = self._body_json()
            to = (b.get("to") or "").strip()
            task = (b.get("task") or "").strip()
            if not to or not task:
                self._send(400, {"ok": False, "error": "to/task 必填"})
                return
            notify = (b.get("notify") or "auto").strip()
            if notify not in ("auto", "always", "never"):
                notify = "auto"
            try:
                tid = send_task(to, b.get("title", ""), task,
                                requires_approval=bool(b.get("requires_approval")),
                                notify=notify)
                self._send(200, {"ok": True, "id": tid})
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)})
        elif path == "/api/supervise":
            # 手动触发 daemon 自愈:复用 agentbus supervise(可 dry-run)
            b = self._body_json()
            self._send(200, run_supervise(dry_run=bool(b.get("dry_run"))))
        elif path == "/api/clear_replies":
            b = self._body_json()
            role = (b.get("role") or "Master").strip()
            n = clear_replies(role, b.get("file"))
            self._send(200, {"ok": True, "cleared": n})
        elif path == "/api/todos/add":
            self._send(200, todo_add(self._body_json()))
        elif path == "/api/todos/update":
            self._send(200, todo_update(self._body_json()))
        elif path == "/api/todos/delete":
            self._send(200, todo_delete(self._body_json()))
        elif path == "/api/todos/dispatch":
            self._send(200, todo_dispatch(self._body_json()))
        elif path == "/api/todos/answer":
            self._send(200, todo_answer(self._body_json()))
        elif path == "/api/lr-decide":
            b = self._body_json()
            self._send(200, resolve_lr_batch((b.get("task_id") or "").strip(), b.get("approvals") or {}))
        else:
            self._send(404, {"error": "not found"})

    def _sse(self):
        global _change_ver
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            with _change_cv:
                last = _change_ver
            while True:
                with _change_cv:
                    # 最长 15s 等变更;到点发心跳保活
                    got = _change_cv.wait_for(lambda: _change_ver != last, timeout=15)
                    if got:
                        last = _change_ver
                if got:
                    self.wfile.write(b"data: change\n\n")
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def start_supervisor(interval=90):
    """后台 supervisor 线程:周期跑 `agentbus supervise`,自愈 fleet.json 里已死的 daemon。
    复用常驻 WebUI 进程当宿主(它本身已登录自启 + WmiPrvSE 脱壳常驻),不另起需要再看护的进程。
    用 Event.wait 计时(非 sleep 轮询忙等),进程退出即停。"""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "supervise"],
                               capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
            except Exception:
                pass

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[supervise] fleet 自愈线程已启动,间隔 %ds(读 fleet.json,重启已死 daemon)" % interval)
    return t


def start_selfwake_guard(interval=180):
    """常驻自醒探测网(Master 令·B):每 ~interval 秒后台跑 `ab selfwake-guard` ——
    它扫掉自醒(stale)的 agent → dedup 投「重挂」提醒到其 inbox + WinRT toast 报警。
    由 WebUI 8799 always-on 驱动,不依赖任何 agent 醒着(把「18h 没人发现掉自醒」变「3 分钟内报警」)。
    探测逻辑全在 agentbus(cmd_selfwake_guard),此处只周期调它。
    · daemon=True 随 server 退出;· 首轮先 sleep(避开重启后 agent 尚未重挂的假警);
    · 失败静默 try/except,绝不掀翻 8799;· 子进程带 CREATE_NO_WINDOW 免弹窗(见记忆 popen-claude-no-window)。"""
    def loop():
        while True:
            time.sleep(interval)
            try:
                subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "selfwake-guard"],
                               capture_output=True, timeout=60, creationflags=_NO_WINDOW)
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--no-supervise", action="store_true", help="关闭 fleet daemon 自愈线程")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost"):
        print("拒绝:内部工具只允许绑 127.0.0.1 /", args.host)
        sys.exit(2)

    start_watcher()
    start_fleet_refresher(20)         # 唯一舰队刷新者:20s 子进程扫描刷新 _fleet_cache(请求路径零扫描)
    start_health_refresher(25)        # 唯一健康刷新者:25s 子进程 health --json 刷新 _health_cache(请求路径零扫描)
    start_ctx_refresher(12)           # 唯一上下文占用刷新者:12s bounded jsonl 尾读刷新 _ctx_cache(1M 撞墙预警,请求路径零现读)
    start_who_refresher(20)           # 唯一 who 刷新者:20s 子进程 who --json 刷新 _who_cache(供上下文模块校验 role 在岗,请求路径零扫描)
    start_status_pusher(15)           # daemon→agent 定时通信:15s 刷新 fleet_status.json(复用上面缓存的扫描,不再自扫)
    if not args.no_supervise:
        start_supervisor()
    start_selfwake_guard(180)         # 常驻自醒探测网(Master令·B):180s 跑 ab selfwake-guard,掉自醒→inbox提醒+toast(8799 always-on 驱动,不依赖 agent 醒着)
    # 预热缓存(后台线程,不阻塞启动):
    #  · build_bi() 跑一遍 → 填充 _proc_cache(psutil.Process 对象 + cpu_percent 基线),
    #    使【首个真实 /api/bi】~220ms 而非冷启 ~4.5s(_proc_cache 无 TTL,长期受益,首屏 BI 关键)。
    #  · compute_fleet_cached() → 首个 /api/state 若在 3s TTL 内命中热缓存(快速重连时受益)。
    #  · 舰队缓存由 start_fleet_refresher 首轮立即填充(~4s),无需在此预热。
    def _prewarm():
        try:
            build_bi()
        except Exception:
            pass
    threading.Thread(target=_prewarm, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    url = "http://%s:%d/" % (args.host, args.port)
    print("=" * 56)
    print(" Agent Bus 控制台已启动")
    print("   ", url)
    print("    BUS =", BUS)
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止。")
        srv.shutdown()


if __name__ == "__main__":
    main()
