# -*- coding: utf-8 -*-
"""bus.lock —— 通用共享资源互斥(分离 A1·2026-09-23·Master数分 b3282f 签):bus.edge 的算法逐字参数化(resource 名 → 锁文件)。

任何域的独占资源(浏览器焦点 / 某设备 / 某远端会话…)都用同一原语:
  ab lock acquire --resource <name> [--who --timeout --ttl]  → stdout JSON {acquired, who}
  ab lock release --resource <name> [--who --force]           → {released, by}
  ab lock status  [--resource <name>]                          → {locks:[{resource, who, acquired_at, age_s}]}
锁文件:BUS/locks/<name>.lock(O_EXCL 原子抢·抢不到 watchdog 等释放不轮询·stale 回收:持锁 role 进程死/超 ttl → rename 原子夺取·ABA 防)。
过渡特判:resource == "edge" 映射旧路径 BUS/edge.lock(health/status/UI 的 edge_lock 读方零改动·A4 再迁)。
依赖:config(BUS/_now)· io(_audit/_steal_stale_lock)· watch· proc· mailbox。无反向依赖=无环。
"""
import json, os, re, socket, time

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, _now
from bus.io import _audit, _steal_stale_lock
from bus.watch import arm_watch
from bus.proc import _age_seconds
from bus.mailbox import _resolve_self_role

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def lock_path(resource):
    if resource == "edge":                       # 过渡特判(A4 迁 locks/ 后删)
        return BUS / "edge.lock"
    if not _NAME_RE.match(resource or ""):
        raise SystemExit("✗ --resource 须为 1-64 位字母/数字/_-.:%r" % (resource,))
    d = BUS / "locks"; d.mkdir(exist_ok=True)
    return d / (resource + ".lock")


def lock_holder(resource):
    p = lock_path(resource)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"who": "?", "acquired_at": ""}


def _role_proc_alive(role):
    """持锁 role 是否还有活进程(daemon 或绑定的 Claude/Codex host)——死了则锁可被回收。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return False
    try:
        pr = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return False
    if psutil is None:
        return True  # 没 psutil 不敢判死
    daemon_pid = pr.get("daemon_pid")
    if daemon_pid and psutil.pid_exists(daemon_pid):
        return True
    from bus.proc import _host_pid_alive
    host_pid = pr.get("host_pid") or pr.get("claude_pid") or pr.get("codex_pid")
    host_kind = pr.get("host_kind") or ("claude" if pr.get("claude_pid") else ("codex" if pr.get("codex_pid") else None))
    if host_pid and _host_pid_alive(host_pid, host_kind):
        return True
    return False


def _stale(holder, ttl):
    if not holder:
        return False
    age = _age_seconds(holder.get("acquired_at", ""))
    if age is not None and age > ttl:
        return True
    return not _role_proc_alive(holder.get("who", ""))


def _wait_file_gone(path, timeout):
    if not path.exists():
        return
    ev, _stop_watch, ok = arm_watch([path.parent], suffix=path.name,
                                    on=("deleted", "moved"), moved_field="src_path")
    try:
        if not path.exists():
            return
        if ok:
            ev.wait(timeout=timeout)
        else:                          # 降级:轮询阻塞等释放(别秒返)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and path.exists():
                time.sleep(0.5)
    finally:
        _stop_watch()


def acquire(resource, who=None, timeout=120.0, ttl=300.0):
    """返回结果 dict(与旧 edge-acquire stdout JSON 同构);调用方负责 print。"""
    who = who or _resolve_self_role() or os.environ.get("AGENTBUS_ROLE", "unknown")
    lp = lock_path(resource)
    deadline = time.time() + timeout
    legacy = resource == "edge"
    while True:
        try:
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"who": who, "acquired_at": _now(), "host": socket.gethostname(),
                                     "resource": resource}, ensure_ascii=False).encode("utf-8"))
            os.close(fd)
            _audit("edge_acquire" if legacy else "lock_acquire", who=who, resource=resource)
            return {"acquired": True, "who": who}
        except FileExistsError:
            holder = lock_holder(resource)
            if _stale(holder, ttl):
                if _steal_stale_lock(lp):   # ABA 防(P1):rename 原子夺取恒一胜,绝不 unlink 到别人新锁
                    _audit("edge_steal" if legacy else "lock_steal", who=who, resource=resource,
                           victim=(holder or {}).get("who"))
                continue  # 夺取成败都重试 O_EXCL(败=别人抢先,正常竞争)
            if time.time() >= deadline:
                return {"acquired": False, "timeout": True, "holder": (holder or {}).get("who")}
            _wait_file_gone(lp, min(max(deadline - time.time(), 0.1), 10))  # 等释放(事件),最多 10s 再探
        except Exception as e:
            return {"acquired": False, "error": str(e)}


def release(resource, who=None, force=False):
    who = who or _resolve_self_role() or os.environ.get("AGENTBUS_ROLE", "unknown")
    lp = lock_path(resource)
    holder = lock_holder(resource)
    if not holder:
        return {"released": False, "reason": "无锁"}
    if not force and holder.get("who") != who:
        return {"released": False, "reason": "锁在 %s 手里(--force 可强释)" % holder.get("who")}
    try: lp.unlink()
    except Exception: pass
    _audit("edge_release" if resource == "edge" else "lock_release", who=who, resource=resource)
    return {"released": True, "by": who}


def holders():
    """全部持锁快照 [{resource, who, acquired_at, age_s}](含过渡期 edge.lock)。status/health/UI 读方(A4)。"""
    out, seen = [], set()
    d = BUS / "locks"
    for p in (sorted(d.glob("*.lock")) if d.is_dir() else []):
        r = p.name[:-5]; seen.add(r)
        h = lock_holder(r)
        if h:
            out.append({"resource": r, "who": h.get("who"), "acquired_at": h.get("acquired_at", ""),
                        "age_s": _age_seconds(h.get("acquired_at", ""))})
    h = lock_holder("edge")
    if h and "edge" not in seen:
        out.append({"resource": "edge", "who": h.get("who"), "acquired_at": h.get("acquired_at", ""),
                    "age_s": _age_seconds(h.get("acquired_at", ""))})
    return out


def cmd_lock(args):
    if args.op in ("acquire", "release") and not args.resource:
        raise SystemExit("✗ lock %s 须 --resource <name>" % args.op)
    if args.op == "acquire":
        print(json.dumps(acquire(args.resource, args.who, args.timeout, args.ttl), ensure_ascii=False))
    elif args.op == "release":
        print(json.dumps(release(args.resource, args.who, args.force), ensure_ascii=False))
    else:
        hs = holders()
        if args.resource:
            hs = [h for h in hs if h["resource"] == args.resource]
        print(json.dumps({"locks": hs}, ensure_ascii=False))
