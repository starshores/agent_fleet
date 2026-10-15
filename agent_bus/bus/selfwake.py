# -*- coding: utf-8 -*-
"""bus.selfwake —— 事件自醒在岗判定 · park 休眠 · wait 进程探活 · 进程祖先集(中层)。

依赖:config(BUS)· proc(_proc_scan/_age_seconds)· psutil。不依赖任何 cmd_* / 上层。
自醒三态化(2026-07-08):armed 在岗 / recent 刚fire待重挂(瞬时) / stale 掉线 / off 未醒 / parked 休眠。
"""
import json, os, time

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS
from bus.proc import _proc_scan, _age_seconds
from bus.proc import _host_pid_alive
from bus.codex_recovery import requeue_blocked, fence_tasks


def _wait_proc_roles():
    """挂着 `agentbus wait --role X` 进程的 role 集合(协调位事件自醒判活;复用 _proc_scan 单次扫描+缓存)。
    只认真正的 python 进程(排除 bash -c 壳——壳 cmdline 也含 agentbus wait 串且退出后可能残留)。"""
    return _proc_scan()[1]


def _reg_wait_state(role):
    """P2b(2026-08-10 用户令):读 <role>/wait.reg.json 判 role 有无【健康在册 wait】(reg 单一源·
    附宿主孤儿识别)。返回 (True, detail)=有活 wait;(False, detail)=已确认宿主退出的孤儿 wait,
    不可由进程扫描恢复为 armed;(None, None)=reg 不可用/持有者已退→调用方【回退
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
    hp = reg.get("host_pid") or reg.get("host_claude_pid")  # generic host; legacy Claude field supported
    hk = reg.get("host_kind") or ("claude" if reg.get("host_claude_pid") else None)
    if hp:
        host_alive = _host_pid_alive(hp, hk)
        if not host_alive:
            return (False, "reg 在册 wait 但宿主 %s(%s)已退=孤儿(P1 自检将自杀)" % (hk or "agent", hp))
    return (True, "会话自醒(reg 在册·wait %s 在跑)" % wp)


def _codex_messenger_evidence(role, session_id):
    """Read evidence with stable reasons; missing evidence never grants recovery."""
    if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
        return None, "messenger_session_invalid"
    path = BUS / role / "messenger.state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "messenger_state_missing"
    except (ValueError, UnicodeError):
        return None, "messenger_state_invalid"
    except OSError:
        return None, "messenger_state_unreadable"
    if not isinstance(state, dict):
        return None, "messenger_state_invalid"
    if state.get("session_id") != session_id:
        return None, "messenger_session_mismatch"
    return state, None


def _codex_messenger_diagnostic(role):
    """Read-only evidence diagnosis for a role already declared as Codex."""
    try:
        presence = json.loads((BUS / role / "presence.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(presence, dict) or presence.get("host_kind") != "codex":
        return None
    _, reason = _codex_messenger_evidence(role, presence.get("host_session_id"))
    return reason


def _codex_messenger_state(role, session_id):
    """A live bus wait alone cannot establish Codex model delivery."""
    state, reason = _codex_messenger_evidence(role, session_id)
    if reason == "messenger_session_mismatch":
        return ("stale", "Codex 信使报活属于其他会话 [%s]" % reason)
    if reason:
        return ("recent", "Codex 信使投递状态不可用，尚未确认就绪 [%s]" % reason)
    age = _age_seconds(state.get("heartbeat", ""))
    if age is None or age < 0 or age > 35:
        return ("stale", "Codex 信使报活超期")
    pid = state.get("pid")
    try:
        cl = psutil.Process(int(pid)).cmdline() if psutil is not None else []
        if not ("--session-id" in cl and session_id in cl
                and any(os.path.basename(str(x)).lower() == "codex_messenger.py" for x in cl)):
            return ("stale", "Codex 信使进程已退或 PID 被复用")
    except Exception:
        return ("stale", "Codex 信使进程已退")
    if state.get("status") != "armed":
        return ("recent", "Codex 信使在报活但投递链未就绪(%s)" % state.get("status"))
    if not (state.get("wait_alive") and state.get("app_ready") and state.get("pump_alive")):
        return ("recent", "Codex 信使在报活但投递链不完整")
    return ("armed", "Codex 信使在岗(pid %s·报活 %ss·wait/App Server 就绪)" % (pid, int(age)))


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
    return _verdict_body(role, pf, wait_roles)


def _inbox_backlog(role, min_age_s=None):
    """role inbox 里【未领且已放置 ≥min_age_s】的消息 → (条数, 最老小时)。用于「忙而聋」判定(2026-09-24 实证:
    turn 活跃 15h 却 inbox 积压 7 条=有 turn 无 bus 动作=收不到协作消息;守卫催单投 inbox 同样无人读)。
    min_age_s 默认 config deaf_inbox_min(30 分钟)。读失败 (0, 0)。"""
    try:
        from bus.config import cfg
        if min_age_s is None:
            min_age_s = float(cfg("deaf_inbox_min", 30)) * 60
        now = time.time(); n = 0; oldest = 0.0
        for p in (BUS / role / "inbox").glob("*.json"):
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
                ts = time.mktime(time.strptime(str(o.get("created", ""))[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                continue
            age = now - ts
            if age >= min_age_s:
                n += 1; oldest = max(oldest, age)
        return n, round(oldest / 3600, 1)
    except Exception:
        return 0, 0.0


def _is_orphan_claim(role, obj, now=None, min_s=None, turn_ts=None):
    """单条 processing 消息是否悬单(④型·Master数分 d21617 判据·2026-09-24):
    claimed 之后该 role【无任何 turn 活动】(turn_state.ts <= claimed 或无 turn_state)且距 claimed ≥ orphan_no_turn_min(默认 30)分钟
    = wait 领了但输出没送到会话(harness 回收/不再通知)。★不能用 claim_pid 存活判(claim_pid=wait 自身·正常 claim 后即退=恒亡·判据反了会把在办消息退回=双处理)。
    turn_ts 可由调用方预读传入(批量时省 IO)。"""
    if requeue_blocked(role, obj):
        return False
    try:
        from bus.config import cfg
        if min_s is None:
            min_s = float(cfg("orphan_no_turn_min", 30)) * 60
        now = now or time.time()
        cl = str(obj.get("claimed") or "")[:19]
        if not cl:
            return False
        cl_ts = time.mktime(time.strptime(cl, "%Y-%m-%dT%H:%M:%S"))
        if now - cl_ts < min_s:
            return False
        if turn_ts is None:
            try:
                turn_ts = (json.loads((BUS / role / "turn_state.json").read_text(encoding="utf-8")).get("ts") or "")[:19]
            except Exception:
                turn_ts = ""
        return not (turn_ts and turn_ts > cl)
    except Exception:
        return False


def _orphan_claims(role):
    """processing 里的悬单列表(见 _is_orphan_claim)。只判 mode=listening 的会话角色;daemon(worker 跑 claude -p·turn_state 不由 hook 盖)不判·交 reap ttl 兜。"""
    out = []
    try:
        pr = json.loads((BUS / role / "presence.json").read_text(encoding="utf-8"))
        if pr.get("mode") != "listening":
            return out
    except Exception:
        return out
    try:
        turn_ts = (json.loads((BUS / role / "turn_state.json").read_text(encoding="utf-8")).get("ts") or "")[:19]
    except Exception:
        turn_ts = ""
    now = time.time()
    try:
        for p in (BUS / role / "processing").glob("*.json"):
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if _is_orphan_claim(role, o, now=now, turn_ts=turn_ts):
                out.append(p)
    except Exception:
        pass
    return out


def _requeue_orphans(role):
    """悬单退回 inbox(④型真修·Master数分 15b5e5 a):新 wait 起手调用→自己立刻能领到;reap 周期也调用。
    先原地改写再 os.replace 原子搬回(任一时刻文件只在一处)。返回退回条数。"""
    n = 0
    for p in _orphan_claims(role):
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
            if requeue_blocked(role, o):
                fence_tasks(role, [o], "orphan_requires_reconcile")
                continue
            o["status"] = "pending"; o["requeue_count"] = int(o.get("requeue_count", 0)) + 1
            o["requeued_at"] = time.strftime("%Y-%m-%dT%H:%M:%S"); o["requeue_why"] = "claimed后无turn活动≥阈值(④型悬单·输出未送达会话)"
            o.pop("claimed", None); o.pop("claim_pid", None); o.pop("claim_ppid", None)
            p.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(str(p), str(BUS / role / "inbox" / p.name)); n += 1
        except Exception:
            pass
    return n


def _deaf_tag(role):
    """turn 活跃但 inbox 积压 → '⚠忙而聋:inbox积压N条(最老Xh)·无wait=收不到消息' ;无积压 → ''。"""
    n, h = _inbox_backlog(role)
    tag = ("⚠忙而聋:inbox积压%d条(最老%sh)·无wait=收不到协作消息·任务间隙请 ab peek 排空+重挂" % (n, h)) if n else ""
    o = len(_orphan_claims(role))
    if o:
        tag = (tag + "·" if tag else "") + "⚠悬单%d(claimed后无turn≥阈值=④型·重挂wait即自动退回inbox)" % o
    return tag


def _turn_busy(role, max_age=900):
    """role 的 turn 是否活跃干活中(turn_state.json 由 hook 盖:PreToolUse/thinking 等)。
    state∈{tool,thinking} 且 ts 距今 <max_age(默认 15min·长工具期间 ts 不刷新故放宽)→
    返回 '跑工具:Bash·age 42s' 类描述;否则 None。读失败 None(fail-quiet 回落原判定)。"""
    try:
        t = json.loads((BUS / role / "turn_state.json").read_text(encoding="utf-8"))
        if t.get("state") not in ("tool", "thinking"):
            return None
        age = _age_seconds(t.get("ts", ""))
        if age is None or age >= max_age:
            return None
        return "%s·turn_ts %ss前" % (t.get("label") or t.get("state"), int(age))
    except Exception:
        return None


def _verdict_body(role, pf, wait_roles):
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
    messenger = None
    if pr.get("host_kind") == "codex":
        messenger = _codex_messenger_state(role, pr.get("host_session_id"))
    # 非 daemon:协调位/会话自醒 = 独立 wait 进程(不刷心跳)→ 优先按进程判活
    # P2b(2026-08-10 用户令):判据优先读 reg(单一源·附宿主孤儿识别)→ 回退 psutil wait_roles(兼容无 reg)
    reg_armed, reg_detail = _reg_wait_state(role)
    if reg_armed is False:
        # A live wait PID does not restore its dead host. Only unavailable
        # registration (None) may use the legacy process-scan fallback.
        return ("stale", reg_detail)
    if messenger is not None and messenger[0] != "armed":
        return messenger
    if reg_armed:
        return messenger if messenger is not None else ("armed", reg_detail)
    if wait_roles is None:
        wait_roles = _wait_proc_roles()
    if role in wait_roles:
        return messenger if messenger is not None else ("armed", "会话自醒(后台 wait 进程在跑·无 reg)")
    if mode == "listening":
        kind = pr.get("host_kind") or ("claude" if pr.get("claude_pid") else None)
        pid = pr.get("host_pid") or pr.get("claude_pid") or pr.get("codex_pid")
        alive = _host_pid_alive(pid, kind)
        if alive and fresh:
            deaf = _deaf_tag(role)          # 2026-09-24:刚 fire 待重挂态也要露出 悬单/聋(④型悬单恰在此态出现)
            if deaf:
                return ("recent", "有心跳无 wait 进程但 %s(心跳 %ss)" % (deaf, age))
            return ("recent", "有心跳无 wait 进程(刚 fire 待重挂/交互中·非 event-armed·心跳 %ss)" % age)
        if alive:
            # 交叉 turn_state(2026-09-17):活跃干活中(turn=tool/thinking·ts 较新)不算掉线——
            # 干活期不挂 wait 是正常态,误标 ⚠掉线 会误导协调位发拉活/催单(实证:Master 对
            # 示例组员2 turn=tool·cpu 6% 活跃中误发拉活)。真卡死判据=turn ts 长期不动或进程退。
            ts_state = _turn_busy(role)
            if ts_state:
                deaf = _deaf_tag(role)          # 2026-09-24:干活期正常态 ≠ 忙而聋(inbox 积压=协作断线·如实标出)
                if deaf:
                    return ("recent", "turn 活跃但 %s(%s)" % (deaf, ts_state))
                return ("recent", "turn 活跃干活中(%s·无 wait=干活期正常态·非掉线)" % ts_state)
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
