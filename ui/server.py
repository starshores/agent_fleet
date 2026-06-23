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

隐私铁律:pid_tags.json 里 private=true 的(如「其他事务🔒」)只显示 pid=标签🔒,
          绝不显示 cwd/细节,也绝不 psutil 探它。
"""
import argparse
import json
import os
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


def compute_fleet():
    """舰队总览 —— 复刻 cmd_who 的判据(presence 心跳 × 实际 claude 进程)。"""
    psutil_ok = ab.psutil is not None
    alive = ab._alive_claude_pids() if psutil_ok else set()
    tags = ab._load_pidtags()
    roles = _roles()
    rows = []
    claimed = set()

    for r in roles:
        pf = BUS / r / "presence.json"
        _sw = ab._self_wake_state(r, alive)    # 自醒检测(传入已算好的 alive 集合,免每 role 重扫)
        row = {
            "role": r,
            "inbox": _count(r, "inbox"),
            "processing": _count(r, "processing"),
            "done": _count(r, "done"),
            "held": _count(r, "held"),      # 高风险待批(requires_approval 留 inbox;held 为显式批前暂存)
            "failed": _count(r, "failed"),  # 毒任务(reap 超 max-requeue)
            "pid": None, "pid_display": None, "age": None, "mode": None, "cwd": None,
            "started": None, "host": None,
            "verdict": "未声明(无 presence)", "vclass": "none",
            "self_wake": _sw[0], "self_wake_detail": _sw[1],   # armed=在岗 stale=掉线 off=未醒
        }
        if not pf.exists():
            rows.append(row)
            continue
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            row["verdict"] = "presence 损坏"
            row["vclass"] = "dead"
            rows.append(row)
            continue
        pid = pr.get("claude_pid")
        age = ab._age_seconds(pr.get("last_seen", ""))
        mode = pr.get("mode", "?")
        wt = pr.get("wait_timeout") or 110
        listen_fresh = (age is not None and age <= wt + 60)
        row.update({
            "age": age, "mode": mode,
            "cwd": pr.get("cwd"), "started": pr.get("started"),
            "host": pr.get("host"),
        })

        if mode == "daemon":
            # 无可见 tab 的后台守护进程:按 daemon_pid 判活(psutil.pid_exists)。
            # daemon_pid 不是 claude 进程 —— 绝不计入 claimed(不污染"在跑 claude"统计)。
            dpid = pr.get("daemon_pid")
            row["pid_display"] = "d:%s" % (dpid or "?")
            d_alive = ab.psutil.pid_exists(dpid) if (dpid and psutil_ok) else None
            if not psutil_ok:
                row["verdict"] = "daemon(心跳%ss)" % (age if age is not None else "?")
                row["vclass"] = "idle"
            elif d_alive:
                row["verdict"] = "✓ daemon在听"
                row["vclass"] = "live"
            elif dpid:
                row["verdict"] = "✗ daemon已退"
                row["vclass"] = "dead"
            else:
                row["verdict"] = "daemon?"
                row["vclass"] = "warn"
            rows.append(row)
            continue

        # 普通可见 tab(claude 进程)
        row["pid"] = pid
        row["pid_display"] = str(pid) if pid else None
        proc_alive = (pid in alive) if (pid and psutil_ok) else None
        if pid:
            claimed.add(pid)
        if not psutil_ok:
            row["verdict"] = "心跳%ss前(mode=%s)" % (age if age is not None else "?", mode)
            row["vclass"] = "idle"
        elif pid and not proc_alive:
            row["verdict"] = "✗ 进程已退(残留)"
            row["vclass"] = "dead"
        elif mode == "listening" and proc_alive and listen_fresh:
            row["verdict"] = "✓ worker在听"
            row["vclass"] = "live"
        elif mode == "listening" and proc_alive:
            row["verdict"] = "⚠ worker静默(超心跳%ss)" % (age if age is not None else "?")
            row["vclass"] = "warn"
        elif proc_alive:
            row["verdict"] = "已声明(hello),未起循环"
            row["vclass"] = "idle"
        else:
            row["verdict"] = "心跳%ss前(无pid)" % (age if age is not None else "?")
            row["vclass"] = "warn"
        rows.append(row)

    # 在跑但未认领的 claude 进程:区分 已标注(非fleet) vs 真未声明
    tagged, unknown = [], []
    if psutil_ok:
        rest = sorted(alive - claimed)
        for pid in rest:
            t = tags.get(str(pid))
            if t:
                # 隐私铁律:private 只暴露 pid + label,绝不探细节
                tagged.append({
                    "pid": pid,
                    "label": t.get("label", "?"),
                    "private": bool(t.get("private")),
                })
            else:
                unknown.append(pid)

    return {
        "psutil": psutil_ok,
        "roles": rows,
        "tagged": tagged,
        "unknown": unknown,
        "alive_count": len(alive),
        "fleet_count": len(claimed),
    }


# compute_fleet 每次跑 ab._alive_claude_pids()(全进程扫描),本机进程多 ~4s。
# SSE/轮询频繁打 /api/state → 卡。加短 TTL(3s)内存缓存:窗口内直接返回上次结果,
# 全进程扫描最多每 3s 一次。compute 放锁内 → 同一窗口的并发请求只扫一次,其余等到后拿新缓存。
_FLEET_TTL = 3.0
_fleet_cache = {"ts": 0.0, "data": None}
_fleet_lock = threading.Lock()


def compute_fleet_cached():
    with _fleet_lock:
        c = _fleet_cache
        if c["data"] is not None and (time.time() - c["ts"]) < _FLEET_TTL:
            return c["data"]
        data = compute_fleet()
        c["data"] = data
        c["ts"] = time.time()
        return data


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
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=40)
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
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    except Exception as e:
        return {"ok": False, "error": str(e), "lines": []}
    out = (r.stdout or "").strip()
    lines = [l for l in out.splitlines() if l.strip()] if out else []
    return {"ok": r.returncode == 0, "error": (r.stderr or "").strip() or None, "lines": lines}


def build_state(reply_role="Master"):
    fleet = compute_fleet_cached()  # 3s TTL 缓存,避免每次 /api/state 全进程扫描
    # 给每个 role 附带可点开的任务明细(inbox/processing/held/failed 任一非空就读)
    for row in fleet["roles"]:
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
        _sw = ab._self_wake_state(r)   # 自醒检测(pid_exists 便宜判活)
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
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
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


def _sorted_todos():
    items = _load_todos()
    items.sort(key=lambda t: (STATUS_ORDER.get(t.get("status"), 9),
                              PRIORITY_ORDER.get(t.get("priority"), 9),
                              t.get("deadline") or "9999"))
    return items


def todo_add(b):
    items = _load_todos()
    o = {
        "id": _todo_id(),
        "title": (b.get("title") or "").strip() or "(无标题)",
        "detail": (b.get("detail") or "").strip(),
        "source": (b.get("source") or "").strip(),
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
            for f in ("title", "detail", "source", "deadline"):
                if f in b and b[f] is not None:
                    o[f] = str(b[f]).strip()
            if b.get("priority") in _VALID_PRIORITY:
                o["priority"] = b["priority"]
            if b.get("status") in _VALID_STATUS:
                o["status"] = b["status"]
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


def todo_dispatch(b):
    """把一条待办派成 bus 任务(复用 send_task,from=Master),回写 task_id 闭环追踪。"""
    tid = b.get("id")
    to = (b.get("to") or "").strip()
    if not to:
        return {"ok": False, "error": "未选 to role"}
    items = _load_todos()
    for o in items:
        if o.get("id") == tid:
            title = o.get("title") or "(待办)"
            parts = [o.get("detail") or ""]
            if o.get("source"):
                parts.append("\n来源:%s" % o["source"])
            if o.get("deadline"):
                parts.append("\n截止:%s" % o["deadline"])
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
.badge.live{background:rgba(62,207,142,.15);color:var(--live)}
.badge.warn{background:rgba(245,166,35,.15);color:var(--warn)}
.badge.dead{background:rgba(255,93,93,.15);color:var(--dead)}
.badge.idle{background:rgba(125,138,160,.15);color:var(--idle)}
.badge.none{background:rgba(90,97,114,.15);color:var(--none)}
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
/* 待办池 TODO Pool */
.todo-card{margin:8px 16px;background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px 12px;border-left:3px solid var(--line)}
.todo-card.pri-hi{border-left-color:var(--dead)}
.todo-card.pri-mid{border-left-color:var(--warn)}
.todo-card.pri-lo{border-left-color:var(--idle)}
.todo-card.is-done{opacity:.6}
.todo-h{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.todo-title{font-weight:600;font-size:13.5px}
.todo-detail{color:#cdd4e0;font-size:12.5px;margin:6px 0 0;white-space:pre-wrap;word-break:break-word}
.todo-meta{color:var(--dim);font-size:11.5px;margin-top:6px;display:flex;gap:14px;flex-wrap:wrap;align-items:center}
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
</style></head>
<body>
<header>
  <h1>🛰 Agent Bus 控制台</h1>
  <span class="pill"><span id="connDot" class="dot"></span><span id="connTxt">连接中…</span></span>
  <span class="pill">在跑 claude <b id="aliveN" style="color:var(--txt)">–</b> · fleet <b id="fleetN" style="color:var(--txt)">–</b></span>
  <span class="spacer"></span>
  <span class="ctrl">兜底轮询
    <select id="pollSel">
      <option value="0">关(仅事件)</option>
      <option value="3000">3s</option>
      <option value="5000" selected>5s</option>
      <option value="10000">10s</option>
      <option value="30000">30s</option>
    </select>
  </span>
  <button class="btn ghost" id="refreshBtn" style="margin:0;padding:6px 14px">手动刷新</button>
  <span class="pill muted" id="updT">–</span>
</header>

<main>
  <section>
    <div class="sec-h">A · 舰队总览 <span class="sub">role 身份 / 心跳 / 队列(同 <span class="mono">who</span>+<span class="mono">list</span>)— 点行展开任务</span></div>
    <div class="sec-b">
      <table>
        <thead><tr><th>Role</th><th>判定</th><th>pid<span class="sub" style="font-weight:400"> (d:=daemon)</span></th><th>心跳</th><th>inbox</th><th>proc</th><th>done</th><th>held</th><th>failed</th><th>cwd</th></tr></thead>
        <tbody id="fleetBody"></tbody>
      </table>
    </div>
    <div class="foot" id="procFoot"></div>
  </section>

  <section>
    <div class="sec-h">B · 待办池 TODO Pool <span class="sub">集中管理待办 · 存 <span class="mono">agent_bus/todos.json</span> · 可一键【派活】成 bus 任务</span>
      <span class="spacer" style="flex:1"></span>
      <span class="filt">优先级
        <select id="filtPri"><option value="">全部</option><option>高</option><option>中</option><option>低</option></select>
        状态
        <select id="filtStatus"><option value="">全部</option><option>待办</option><option>进行中</option><option>搁置</option><option>完成</option></select>
        <label class="chk" style="margin:0"><input type="checkbox" id="filtHideDone"> 隐藏已完成</label>
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

  <div class="grid2">
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

  <section>
    <div class="sec-h">H · 最近事件 <span class="sub">审计流 <span class="mono">events.jsonl</span> 末 40 行 · 倒序(send/claim/done)</span></div>
    <div id="eventBox" style="max-height:300px;overflow:auto"></div>
  </section>

  <div class="grid2">
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

  <section>
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
</main>

<div id="toast" class="toast"></div>

<script>
let state=null, pollTimer=null, lastTasksOpen=new Set();

function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function fmtAge(a){ if(a==null) return '?'; if(a<60) return a+'s'; if(a<3600) return Math.floor(a/60)+'m'+(a%60)+'s'; return Math.floor(a/3600)+'h'+Math.floor((a%3600)/60)+'m';}
function qc(n,cls){return `<span class="qcount ${n?cls:'z'}">${n}</span>`;}
function swBadge(s,detail){
  const t=esc(detail||'');
  if(s==='armed') return ` <span class="badge live" title="自醒在岗 ${t}">🔔自醒</span>`;
  if(s==='stale') return ` <span class="badge dead" title="自醒掉线!需重挂 ${t}">⚠自醒掉线</span>`;
  return ` <span class="badge none" title="未自醒 ${t}">—</span>`;
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

  // fleet table
  const tb=document.getElementById('fleetBody'); tb.innerHTML='';
  f.roles.forEach(r=>{
    const hasTasks=(r.inbox+r.processing+(r.held||0)+(r.failed||0))>0;
    const tr=document.createElement('tr');
    tr.className='role-row'+(hasTasks?' clickable':'');
    tr.innerHTML=`<td><b>${esc(r.role)}</b></td>
      <td><span class="badge ${r.vclass}">${esc(r.verdict)}</span>${swBadge(r.self_wake,r.self_wake_detail)}</td>
      <td class="mono">${esc(r.pid_display||'–')}</td>
      <td class="mono">${fmtAge(r.age)}</td>
      <td>${qc(r.inbox,'ib')}</td><td>${qc(r.processing,'pr')}</td><td>${qc(r.done,'dn')}</td>
      <td>${qc(r.held||0,'held')}</td><td>${qc(r.failed||0,'failed')}</td>
      <td class="cwd" title="${esc(r.cwd||'')}">${esc(r.cwd||'–')}</td>`;
    tb.appendChild(tr);
    if(hasTasks){
      const det=document.createElement('tr'); det.className='tasks'; det.dataset.role=r.role;
      const open=lastTasksOpen.has(r.role);
      det.style.display=open?'':'none';
      let inner='<div class="task-wrap">';
      const subBadge={inbox:'idle',processing:'warn',held:'held',failed:'failed'};
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
      det.innerHTML=`<td colspan="10">${inner}</td>`;
      tb.appendChild(det);
      tr.onclick=()=>{const vis=det.style.display!=='none';det.style.display=vis?'none':'';
        if(vis)lastTasksOpen.delete(r.role);else lastTasksOpen.add(r.role);};
    }
  });

  // process footer
  let foot=[];
  if(f.tagged.length) foot.push('已标注(非fleet):'+f.tagged.map(t=>`<span class="tag ${t.private?'lock':''}">${esc(t.label)} pid ${t.pid}${t.private?' 🔒':''}</span>`).join(''));
  foot.push(`<span class="muted">在跑 claude ${f.alive_count} · fleet ${f.fleet_count} · 已标注 ${f.tagged.length} · 未声明 ${f.unknown.length}${f.unknown.length?' ('+f.unknown.join(', ')+')':''}</span>`);
  document.getElementById('procFoot').innerHTML=foot.join('<br>');

  // daemon 自愈面板(fleet.json × 存活)
  const db=document.getElementById('daemonBox'); const dm=state.daemons||{daemons:[]};
  if(dm.error){ db.innerHTML='<div class="dmn"><span class="badge dead">err</span> '+esc(dm.error)+'</div>'; }
  else if(!dm.daemons.length){ db.innerHTML='<div class="muted" style="padding:8px 16px">fleet.json 无 daemon 条目</div>'; }
  else{ db.innerHTML=dm.daemons.map(d=>`<div class="dmn">
      <span class="badge ${d.alive?'live':'dead'}">${d.alive?'✓ 存活':'✗ 已死'}</span>
      <b>${esc(d.role)}</b>
      <span class="cwd" title="${esc(d.cwd||'')}">${esc(d.cwd||'')}</span>
      <span class="spacer" style="flex:1"></span>
      <span class="muted">wait ${esc(d.wait_timeout)}s</span></div>`).join(''); }

  // 最近事件(events.jsonl 倒序)
  const eb=document.getElementById('eventBox'); const evs=state.events||[];
  if(!evs.length){ eb.innerHTML='<div class="muted" style="padding:8px 16px">（无事件）</div>'; }
  else{ eb.innerHTML=evs.map(e=>{
      const tail=e.event==='send'?('→ '+esc(e.to)+(e.title?' · '+esc(e.title):'')):
                 e.event==='claim'?('by '+esc(e.role)):
                 e.event==='done'?('→ '+esc(e.to)+' · '+esc(e.status||'')):(esc(e.raw||''));
      return `<div class="ev"><span class="ets">${esc(e.ts)}</span>
        <span class="et ${esc(e.event)}">${esc(e.event)}</span>
        <span class="mono">${esc(e.id||'')}</span>
        ${e.frm?'<span class="muted">from '+esc(e.frm)+'</span>':''}
        <span>${tail}</span></div>`; }).join(''); }

  // send-to dropdown (preserve selection)
  const sel=document.getElementById('sendTo'); const cur=sel.value;
  sel.innerHTML=state.all_roles.map(r=>`<option ${r==cur?'selected':''}>${esc(r)}</option>`).join('');

  // reply role dropdown
  const rr=document.getElementById('replyRole'); const rc=rr.value||state.reply_role;
  rr.innerHTML=state.all_roles.map(r=>`<option ${r==rc?'selected':''}>${esc(r)}</option>`).join('');

  // replies
  const rb=document.getElementById('replyBox');
  if(!state.replies.length){ rb.innerHTML='<div class="muted" style="padding:6px 16px">（无回填）</div>'; }
  else{ rb.innerHTML=state.replies.map(r=>`<div class="reply">
      <div class="r-h"><span class="badge ${r.status==='ok'?'live':'dead'}">${esc(r.status||'?')}</span>
        <span class="mono">${esc(r.id)}</span> · from <b>${esc(r.from)}</b>
        <span>${esc(r.title)}</span> <span class="muted">${esc(r.finished)}</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" style="margin:0;padding:2px 9px" onclick="clearOne('${esc(r.file)}')">清除</button></div>
      <pre>${esc(r.result)}</pre></div>`).join(''); }

  renderTodos();
  fillLiveRoles();
}

// 实时视图 live.log —— .log 不走 SSE,选中 role 后独立轮询(1.5s)
let livePoll=null;
function fillLiveRoles(){
  const sel=document.getElementById('liveRole'); if(!sel) return;
  const cur=sel.value;
  sel.innerHTML=['<option value="">（选 role）</option>']
    .concat((state.all_roles||[]).map(r=>`<option ${r==cur?'selected':''}>${esc(r)}</option>`)).join('');
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
function renderTodos(){
  const box=document.getElementById('todoBox'); if(!box) return;
  let items=(state.todos||[]).slice();
  const fp=document.getElementById('filtPri').value;
  const fs=document.getElementById('filtStatus').value;
  const hideDone=document.getElementById('filtHideDone').checked;
  if(fp) items=items.filter(t=>t.priority===fp);
  if(fs) items=items.filter(t=>t.status===fs);
  if(hideDone) items=items.filter(t=>t.status!=='完成');
  if(!items.length){ box.innerHTML='<div class="muted" style="padding:8px 16px">（无待办,或被筛选）</div>'; return; }
  const roleOpts=state.all_roles.map(r=>`<option>${esc(r)}</option>`).join('');
  box.innerHTML=items.map(t=>{
    const od=isOverdue(t);
    const stSel=['待办','进行中','搁置','完成'].map(s=>`<option ${s===t.status?'selected':''}>${s}</option>`).join('');
    const link=t.task_id?`<span class="linkpill" title="已派给 ${esc(t.dispatched_to||'')}">🔗 ${esc(t.dispatched_to||'')} · ${esc(t.task_id)}</span>`:'';
    return `<div class="todo-card ${PRI_CARD[t.priority]||''} ${t.status==='完成'?'is-done':''}">
      <div class="todo-h">
        <span class="badge ${PRI_CLS[t.priority]||'p-mid'}">${esc(t.priority)}</span>
        <span class="badge ${ST_CLS[t.status]||'s-todo'}">${esc(t.status)}</span>
        <span class="todo-title">${esc(t.title)}</span>
        ${link}
      </div>
      ${t.detail?`<div class="todo-detail">${esc(t.detail)}</div>`:''}
      <div class="todo-meta">
        ${t.source?`<span>来源:${esc(t.source)}</span>`:''}
        ${t.deadline?`<span class="${od?'overdue':''}">截止:${esc(t.deadline)}${od?' ⚠逾期':''}</span>`:''}
        <span>建:${esc(t.created)}</span>
      </div>
      <div class="todo-acts">
        <span class="muted">状态</span>
        <select onchange="tStatus('${t.id}',this.value)">${stSel}</select>
        <span class="muted" style="margin-left:6px">派给</span>
        <select id="trole-${t.id}">${roleOpts}</select>
        <button class="btn mini" onclick="tDispatch('${t.id}')">派活</button>
        <button class="btn mini ghost" onclick="tEdit('${t.id}')">编辑</button>
        <button class="btn mini danger" onclick="tDelete('${t.id}')">删除</button>
      </div>
    </div>`;
  }).join('');
}

async function tPost(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
async function tStatus(id,val){ const j=await tPost('/api/todos/update',{id,status:val}); if(j.ok)toast('状态已改为 '+val); else toast('改状态失败:'+(j.error||'?'),true); load(); }
async function tDelete(id){ if(!confirm('删除这条待办?')) return; const j=await tPost('/api/todos/delete',{id}); toast(j.ok?'已删除':'删除失败',!j.ok); load(); }
async function tDispatch(id){
  const to=document.getElementById('trole-'+id).value;
  if(!confirm('把这条待办派给 '+to+' (from=Master)?')) return;
  const j=await tPost('/api/todos/dispatch',{id,to});
  if(j.ok){toast('✅ 已派给 '+to+' · task '+j.task_id);}else{toast('派活失败:'+(j.error||'?'),true);}
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

async function load(role){
  try{
    const rr=role||document.getElementById('replyRole').value||'Master';
    const res=await fetch('/api/state?reply_role='+encodeURIComponent(rr));
    state=await res.json(); render();
    document.getElementById('updT').classList.remove('flash');void document.getElementById('updT').offsetWidth;
    document.getElementById('updT').classList.add('flash');
  }catch(e){ console.error(e); }
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
    es.onmessage=()=>load();
    es.onerror=()=>{setConn(false); es.close(); setTimeout(connectSSE,3000);};
  }catch(e){ setConn(false); }
}

async function clearOne(file){
  const role=document.getElementById('replyRole').value;
  await fetch('/api/clear_replies',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({role,file})});
  toast('已清除 '+file); load();
}

document.getElementById('refreshBtn').onclick=()=>load();
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
document.getElementById('filtHideDone').onchange=renderTodos;

// 实时视图 live.log
document.getElementById('liveRole').onchange=setLivePoll;
document.getElementById('liveBottom').onclick=()=>{const b=document.getElementById('liveBox');b.scrollTop=b.scrollHeight;};

load(); connectSSE(); setPoll();
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
            self._send(200, build_state(role))
        elif path == "/api/events":
            self._sse()
        elif path == "/api/reap":
            # 孤儿巡检:只读 dry-run,复用 agentbus reap --dry-run
            self._send(200, reap_dryrun())
        elif path == "/api/todos":
            self._send(200, {"ok": True, "todos": _sorted_todos()})
        elif path == "/api/live":
            q = parse_qs(u.query)
            self._send(200, read_live(role=(q.get("role", [None])[0]),
                                      dispatch_id=(q.get("id", [None])[0])))
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
                               capture_output=True, text=True, timeout=60)
            except Exception:
                pass

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[supervise] fleet 自愈线程已启动,间隔 %ds(读 fleet.json,重启已死 daemon)" % interval)
    return t


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
    start_status_pusher(5)            # daemon→agent 定时通信:5s 刷新 fleet_status.json
    if not args.no_supervise:
        start_supervisor()
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
