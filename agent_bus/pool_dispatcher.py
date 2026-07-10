#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pool_dispatcher.py — 弹性池派发器(Phase 2a · 2026-07-10)。

⏸ FUTURE·不自动运行(用户 2026-07-10 拍板改走【手动管理 tab 下线】,不建自动 offline 机制)。
   运行时 join/leave 已支持手动缩放(关成员=presence掉=临时缩池·别人顶上;重开=rejoin),不需自动派发器。
   本脚本【留档备 future】,当前【无 daemon 拉它、不激活】。决策支持改由 ab mem-cap + WebUI 内存/并发看板(使用方)提供。
   dry-run 已验 gate/scaling 正确;future 若走自动弹性可直接启用。


治内存墙:池成员不再是持久 claude tab(~360MB/个常驻),而是【按需 spin 一次性 claude -p worker 跑一分片即释】。
空闲时只本派发器阻塞在 watchdog(~0 token、极小内存)。

机制:watchdog 监组共享 inbox(1a);有分片 + 当前并发 < 内存感知上限 → 原子取分片(O_EXCL)→ 以 shard_tid
建 _dispatch 记录(from=分片原 from=fanouter)→ spawn dispatch_agent(detached claude -p)跑完 done 到
<fanouter>/done/<shard_tid>(1b collect --stream 边到边收)→ worker 退出释放内存。跟踪 Popen 计并发。

内存感知上限(使用方 公式·ab._mem_cap):budget=avail−reserve;cap=budget//per_worker;
硬闸 percent≥92 或 avail<reserve → cap=0(已颠簸别 spin,分片留组 inbox 等 pull 背压=宁排队别拖垮全机)。

用法:python pool_dispatcher.py --group 示例组 [--per-worker-gb 0.5] [--reserve-gb 2] [--dry-run]
  --dry-run:只算 spin 决策 + 打日志,不真取分片/不 spawn(验 gate/scaling 用)。
"""
import argparse, json, os, sys, time, subprocess
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.environ.setdefault("PYTHONUTF8", "1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentbus as ab


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _log(g, msg):
    line = "%s [pool:%s] %s" % (_now(), g, msg)
    print(line, flush=True)
    try:
        d = ab._group_dir(g); d.mkdir(parents=True, exist_ok=True)
        with open(d / "dispatcher.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _pending_shards(g, include_approval=False):
    gin = ab._group_dir(g) / "inbox"
    if not gin.exists():
        return []
    out = []
    for p in sorted(gin.glob("*.json")):
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
            if o.get("requires_approval") and not include_approval:
                continue
        except Exception:
            continue
        out.append(p)
    return out


def _take_shard(path):
    """O_EXCL 原子取一分片:建 <task>.lock 独占 → 读内容 → 移出组 inbox(改名 .taken)→ 返回 task dict。
    抢输/孤儿处理复用 agentbus 的 stale 语义(这里简化:锁存在且非 stale 就跳过)。返回 (obj) 或 None。"""
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if ab._group_lock_stale(lock):
            try: lock.unlink()
            except Exception: pass
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except Exception:
                return None
        else:
            return None
    except OSError:
        return None
    try:
        os.write(fd, json.dumps({"who": "pool_dispatcher", "pid": os.getpid(), "acquired_at": _now()}).encode("utf-8"))
    except Exception:
        pass
    os.close(fd)
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        obj = None
    try:
        path.unlink()          # 移出组 inbox(内容已捕获进 dispatch 记录)
    except Exception:
        pass
    try:
        lock.unlink()
    except Exception:
        pass
    return obj


def _spawn_worker(g, obj, cwd):
    """以 shard_tid 建 _dispatch 记录 + spawn dispatch_agent(detached claude -p)。结果 done 到 <from>/done/<shard_tid>。
    返回 Popen(供计并发)或 None。"""
    tid = obj.get("id") or ab._gen_id("shard")
    rec = {"id": tid, "from": obj.get("from", "unknown"), "name": "pool:%s" % g,
           "title": obj.get("title", ""), "task": obj.get("task", ""), "cwd": cwd,
           "timeout": 3600, "created": _now(), "kind": "pool_shard", "group": g}
    dd = ab.BUS / "_dispatch"; dd.mkdir(parents=True, exist_ok=True)
    ab._write_json(dd / (tid + ".json"), rec)
    da = str(Path(__file__).parent / "dispatch_agent.py")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        return subprocess.Popen([sys.executable, da, "--id", tid], creationflags=flags,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ab.BUS))
    except Exception as e:
        _log(g, "spawn 失败 %s: %s" % (tid, e))
        return None


def main():
    ap = argparse.ArgumentParser(prog="pool_dispatcher")
    ap.add_argument("--group", required=True)
    ap.add_argument("--per-worker-gb", type=float, default=0.5, dest="per_worker_gb")
    ap.add_argument("--reserve-gb", type=float, default=2.0, dest="reserve_gb")
    ap.add_argument("--cwd", default=None, help="worker cwd(默认 config.default_worker_cwd)")
    ap.add_argument("--poll", type=float, default=3600.0, help="watchdog 等待上限(秒)")
    ap.add_argument("--include-approval", action="store_true", dest="include_approval")
    ap.add_argument("--dry-run", action="store_true", help="只算 spin 决策+打日志,不取分片/不 spawn")
    a = ap.parse_args()
    g = a.group
    cwd = a.cwd or ab.cfg("default_worker_cwd")
    ab._group_dir(g).joinpath("inbox").mkdir(parents=True, exist_ok=True)
    _log(g, "弹性派发器启动 per_worker=%.2fG reserve=%.2fG cwd=%s%s"
         % (a.per_worker_gb, a.reserve_gb, cwd, " [DRY-RUN]" if a.dry_run else ""))

    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    import threading
    ev = threading.Event()

    class H(FileSystemEventHandler):
        def on_created(self, e):
            if str(e.src_path).endswith(".json"):
                ev.set()
        def on_moved(self, e):
            if str(getattr(e, "dest_path", "")).endswith(".json"):
                ev.set()

    gin = ab._group_dir(g) / "inbox"
    obs = Observer(); obs.schedule(H(), str(gin), recursive=False)
    obs_ok = True
    try:
        obs.start()
    except Exception:
        obs_ok = False

    running = []   # 活跃 worker Popen
    try:
        while True:
            running = [p for p in running if p.poll() is None]   # 剪已退出的(释放并发额)
            pend = _pending_shards(g, a.include_approval)
            cap_info = ab._mem_cap(a.per_worker_gb, a.reserve_gb)
            cap = cap_info["mem_cap"]
            slots = max(0, cap - len(running))
            to_spin = min(len(pend), slots)
            if pend:
                _log(g, "pending=%d running=%d mem{avail=%sG pct=%s cap=%d hard=%s} → spin=%d"
                     % (len(pend), len(running), cap_info["avail_gb"], cap_info["percent"], cap, cap_info["hard_gate"], to_spin))
            for i in range(to_spin):
                if a.dry_run:
                    _log(g, "  [dry-run] would spin worker for %s" % pend[i].name)
                    continue
                obj = _take_shard(pend[i])
                if not obj:
                    continue      # 被别的派发器/worker 抢走或坏 → 跳过
                p = _spawn_worker(g, obj, cwd)
                if p:
                    running.append(p)
                    _log(g, "  spin worker pid=%s shard=%s from=%s" % (p.pid, obj.get("id"), obj.get("from")))
            # 到顶/硬闸/dry-run:分片留组 inbox 等(pull 背压)。等事件或周期复查(有 running 时短轮询收割额度)。
            ev.clear()
            if obs_ok:
                ev.wait(timeout=(5.0 if running else a.poll))   # 有在跑的→5s 复查收割退出额度;全空闲→长等事件
            else:
                time.sleep(2.0)
    except KeyboardInterrupt:
        pass
    finally:
        if obs_ok:
            try:
                obs.stop(); obs.join()
            except Exception:
                pass


if __name__ == "__main__":
    main()
