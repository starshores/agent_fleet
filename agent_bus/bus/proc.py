# -*- coding: utf-8 -*-
"""bus.proc —— 进程扫描 / 进程祖先上溯 / 时间年龄(psutil 层叶子,不依赖上层)。

身份的物理基础:OS 里所有 claude 进程长得一样,靠父链上溯 + 单次全扫描缓存来识别/探活。
"""
import os, time

try:
    import psutil
except Exception:
    psutil = None


def _claude_by_session(sid):
    """按 session_id 反查活 claude 进程:命令行含该 sid 的 claude.exe(`claude --resume <sid>` 锚)。
    治本回退(2026-07-29 模型侧报):本 harness 下 Bash 子 shell 的父进程链偶发够不到 tab 的
    claude.exe → parent-walk 失败 → presence 继承旧(死)pid,--resume 恢复的会话全中招。
    仅 resume 的会话命令行带 sid(新开 tab 不带 → 返 None,走上层继承逻辑)。返回 (pid, cwd) 或 (None, None)。"""
    if psutil is None or not sid:
        return None, None
    try:
        for p in psutil.process_iter(["name"]):
            try:
                if "claude" not in ((p.info.get("name") or "").lower()):
                    continue
                if sid in " ".join(p.cmdline()):
                    try:
                        cwd = p.cwd()
                    except Exception:
                        cwd = None
                    return p.pid, cwd
            except Exception:
                continue
    except Exception:
        pass
    return None, None


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


def _host_kind(name):
    """Return the supported interactive host kind for an exact executable name."""
    base = os.path.basename(name or "").lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base if base in ("claude", "codex") else None


def _host_session_id(kind=None, env=None):
    """Return this session's opaque host id without crossing Claude/Codex namespaces."""
    env = env or os.environ
    if kind == "claude":
        return env.get("CLAUDE_CODE_SESSION_ID")
    if kind == "codex":
        return env.get("CODEX_SESSION_ID") or env.get("CODEX_THREAD_ID")
    return (env.get("CLAUDE_CODE_SESSION_ID") or env.get("CODEX_SESSION_ID")
            or env.get("CODEX_THREAD_ID"))


def _host_ancestor():
    """Find the current Claude or Codex host on this process's own parent chain.

    Returns (kind, pid, cwd, chain). This deliberately does not scan other
    Codex processes: callers can only bind to the host that launched them.
    """
    if psutil is None:
        return None, None, None, []
    chain = []
    try:
        p = psutil.Process(os.getpid())
        for _ in range(20):
            p = p.parent()
            if p is None:
                break
            try:
                name = p.name() or ""
            except Exception:
                name = ""
            chain.append({"pid": p.pid, "name": name.lower()})
            kind = _host_kind(name)
            if kind:
                try:
                    cwd = p.cwd()
                except Exception:
                    cwd = None
                return kind, p.pid, cwd, chain
    except Exception:
        pass
    return None, None, None, chain


def _proc_host_ancestor(pid):
    """Find a supported host process above one known process, without global scanning."""
    if psutil is None:
        return None, None
    try:
        p = psutil.Process(pid)
        for _ in range(20):
            p = p.parent()
            if p is None:
                break
            try:
                kind = _host_kind(p.name() or "")
            except Exception:
                kind = None
            if kind:
                return kind, p.pid
    except Exception:
        pass
    return None, None


def _host_pid_alive(pid, kind=None):
    """Validate one known host PID by its executable name (guards PID reuse)."""
    if psutil is None or not pid:
        return False
    try:
        proc = psutil.Process(int(pid))
        actual = _host_kind(proc.name() or "")
        return bool(actual and (kind is None or actual == kind))
    except Exception:
        return False


def _proc_claude_ancestor(pid):
    """从【给定 pid】向上 walk 父链找 claude.exe 祖先 pid(判"某 wait 进程属哪个活会话实例"用)。
    与 _claude_ancestor 的区别:那个走当前进程,这个走任意 pid。追不到(父链断/进程没了)返 None。
    ★为何用 claude 祖先 pid 而非 session_id 判同会话:--resume 会复用 session_id(旧/新会话同 sid),
    但每个活 claude.exe 是独立进程 pid → 祖先 pid 才精确区分"同一个活跃会话实例"。"""
    if psutil is None:
        return None
    try:
        p = psutil.Process(pid)
        for _ in range(15):
            p = p.parent()
            if p is None:
                break
            try:
                if "claude" in (p.name() or "").lower():
                    return p.pid
            except Exception:
                continue
    except Exception:
        pass
    return None


def _proc_session_id(pid):
    """Read the inherited Claude/Codex session id for a known process.
    ★比父链祖先 pid 更稳:env 在进程创建时固定,【不受 harness 背景 shell 父链偶发断裂影响】
    (reboot-hello 记的坑:背景进程 parent-walk 偶发够不到 claude.exe → _proc_claude_ancestor 误返
    None → 曾把同会话 wait 误判跨会话杀掉)。Windows 上 p.environ() 对同用户进程通常可读;
    读不到/无权限/进程没了返 None(调用方回退父链判据)。"""
    if psutil is None:
        return None
    try:
        env = psutil.Process(pid).environ() or {}
        kind, _host_pid = _proc_host_ancestor(pid)
        return _host_session_id(kind, env)
    except Exception:
        return None


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
