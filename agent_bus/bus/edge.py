# -*- coding: utf-8 -*-
"""bus.edge —— Edge/CDP 操作互斥锁（云文档 CDP 焦点冲突止血，v1.2）。

多 agent 挂同一 Edge 操作云文档时 OS 焦点独占→串台。约定：Edge/CDP 操作前 edge-acquire、用完 edge-release。
bus 放 edge.lock（O_EXCL 原子抢）；抢不到 watchdog 等释放（不轮询）；stale 回收（持锁者进程死/超 ttl 强制抢）。

依赖：config(BUS/_now)· io(_audit)· proc(_age_seconds)· mailbox(_resolve_self_role)· psutil/watchdog。无反向依赖=无环。
"""
import json, os, socket, threading, time

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, _now
from bus.io import _audit, _steal_stale_lock
from bus.proc import _age_seconds
from bus.mailbox import _resolve_self_role


def _edge_lock_path():
    return BUS / "edge.lock"


def _edge_holder():
    p = _edge_lock_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"who": "?", "acquired_at": ""}


def _role_proc_alive(role):
    """持锁 role 是否还有活进程(daemon_pid 或 claude_pid 存在)——死了则锁可被回收。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return False
    try:
        pr = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return False
    if psutil is None:
        return True  # 没 psutil 不敢判死
    for k in ("daemon_pid", "claude_pid"):
        pid = pr.get(k)
        if pid and psutil.pid_exists(pid):
            return True
    return False


def _edge_stale(holder, ttl):
    if not holder:
        return False
    age = _age_seconds(holder.get("acquired_at", ""))
    if age is not None and age > ttl:
        return True
    return not _role_proc_alive(holder.get("who", ""))


def _wait_file_gone(path, timeout):
    if not path.exists():
        return
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    ev = threading.Event(); name = path.name

    class H(FileSystemEventHandler):
        def on_deleted(self, e):
            if str(e.src_path).endswith(name):
                ev.set()
        def on_moved(self, e):
            if str(getattr(e, "src_path", "")).endswith(name):
                ev.set()

    obs = Observer(); obs.schedule(H(), str(path.parent), recursive=False); obs.start()
    try:
        if not path.exists():
            return
        ev.wait(timeout=timeout)
    finally:
        obs.stop(); obs.join()


def cmd_edge_acquire(args):
    who = args.who or _resolve_self_role() or os.environ.get("AGENTBUS_ROLE", "unknown")
    lp = _edge_lock_path()
    deadline = time.time() + args.timeout
    while True:
        try:
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"who": who, "acquired_at": _now(), "host": socket.gethostname()},
                                    ensure_ascii=False).encode("utf-8"))
            os.close(fd)
            _audit("edge_acquire", who=who)
            print(json.dumps({"acquired": True, "who": who}, ensure_ascii=False)); return
        except FileExistsError:
            holder = _edge_holder()
            if _edge_stale(holder, args.ttl):
                if _steal_stale_lock(lp):   # ABA 防(P1):rename 原子夺取恒一胜,绝不 unlink 到别人新锁
                    _audit("edge_steal", who=who, victim=(holder or {}).get("who"))
                continue  # 夺取成败都重试 O_EXCL(败=别人抢先,正常竞争)
            if time.time() >= deadline:
                print(json.dumps({"acquired": False, "timeout": True,
                                  "holder": (holder or {}).get("who")}, ensure_ascii=False)); return
            _wait_file_gone(lp, min(max(deadline - time.time(), 0.1), 10))  # 等释放(事件),最多 10s 再探
        except Exception as e:
            print(json.dumps({"acquired": False, "error": str(e)}, ensure_ascii=False)); return


def cmd_edge_release(args):
    who = args.who or _resolve_self_role() or os.environ.get("AGENTBUS_ROLE", "unknown")
    lp = _edge_lock_path()
    holder = _edge_holder()
    if not holder:
        print(json.dumps({"released": False, "reason": "无锁"}, ensure_ascii=False)); return
    if not args.force and holder.get("who") != who:
        print(json.dumps({"released": False, "reason": "锁在 %s 手里(--force 可强释)" % holder.get("who")}, ensure_ascii=False)); return
    try: lp.unlink()
    except Exception: pass
    _audit("edge_release", who=who)
    print(json.dumps({"released": True, "by": who}, ensure_ascii=False))
