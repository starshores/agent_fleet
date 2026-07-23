# -*- coding: utf-8 -*-
"""bus.core —— 消息任务流核心:send / wait / done / request / serve / peek / replies + 目标可达校验。
全员自醒命脉(wait/done/hello 每回合 shell)。依赖下层全部 bus 模块;无环(mailbox/pool 领取原语已下沉)。
_wait_core 内部按需 import watchdog(随函数走)。"""
import os, sys, json, time, threading, socket, subprocess

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, METRICS, cfg, _now, _idle_timeout_policy
from bus.io import _gen_id, _write_json, _oldest, _audit, record_metric, read_metrics
from bus.roster import (_agents_file, _load_agents, _save_agents, _role_dir, _list_roles,
                        _retired_roles, _is_test_role, _PSEUDO_ROLE_LABELS, _pseudo_role_label, HUMAN_ATTENDED)
from bus.proc import _claude_ancestor, _proc_scan, _alive_claude_pids, _age_seconds
from bus.presence import _touch_presence, _set_current_task, _pidtags_file, _load_pidtags, _owner_live
from bus.selfwake import _wait_proc_roles, _parked_path, _is_parked, _park_expired, _self_wake_state, _ancestor_pids
from bus.notify import _toast_throttled, _fire_toast, _maybe_toast, _emit_completion_notice
from bus.mailbox import _claim, _claim_and_print, _resolve_self_role, _oldest_claimable
from bus.pool import _role_groups, _group_dir, _claim_group_task, route_on_member_done


def _warn_unreachable(role):
    """目标可达校验(治【静默漏接】:派了≠接了):退役/自醒非在岗就 stderr 告警。
    测试角色不校验;协调位 Master/MasterAgent(人值守、不走 claim)豁免。send/answer 共用。"""
    if _is_test_role(role):
        return
    if role in _retired_roles():
        sb = _load_agents().get(role, {}).get("succeeded_by")
        sys.stderr.write("⚠ 目标 %s 已退役,活没人接——请改派%s。(仍写入留痕)\n"
                         % (role, ("(接替=%s)" % sb) if sb else ""))
    elif role not in HUMAN_ATTENDED:
        if _self_wake_state(role)[0] in ("stale", "off"):   # recent(刚fire待重挂)不警:消息入inbox它重挂即领
            sys.stderr.write("⚠ 目标 %s 自醒非在岗——派了可能没人接(claim);"
                             "换个在岗的 / 让它重挂自醒,或确认它是人值守 tab。(仍写入其 inbox)\n" % role)


# ---- (_load_agents / _save_agents / _gen_id / _write_json / _oldest / _audit /
#       record_metric / read_metrics 已抽到 bus/ 包 · 见文件头 import) ----


# ---------------- identity / presence(已抽到 bus/proc.py + bus/presence.py · 见文件头 import)----------------


# ---------------- commands ----------------



def cmd_send(args):
    role = args.to
    _role_dir(role)
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    if frm == "unknown":
        sys.stderr.write(
            "⚠ from=unknown(没带 --from 且无 AGENTBUS_ROLE):worker 完成后的回执会落进 "
            "unknown/、原请求方永远收不到=静默丢失。请带 --from <你的role> 重发。\n")
    _warn_unreachable(role)   # 校验目标在岗(治静默漏接:派了≠接了)
    tid = _gen_id(role)
    task = {
        "id": tid, "from": frm, "to": role,
        "title": args.title or "", "task": args.task,
        "created": _now(), "status": "pending",
    }
    if args.requires_approval:
        task["requires_approval"] = True   # 高风险:daemon 不执行,只交互 tab/人工批准后做
    _write_json(BUS / role / "inbox" / (tid + ".json"), task)
    _audit("send", id=tid, frm=frm, to=role, title=args.title or "")
    print(tid)


# ---------------- 任务原子领取原语(已抽到 bus/mailbox.py · 见文件头 import)----------------


def _wait_core(role, timeout, include_approval=False):
    """阻塞到 inbox 有【可领取】任务(watchdog 事件),claim 并返回 task dict;空闲超时返回 None。
    不触 presence、不打印——给 cmd_wait / cmd_serve / daemon 复用。
    include_approval=False(默认,所有自动监听者)时,requires_approval 高风险任务不会被领取,
    统一实现 requires_approval 闸(tab 与 daemon 一致),高风险任务留 inbox 等人工批准领取。"""
    inbox = _role_dir(role) / "inbox"
    groups = _role_groups(role)                       # 所属工作组(运行时·可空);无组 → 下面组循环空转 = 行为与旧完全一致
    group_inboxes = [_group_dir(g) / "inbox" for g in groups]

    def _try_claim():
        """先自己 inbox(点名/会话粘连活),再各组共享 inbox(池活·O_EXCL 并发安全)。
        返回 claim 到的 task dict,或 None(无可领 / 竞态没抢到 → 调用方续等,gate②)。"""
        t = _oldest_claimable(inbox, include_approval)
        if t:
            obj = _claim(role, t)
            if obj:
                return obj                            # _claim 返 None=竞态没抢到 → 落到组循环继续看
        for g in groups:
            obj = _claim_group_task(role, g, include_approval)
            if obj:
                return obj
        return None

    obj = _try_claim()
    if obj:
        return obj

    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    ev = threading.Event()

    class H(FileSystemEventHandler):
        def on_created(self, e):
            if str(e.src_path).endswith(".json"):
                ev.set()
        def on_moved(self, e):
            if str(getattr(e, "dest_path", "")).endswith(".json"):
                ev.set()

    obs = Observer()
    obs.schedule(H(), str(inbox), recursive=False)
    for gin in group_inboxes:                         # 双听:所属组共享 inbox 的新任务也唤醒本成员来 pull
        try:
            gin.mkdir(parents=True, exist_ok=True)
            obs.schedule(H(), str(gin), recursive=False)
        except Exception:
            pass
    # 兜底(2026-07-08 Master 提):Observer.start() 若抛异常(watchdog 装坏/资源限),原来会崩溃传播;
    # 现捕获→降级【轮询式阻塞】(每 2s 查一次 到 deadline),【绝不秒返 idle】(秒返=事件自醒失效)。
    obs_ok = True
    try:
        obs.start()
    except Exception:
        obs_ok = False
    try:
        obj = _try_claim()                            # 启动 observer 后再查一次,消除竞态
        if not obj:
            # 循环等到截止:被【无关/竞态/惊群没抢到】的事件唤醒但没领到时,清标志继续等剩余,
            # 而非直接返回 idle(一次 spurious 就空返 = 自醒被白白唤起费 token)。惊群败者走这条=优雅续等(gate②)。
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break                             # 真超时,无任务 → idle
                if obs_ok:
                    woke = ev.wait(timeout=remaining)
                    obj = _try_claim()
                    if obj:
                        break                         # 领到(自己 inbox 或组池)
                    if not woke:
                        break                         # ev.wait 超时返回(非事件)→ idle
                    ev.clear()                        # 被事件唤醒但没领到(惊群没抢到/竞态/无关)→ 续等剩余
                else:
                    time.sleep(min(remaining, 2.0))   # 降级:轮询阻塞(别秒返),≤2s 发现新任务(含组 inbox)
                    obj = _try_claim()
                    if obj:
                        break
    finally:
        if obs_ok:
            try:
                obs.stop(); obs.join()
            except Exception:
                pass

    return obj


def _wait_for_reply(from_role, tid, timeout):
    """阻塞到 <from_role>/done/<tid>.json 出现(被请求方的 done 已按 from 路由到这),读出并消费。
    消费(unlink)是为了不污染 from_role 的 replies —— 同步应答不该再被当成异步回填堆在 done/。
    复用 _wait_core 同款 watchdog 模式,只是改成等 done/ 下的【特定文件名】。"""
    done_dir = _role_dir(from_role) / "done"
    target = done_dir / (tid + ".json")

    def _read_consume():
        try:
            obj = json.loads(target.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": "bad reply: %s" % e}
        try:
            target.unlink()
        except Exception:
            pass
        return obj

    if target.exists():
        return _read_consume()

    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    ev = threading.Event()
    suffix = tid + ".json"

    class H(FileSystemEventHandler):
        def on_created(self, e):
            if str(e.src_path).endswith(suffix):
                ev.set()
        def on_moved(self, e):
            if str(getattr(e, "dest_path", "")).endswith(suffix):
                ev.set()

    obs = Observer()
    obs.schedule(H(), str(done_dir), recursive=False)
    obs.start()
    try:
        if target.exists():            # 起 observer 后再查一次,消竞态(同 _wait_core)
            return _read_consume()
        ev.wait(timeout=timeout)
    finally:
        obs.stop(); obs.join()

    return _read_consume() if target.exists() else None


def _kill_prior_waits(role):
    """per-role 幂等:挂 wait 前清掉【该 role 已存活的独立 agentbus wait 进程】(排除自己)。
    防 naive 重挂累积多 wait 抢同一 notice→即时空唤醒死循环(Master数分 2026-07-02 提:全队防坑,不靠各 agent 纪律)。
    只杀独立 `agentbus.py wait --role <role>` 的 python 进程;daemon 的 wait 在 worker_daemon 进程内跑(cmdline 不同)不受影响。
    正常流(旧 wait 已 fire 退出)扫不到东西=无副作用;只有真累积(旧 wait 仍阻塞)时才清,是纪律滑坡的安全网。返回清掉数。"""
    if psutil is None:
        return 0
    me = os.getpid(); killed = 0
    for p in psutil.process_iter(["name"]):
        try:
            if p.pid == me or "python" not in (p.info.get("name") or "").lower():
                continue
            cl = p.cmdline()
            if "wait" not in cl or not any("agentbus" in str(x) for x in cl):
                continue
            for i, x in enumerate(cl):
                if x == "--role" and i + 1 < len(cl) and cl[i + 1] == role:
                    p.kill(); killed += 1
                    break
        except Exception:
            continue
    return killed


# 维护哨兵:稳定路径(agent_bus 之外),bus 计划内停摆(如目录迁移)时置位;wait 见之原地睡等。
_MAINT_SENTINEL = BUS.parent / ".bus_maintenance"


def _serve_wait(role, timeout, presence_mode="listening", presence_extra=None, include_approval=False):
    # ── 中断 / 自恢复机制(用户 2026-07-15 令·好设计):遇不可恢复问题不崩溃、不空转,睡 5 分钟再自恢复 ──
    # ① 维护窗口哨兵:计划内停摆(bus 目录迁移等)期间【原地睡等,绝不 mkdir 重建 bus】——
    #    否则 _role_dir/_touch_presence 会在被搬走的路径上重建出一个空 bus、分裂状态。哨兵清除后继续正常 wait。
    waited = 0
    while _MAINT_SENTINEL.exists() and waited < 600:      # 最多守 10 分钟维护窗口
        if waited == 0:
            sys.stderr.write("🛠 bus 维护窗口(.bus_maintenance)——原地睡等,不重建 bus...\n")
        time.sleep(15); waited += 15

    _kill_prior_waits(role)   # per-role 幂等:先清同 role 旧 wait,防累积(Master数分 2026-07-02)
    extra = dict(presence_extra or {}); extra["wait_timeout"] = timeout
    try:
        _touch_presence(role, presence_mode, extra)       # 声明身份 + 心跳
        obj = _wait_core(role, timeout, include_approval)
    except Exception as e:
        # ② 不可恢复问题(bus 路径暂失 / 迁移中 / 未预期异常):睡 300s 再自恢复一次,绝不崩溃/秒返空转。
        #    睡眠期间后台 wait 仍"在跑"=agent 保持在岗、透明熬过瞬时故障,恢复后自动接上。
        sys.stderr.write("⚠ wait 遇不可恢复问题:%s → 睡 300s 再自恢复重试...\n" % (str(e)[:150]))
        time.sleep(300)
        try:
            _touch_presence(role, presence_mode, extra)
            obj = _wait_core(role, timeout, include_approval)
        except Exception as e2:
            sys.stderr.write("⚠ 自恢复后仍异常:%s —— 返回 idle,交下一轮重挂。\n" % (str(e2)[:150]))
            obj = None
    print(json.dumps(obj if obj is not None else {"idle": True, "role": role}, ensure_ascii=False))


def cmd_wait(args):
    # --include-approval:有人值守的 tab 显式领取 requires_approval 高风险任务(默认不领,留 inbox 待审)
    to = args.timeout
    if to is None or to <= 0:               # 哨兵 --timeout 0 → 日/夜 idle 策略(日2h/夜6h·真事件仍即时接活)
        to = _idle_timeout_policy()
    _serve_wait(args.role, to, include_approval=args.include_approval)


def cmd_serve(args):
    """通用 worker 入口:自动认出本 tab 的 role(需先 hello 过一次),再阻塞等任务。"""
    role = _resolve_self_role()
    if not role:
        print(json.dumps({"error": "未识别本 tab 身份,请先运行一次:agentbus hello --role <本tab名>"}, ensure_ascii=False))
        return
    _serve_wait(role, args.timeout)


def cmd_request(args):
    """请求-应答原语(ask-and-await):给 B 发子任务并【阻塞等 B 的应答】,按 task id 关联。
    底层就是 send + 等 <from>/done/<id>(done 读任务 from 已把结果路由回这,无需改 done)。
    daemon 模式下 B 的 claude -p 执行中可直接 shell 调 request 去"问 C 并等"。
    死锁/嵌套三层防范:① 禁自请求(to==from)② chain 软环检测(目标已在调用链→拒)
    ③ 强制有限超时(硬兜底:即便环没检出,全链最终超时收敛,绝不永久挂)。
    chain 跨 claude -p 靠 worker 自觉透传 --chain '<我收到任务里的 chain,逗号拼接>'。"""
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    if args.to == frm:                       # 防护1:自请求必死锁(自己阻塞等自己)
        print(json.dumps({"error": "self-request forbidden (to==from): %s" % frm}, ensure_ascii=False)); return
    chain = [c for c in (args.chain.split(",") if args.chain else []) if c]
    if args.to in chain:                     # 防护2:目标已在调用链上 → 环(A→B→A、A→B→C→B 都挡)
        print(json.dumps({"error": "cycle detected: %s already in chain %s" % (args.to, chain)}, ensure_ascii=False)); return
    _role_dir(args.to)
    tid = _gen_id(args.to)
    task = {
        "id": tid, "from": frm, "to": args.to,
        "title": args.title or "", "task": args.task,
        "created": _now(), "status": "pending",
        "reply_mode": "sync", "chain": chain + [frm],   # 把自己加进 chain 往下传
    }
    _write_json(BUS / args.to / "inbox" / (tid + ".json"), task)
    # 防护3:阻塞等应答,强制有限超时
    rep = _wait_for_reply(frm, tid, args.timeout)
    print(json.dumps(rep if rep is not None else {"timeout": True, "id": tid, "to": args.to}, ensure_ascii=False))


def _find_task(role, tid):
    for sub in ("processing", "inbox"):
        p = BUS / role / sub / (tid + ".json")
        if p.exists():
            return p
    # 容错:扫一遍
    for sub in ("processing", "inbox"):
        for p in (BUS / role / sub).glob("*.json"):
            if p.stem == tid or p.stem.startswith(tid):
                return p
    return None


# ---------------- 完成通知(done 端 best-effort WinRT toast)----------------
# 约束:Master/人是 idle 交互会话,推不进去;唯一醒着的接收者是【人】。
# 所以"通知 Master"=给在场的人弹一条 OS 事件 toast(契合全局规则:完成走事件通知不轮询),
# 让人去碰 Master / 开 WebUI。toast 由 done 端发(完成时刻,tab/daemon 都会跑),0 token、非阻塞。
# (PushNotification 是会话内建工具,done 子进程调不到 → 用 WinRT 模板 AppID ClaudeCode.NotifyTest。)
# ---------------- 通知(toast / 完成通知触发自醒)已抽到 bus/notify.py · 见文件头 import ----------------


def cmd_done(args):
    role = args.role or _resolve_self_role()
    if not role:
        print("ERR: 未指定 --role 且无法识别本 tab 身份(先 hello)"); return
    tid = args.id
    p = _find_task(role, tid)
    sender = "unknown"; title = ""
    real_id = tid
    reply_mode = None; task_kind = None; no_receipt = False; route_grp = None
    if p:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            sender = obj.get("from", "unknown"); title = obj.get("title", ""); real_id = obj.get("id", tid)
            reply_mode = obj.get("reply_mode"); task_kind = obj.get("kind"); no_receipt = obj.get("no_receipt", False)
            route_grp = obj.get("route_group")   # 路由任务留痕:done 后触发该组补投(成员空出=事件驱动)
        except Exception:
            pass
    if getattr(args, "to", None):     # 显式指定回执目标——任务已被归档/reap/移走、_find_task 找不到原 from 时用
        sender = args.to
    if sender == "unknown":           # 治【done 回执落 unknown 静默丢失】(如:某业务 agent 的在飞任务被清 stuck 时归档,它再 done 就找不到 from)
        sys.stderr.write("⚠ done: 找不到任务 %s 的请求方(任务可能已被归档/reap/移走)→回执将落 unknown/、原请求方收不到。"
                         "若知道请求方,加 --to <role> 重发本 done。\n" % tid)
    result = {
        "id": real_id, "from": role, "to": sender, "title": title,
        "status": args.status, "result": args.result, "finished": _now(),
    }
    _role_dir(sender)
    _write_json(BUS / sender / "done" / (real_id + ".json"), result)
    if p:
        arch = BUS / role / "archive" / p.name
        try:
            obj["status"] = args.status; obj["result"] = args.result; obj["finished"] = _now()
            _write_json(arch, obj); p.unlink()
        except Exception:
            try: os.replace(p, arch)
            except Exception: pass
    _set_current_task(role, None, None)  # A3:完成后清"当前在做"
    _audit("done", id=real_id, role=role, to=sender, status=args.status)
    _maybe_toast(sender, args.status, title, args.result, args.notify, reply_mode)  # best-effort,不影响 done 结果
    # v1.1: 完成通知触发发起方【自醒】—— 若 sender 是自醒在岗的协调位,往其 inbox 投一条轻量 notice,
    # 触发其后台 wait 自醒(它醒来读 <sender>/done/<id> 取完整结果)。sync 应答/never 不投;只对协调位(防环)。
    # 防环铁律:done 一个 notice 不再生 notice(否则协调位↔协调位 done 各自的 notice 会乒乓无限循环——实测踩过)
    if reply_mode != "sync" and task_kind != "notice" and args.notify != "never" and not no_receipt:
        _emit_completion_notice(sender, role, real_id, args.status, title)
    print("done -> %s (status=%s)" % (sender, args.status))
    # 状态路由(2026-07-23):done 一条 routed 组任务=该成员空出 → 即时 drain 该组 route_queue 补投下一条(事件驱动·非轮询)
    if route_grp:
        try:
            route_on_member_done(role, real_id)
        except Exception:
            pass
    # D(2026-07-08·防自醒-loss):done 后若本 role 无 armed wait 进程 → 提示重挂(前台CLI干完活易忘)
    try:
        if role and role not in ("用户", "unknown") and role not in _wait_proc_roles():
            print("⚠ 自醒提示:你(%s)当前无 armed wait 进程,done 后请【后台 run_in_background】重挂 "
                  "`wait --role %s --timeout 0`(0=自动日2h/夜6h),否则空闲后新消息事件唤不醒你。" % (role, role))
    except Exception:
        pass


def cmd_peek(args):
    role = args.role
    d = _role_dir(role)
    rows = []
    for sub in ("inbox", "processing"):
        for p in sorted((d / sub).glob("*.json")):
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
                rows.append((sub, o.get("id", p.stem), o.get("from", "?"), o.get("title", "") or o.get("task", "")[:50]))
            except Exception:
                rows.append((sub, p.stem, "?", "(unreadable)"))
    if not rows:
        print("(empty)"); return
    for sub, tid, frm, title in rows:
        print("[%s] %s  from=%s  %s" % (sub, tid, frm, title))


def cmd_replies(args):
    role = args.role
    d = _role_dir(role)
    done = d / "done"
    files = sorted(done.glob("*.json"))
    if not files:
        print("(no replies)"); return
    for p in files:
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
            print("== %s  from=%s  status=%s  (%s)" % (o.get("id"), o.get("from"), o.get("status"), o.get("title", "")))
            print(o.get("result", ""))
            print("-" * 60)
        except Exception:
            print("(unreadable)", p.name)
    if args.clear:
        for p in files:
            try: p.unlink()
            except Exception: pass
        print("[cleared %d replies]" % len(files))
