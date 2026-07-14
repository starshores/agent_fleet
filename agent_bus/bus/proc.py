# -*- coding: utf-8 -*-
"""bus.proc —— 进程扫描 / 进程祖先上溯 / 时间年龄(psutil 层叶子,不依赖上层)。

身份的物理基础:OS 里所有 claude 进程长得一样,靠父链上溯 + 单次全扫描缓存来识别/探活。
"""
import os, time

try:
    import psutil
except Exception:
    psutil = None


def _claude_ancestor():
    """从当前进程向上走父链,找到 claude.exe 那个 tab 进程。返回 (pid, cwd, chain)。
    身份只能靠自报:OS 里所有 claude 进程长得一样,谁是谁要会话自己声明。"""
    if psutil is None:
        return None, None, []
    chain = []
    claude_pid = None
    cwd = None
    try:
        p = psutil.Process(os.getpid())
        for _ in range(15):
            p = p.parent()
            if p is None:
                break
            try:
                nm = (p.name() or "").lower()
            except Exception:
                nm = ""
            chain.append({"pid": p.pid, "name": nm})
            if "claude" in nm:
                claude_pid = p.pid
                try:
                    cwd = p.cwd()
                except Exception:
                    cwd = None
                break
    except Exception:
        pass
    return claude_pid, cwd, chain


_PROC_SCAN = {"ts": 0.0, "claude": None, "wait_roles": None}
_PROC_SCAN_TTL = 2.0


def _proc_scan():
    """单次全进程扫描,一次算出两样东西:【在跑 claude pid 集合】+【挂着 agentbus wait 的 role 集合】。
    本机进程数百个,process_iter 一次就贵(~2s);who/selfwake/compute_fleet 原先各自单独全扫,
    冷启叠加数秒(BI 首屏慢的根因之一)。合并为一次 + 2s TTL 缓存:连续调用复用同一次扫描。
    cmdline 是贵操作,只对 name 含 python 的进程【懒取】,不给全部进程取。返回 (claude_pids, wait_roles)。"""
    c = _PROC_SCAN
    now = time.time()
    if c["claude"] is not None and (now - c["ts"]) < _PROC_SCAN_TTL:
        return c["claude"], c["wait_roles"]
    claude = set()
    wait_roles = set()
    if psutil is not None:
        for p in psutil.process_iter(["name"]):
            try:
                nm = (p.info.get("name") or "").lower()
                if "claude" in nm:
                    claude.add(p.pid)
                    continue
                if "python" in nm:                      # 只对 python 进程取 cmdline(贵操作限于少数)
                    cl = p.cmdline()
                    if "wait" in cl and any("agentbus" in str(x) for x in cl):
                        for i, x in enumerate(cl):
                            if x == "--role" and i + 1 < len(cl):
                                wait_roles.add(cl[i + 1])
                                break
            except Exception:
                continue
    c.update(ts=now, claude=claude, wait_roles=wait_roles)
    return claude, wait_roles


def _alive_claude_pids():
    """当前在跑的 claude 进程 pid 集合(复用 _proc_scan 的单次扫描+缓存)。"""
    return _proc_scan()[0]


def _age_seconds(ts):
    try:
        t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
        return max(0, int(time.time() - t))
    except Exception:
        return None
