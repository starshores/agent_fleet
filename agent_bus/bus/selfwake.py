# -*- coding: utf-8 -*-
"""bus.selfwake —— 事件自醒在岗判定 · park 休眠 · wait 进程探活 · 进程祖先集(中层)。

依赖:config(BUS)· proc(_proc_scan/_age_seconds)· psutil。不依赖任何 cmd_* / 上层。
自醒三态化(2026-07-08):armed 在岗 / recent 刚fire待重挂(瞬时) / stale 掉线 / off 未醒 / parked 休眠。
"""
import json, os

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS
from bus.proc import _proc_scan, _age_seconds


def _wait_proc_roles():
    """挂着 `agentbus wait --role X` 进程的 role 集合(协调位事件自醒判活;复用 _proc_scan 单次扫描+缓存)。
    只认真正的 python 进程(排除 bash -c 壳——壳 cmdline 也含 agentbus wait 串且退出后可能残留)。"""
    return _proc_scan()[1]


def _reg_wait_state(role):
    """P2b(2026-08-10 用户令):读 <role>/wait.reg.json 判 role 有无【健康在册 wait】(reg 单一源·
    附宿主孤儿识别)。返回 (True, detail)=有活 wait;(None, None)=reg 不可用/持有者已退→调用方【回退
    psutil wait_roles】(兼容无 reg 的 wait·过渡期 P1 前遗留)。
    ★验 reg.wait_pid 确是 agentbus wait 进程(cmdline·只查这一个 pid=便宜)防 pid 复用误判在岗;
     宿主 claude 死=孤儿(P1 宿主自检将自杀·如实标注,不假报健康在岗)。"""
    if psutil is None:
        return (None, None)
    try:
        reg = json.loads((BUS / role / "wait.reg.json").read_text(encoding="utf-8"))
    except Exception:
        return (None, None)
    wp = reg.get("wait_pid")
    if not wp:
        return (None, None)
    try:
        cl = psutil.Process(wp).cmdline()
        if "wait" not in cl or not any("agentbus" in str(x) for x in cl):
            return (None, None)          # pid 复用成非 wait / 持有者已退 → reg 陈旧,回退 psutil
    except Exception:
        return (None, None)              # 进程没了 → reg 陈旧,回退
    hp = reg.get("host_claude_pid")      # 宿主孤儿识别
    if hp:
        try:
            host_alive = "claude" in (psutil.Process(hp).name() or "").lower()
        except Exception:
            host_alive = False
        if not host_alive:
            return (True, "reg 在册 wait 但宿主 claude(%s)已退=孤儿(P1 自检将自杀)" % hp)
    return (True, "会话自醒(reg 在册·wait %s 在跑)" % wp)


def _parked_path(role):
    return BUS / role / "parked.json"


def _is_parked(role):
    """role 是否【故意暂离(park/休眠)】——用户手动关 tab 省内存时标,与崩溃掉线区分。
    返回 parked dict(since/days/until/note)或 None。轻量·独立 marker 文件·外部可写(role 关了也能标)。"""
    p = _parked_path(role)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"since": "?"}


def _park_expired(pk):
    """park 若带 --days 到期(until 已过)→ True(guard 可温和提醒一次·非掉线报警)。"""
    if not pk or not pk.get("until"):
        return False
    age = _age_seconds(pk.get("until"))
    return age is not None and age > 0


def _guard_exempt_path(role):
    return BUS / role / "guard_exempt.json"


def _is_guard_exempt(role):
    """role 是否【自愿豁免自醒巡检】——轮询模式在岗者(如 ScheduleWakeup 定时 drain bus·非 event-armed
    background wait)。这类 agent 心跳必然陈旧(轮询间隔>心跳阈值),但【并非掉线】:任务在 bus 排队、
    它每轮 drain 取到,不丢。与 parked(故意暂离·不收任务)区别=exempt 仍在岗收任务、只是自醒方式不同。
    返回 exempt dict(mode/reason/since/note)或 None。marker 文件外部可写=agent 自行声明。
    起因:2026-07-16 SQLAGent 请求(background wait 在对话 turn 边界被 SIGTERM·无法跨 turn 常驻心跳→
    改 270s 轮询→守卫每周期误判掉线发[请重挂]=双方空耗)。"""
    p = _guard_exempt_path(role)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"mode": "polling"}


def _self_wake_state(role, alive_pids=None, wait_roles=None):
    """判一个 role 的事件自醒在不在岗。返回 (state, detail)。
    parked=🅿️休眠(用户故意暂离·独立态·不计掉线/不报警·与 stale 严格区分);
    armed=在岗(有真 wait 进程);recent=有心跳无 wait 进程(刚 fire 待重挂/交互中·非 event-armed·瞬时态非告警);
    stale=掉线(曾自醒但进程退/没重挂·心跳超期);off=未自醒(idle/无 presence)。
    daemon 按 daemon_pid 判活;协调位/会话按【有无活的 wait 进程】判活(wait 不刷心跳,不能靠心跳)。
    三态化(2026-07-08 Master批):原"listening+心跳新鲜"误报 armed(按心跳判)→ 改判 recent(如实=非 event-armed),
    消除虚报✓在岗;event 自醒真正在岗只认 wait 进程。
    wait_roles:预扫好的"有 wait 进程的 role 集合"(循环调用务必传入,免每 role 全扫);None 则现扫一次。"""
    pk = _is_parked(role)
    if pk:   # 故意暂离优先短路:不管 presence 死活都判 parked(不误报 stale/掉线)
        ext = "·已到期待unpark" if _park_expired(pk) else ((("·%s天" % pk.get("days")) if pk.get("days") else ""))
        note = ("·" + pk["note"]) if pk.get("note") else ""
        return ("parked", "🅿️休眠(用户故意暂离%s%s)" % (ext, note))
    ex = _is_guard_exempt(role)
    if ex:   # 轮询模式在岗:自愿豁免事件自醒巡检(心跳必陈旧非掉线·任务 bus 排队定时 drain 取)
        note = ("·" + ex["note"]) if ex.get("note") else ""
        return ("polling", "⏱轮询模式在岗(定时 drain·豁免巡检%s)" % note)
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return ("off", "无 presence")
    try:
        pr = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return ("off", "presence 损坏")
    mode = pr.get("mode")
    age = _age_seconds(pr.get("last_seen", ""))
    wt = pr.get("wait_timeout") or 110
    fresh = (age is not None and age <= wt + 60)
    if mode == "daemon":
        dp = pr.get("daemon_pid")
        if psutil is not None and dp and psutil.pid_exists(dp):
            return ("armed", "daemon 自醒(watchdog 阻塞)")
        return ("stale", "daemon 已退,自醒掉线")
    # 非 daemon:协调位/会话自醒 = 独立 wait 进程(不刷心跳)→ 优先按进程判活
    # P2b(2026-08-10 用户令):判据优先读 reg(单一源·附宿主孤儿识别)→ 回退 psutil wait_roles(兼容无 reg)
    reg_armed, reg_detail = _reg_wait_state(role)
    if reg_armed:
        return ("armed", reg_detail)
    if wait_roles is None:
        wait_roles = _wait_proc_roles()
    if role in wait_roles:
        return ("armed", "会话自醒(后台 wait 进程在跑·无 reg)")
    if mode == "listening":
        pid = pr.get("claude_pid")
        alive = bool(psutil is not None and pid and psutil.pid_exists(pid))
        if alive and fresh:
            return ("recent", "有心跳无 wait 进程(刚 fire 待重挂/交互中·非 event-armed·心跳 %ss)" % age)
        if alive:
            return ("stale", "会话在但无 wait 进程、心跳超期 %ss——自醒掉线(没重挂?)" % age)
        return ("stale", "进程已退,自醒掉线")
    return ("off", "未自醒(mode=%s)" % mode)


def _ancestor_pids():
    """本进程(whoami/hook 子进程)向上的祖先 pid 集,用于按 claude_pid 反查绑定 role。"""
    pids = set()
    if psutil is None:
        return pids
    try:
        p = psutil.Process(os.getpid())
        for anc in p.parents():
            pids.add(anc.pid)
    except Exception:
        pass
    return pids
