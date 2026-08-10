# -*- coding: utf-8 -*-
"""bus.core —— 消息任务流核心:send / wait / done / request / serve / peek / replies + 目标可达校验。
全员自醒命脉(wait/done/hello 每回合 shell)。依赖下层全部 bus 模块;无环(mailbox/pool 领取原语已下沉)。
watchdog 监听走公共样板 bus.watch.arm_watch(P1 抽公共)。"""
import os, sys, json, time, threading, socket, subprocess

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, METRICS, cfg, _now, _idle_timeout_policy
from bus.io import _gen_id, _write_json, _oldest, _audit, record_metric, read_metrics, _fail
from bus.roster import (_agents_file, _load_agents, _save_agents, _role_dir, _list_roles,
                        _retired_roles, _is_test_role, _PSEUDO_ROLE_LABELS, _pseudo_role_label, HUMAN_ATTENDED)
from bus.proc import _claude_ancestor, _proc_claude_ancestor, _proc_session_id, _proc_scan, _alive_claude_pids, _age_seconds
from bus.presence import _touch_presence, _set_current_task, _pidtags_file, _load_pidtags, _owner_live
from bus.selfwake import _wait_proc_roles, _parked_path, _is_parked, _park_expired, _self_wake_state, _ancestor_pids
from bus.notify import _toast_throttled, _fire_toast, _maybe_toast, _emit_completion_notice
from bus.mailbox import _claim, _claim_and_print, _resolve_self_role, _oldest_claimable
from bus.watch import arm_watch
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



def _deliver(to, frm, task, audit_event="send"):
    """统一投递契约(P1·2026-07-28):目录保障 + from=unknown 警示 + 可达告警 + 培训闸软警示
    + 写 inbox + 审计。send/request 共用——request 原来径写 inbox 绕过全部四层
    (events.jsonl 里不可见、目标掉线/待训无警示、from=unknown 静默丢回执)。task 需含 id/title。"""
    _role_dir(to)
    if frm == "unknown":
        sys.stderr.write(
            "⚠ from=unknown(没带 --from 且无 AGENTBUS_ROLE):worker 完成后的回执会落进 "
            "unknown/、原请求方永远收不到=静默丢失。请带 --from <你的role> 重发。\n")
    _warn_unreachable(to)   # 校验目标在岗(治静默漏接:派了≠接了)
    if (_load_agents().get(to) or {}).get("trained") is False:   # 培训闸·软警示(新角色未结业)
        sys.stderr.write("⚠ 目标 %s 处于🎓待训期(新注册·培训师未 train-done):简单明确活可派;"
                         "运维/写库/改基建类复杂活请等结业,或在任务里写明验证清单。(仍投递)\n" % to)
    _write_json(BUS / to / "inbox" / (task["id"] + ".json"), task)
    _audit(audit_event, id=task["id"], frm=frm, to=to, title=task.get("title") or "")


def cmd_send(args):
    role = args.to
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    tid = _gen_id(role)
    task = {
        "id": tid, "from": frm, "to": role,
        "title": args.title or "", "task": args.task,
        "created": _now(), "status": "pending",
    }
    if args.requires_approval:
        task["requires_approval"] = True   # 高风险:daemon 不执行,只交互 tab/人工批准后做
    _deliver(role, frm, task)
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

    # 公共 watchdog 样板(P1 抽公共·bus/watch.py):监听自己 inbox + 所属组共享 inbox(双听)。
    # 兜底(2026-07-08 Master 提):Observer 起不来(watchdog 装坏/资源限)ok=False
    # → 降级【轮询式阻塞】(每 2s 查一次到 deadline),【绝不秒返 idle】(秒返=事件自醒失效)。
    ev, _stop_watch, obs_ok = arm_watch([inbox] + group_inboxes)
    try:
        obj = _try_claim()                            # 启动 observer 后再查一次,消除竞态
        if not obj:
            # 循环等到截止:被【无关/竞态/惊群没抢到】的事件唤醒但没领到时,清标志继续等剩余,
            # 而非直接返回 idle(一次 spurious 就空返 = 自醒被白白唤起费 token)。惊群败者走这条=优雅续等(gate②)。
            # ★timeout=None = 真无限阻塞(block 语义·Master数分 spec 2026-08-10):ev.wait(None) 永久等到事件,
            #   零周期空返、无 OverflowError(别传巨大秒数,>2^31-1 ms≈24.8天 底层溢出)。无限时不算 deadline。
            infinite = timeout is None
            deadline = None if infinite else time.monotonic() + timeout
            while True:
                if not infinite:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break                         # 真超时,无任务 → idle
                if obs_ok:
                    woke = ev.wait(timeout=(None if infinite else remaining))
                    obj = _try_claim()
                    if obj:
                        break                         # 领到(自己 inbox 或组池)
                    if not woke:                      # ev.wait 超时返回(非事件)
                        if infinite:
                            ev.clear(); continue      # 无限:理论只在 set 时返回,防御性续等(绝不空返)
                        break                         # 有限超时 → idle
                    ev.clear()                        # 被事件唤醒但没领到(惊群没抢到/竞态/无关)→ 续等剩余
                else:
                    time.sleep(2.0 if infinite else min(remaining, 2.0))  # 降级:轮询阻塞(别秒返),≤2s 发现新任务
                    obj = _try_claim()
                    if obj:
                        break
    finally:
        _stop_watch()

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

    ev, _stop_watch, obs_ok = arm_watch([done_dir], suffix=tid + ".json")   # 公共样板(P1)
    try:
        if target.exists():            # 起 observer 后再查一次,消竞态(同 _wait_core)
            return _read_consume()
        if obs_ok:
            ev.wait(timeout=timeout)
        else:                          # 降级:轮询阻塞等目标文件(别秒返)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not target.exists():
                time.sleep(1.0)
    finally:
        _stop_watch()

    return _read_consume() if target.exists() else None


def _prior_wait_procs(role):
    """扫【该 role 已存活的独立 agentbus wait 进程】(排除自己;只认 name=python 的真 wait,
    bash 包装壳/daemon 内嵌 wait cmdline 不匹配、天然排除)。返回 psutil.Process 列表。"""
    if psutil is None:
        return []
    me = os.getpid(); found = []
    for p in psutil.process_iter(["name"]):
        try:
            if p.pid == me or "python" not in (p.info.get("name") or "").lower():
                continue
            cl = p.cmdline()
            if "wait" not in cl or not any("agentbus" in str(x) for x in cl):
                continue
            for i, x in enumerate(cl):
                if x == "--role" and i + 1 < len(cl) and cl[i + 1] == role:
                    found.append(p)
                    break
        except Exception:
            continue
    return found


def _kill_prior_waits(role):
    """(takeover 路径)清掉该 role 已存活的旧 wait。⚠ 不再是重挂默认——默认走 yield-not-kill
    (见 _serve_wait):杀旧会触发 kill-churn 自持振荡(2026-08-03 业务侧 ~15s 热循环实证:
    新挂杀旧 → 旧 wait 后台任务"完成"(空输出)→ harness 唤醒会话 → 纪律重挂 → 又杀刚挂的 → 循环)。
    仅 serve(前台执行体显式接管)与 wait --takeover 走此路径。返回清掉数。"""
    killed = 0
    for p in _prior_wait_procs(role):
        try:
            p.kill(); killed += 1
        except Exception:
            continue
    return killed


# 维护哨兵:稳定路径(agent_bus 之外),bus 计划内停摆(如目录迁移)时置位;wait 见之原地睡等。
_MAINT_SENTINEL = BUS.parent / ".bus_maintenance"

# wait 阻塞期心跳间隔(会话考古上报 2026-07-29:原来只在 _serve_wait 进场 touch 一次,
# 2h/6h 阻塞期 last_seen 不刷新 → who 心跳持续老化像掉线)。presence 多写者安全(json_lock)。
# 2026-08-10 用户令:P1 注册表宿主自检也走这拍,间隔 120→10s(循环程序消耗低·10s 内发现宿主死够快)。
_WAIT_HB_INTERVAL = float(os.environ.get("AGENTBUS_WAIT_HB_INTERVAL", 10))

# wait 注册表文件(P1·2026-08-10 用户令·独立文件):每 role 一个,记本 wait 身份+宿主+心跳+代数。
def _wait_reg_path(role):
    return BUS / role / "wait.reg.json"


def _wait_self_exit(reg_path, my_pid, why):
    """孤儿/被接管 wait 自杀(P1 宿主自检):清掉自己持有的 reg,stderr 记因,os._exit 立即终止
    (从 daemon 心跳线程调用=连同阻塞在 ev.wait 的主线程一起终止;这正是"孤儿 wait 自己消失"的正道)。"""
    try:
        sys.stderr.write("[wait] %s\n" % why)
    except Exception:
        pass
    if reg_path is not None and my_pid is not None:
        try:
            cur = json.loads(reg_path.read_text(encoding="utf-8"))
            if cur.get("wait_pid") == my_pid:
                reg_path.unlink()
        except Exception:
            pass
    os._exit(0)


def _start_wait_heartbeat(role, mode, extra, host_pid=None, reg_path=None, my_pid=None, interval=None):
    """后台心跳线程:wait 阻塞期间每 interval 秒(默认 10s)刷 presence + reg 心跳(daemon 线程·best-effort)。
    P1 宿主自检(用户令 2026-08-10·根治孤儿):
      ① 启动宿主 claude 已退(pid 没了 / pid 复用成非 claude)→ 本 wait 是孤儿(claim 走任务也没人处理)
         → 自杀,不再抢 inbox。这比"新 wait 去杀孤儿"或"reap 兜底"都干净——孤儿自己消失。
      ② reg 已被别的 wait 覆盖(wait_pid != 我)→ 被接管 → 退出,让位单一执行体。
    host_pid=None(父链断追不到宿主)→ 跳过宿主自检(安全降级:不误杀,靠 P0 yield/reap 兜)。"""
    stop = threading.Event()

    def _beat():
        while not stop.wait(interval or _WAIT_HB_INTERVAL):
            try:
                _touch_presence(role, mode, extra)
            except Exception:
                pass                      # 心跳绝不影响 wait 主流程(bus 路径瞬失等,下一拍再试)
            # ① 宿主自检:宿主 claude 死 → 孤儿自杀(psutil 缺失则跳过,不误杀)
            if host_pid is not None and psutil is not None:
                try:
                    alive = "claude" in (psutil.Process(host_pid).name() or "").lower()
                except Exception:
                    alive = False         # pid 没了 / 读不到 → 判宿主已退
                if not alive:
                    _wait_self_exit(reg_path, my_pid, "宿主 claude(pid=%s)已退→孤儿 wait 自杀(P1)" % host_pid)
            # ② 被接管自检 + 刷 reg 心跳
            if reg_path is not None and my_pid is not None:
                try:
                    cur = json.loads(reg_path.read_text(encoding="utf-8"))
                    if cur.get("wait_pid") != my_pid:
                        _wait_self_exit(None, None, "reg 已被 wait_pid=%s 接管→本 wait 退出(P1)" % cur.get("wait_pid"))
                    cur["last_heartbeat"] = _now()
                    _write_json(reg_path, cur)
                except Exception:
                    pass

    threading.Thread(target=_beat, daemon=True).start()
    return stop


def _serve_wait(role, timeout, presence_mode="listening", presence_extra=None, include_approval=False,
                takeover=False):
    # ── 中断 / 自恢复机制(用户 2026-07-15 令·好设计):遇不可恢复问题不崩溃、不空转,睡 5 分钟再自恢复 ──
    # ① 维护窗口哨兵:计划内停摆(bus 目录迁移等)期间【原地睡等,绝不 mkdir 重建 bus】——
    #    否则 _role_dir/_touch_presence 会在被搬走的路径上重建出一个空 bus、分裂状态。哨兵清除后继续正常 wait。
    waited = 0
    while _MAINT_SENTINEL.exists() and waited < 600:      # 最多守 10 分钟维护窗口
        if waited == 0:
            sys.stderr.write("🛠 bus 维护窗口(.bus_maintenance)——原地睡等,不重建 bus...\n")
        time.sleep(15); waited += 15

    # ── per-role 幂等:yield-not-kill 精确化(2026-08-03 治 kill-churn · 2026-08-10 治"让位给孤儿")──
    # 旧行为(挂前杀同 role 旧 wait)会把双挂放大成自持振荡(杀旧→旧 wait 空输出"完成"→harness 唤醒
    # →纪律重挂→又杀刚挂的→~15s 热循环)。08-03 改 yield:已有更老同 role wait 就让位不杀,治 churn。
    # ★08-10 精确化(治"接了任务不回报前台"bug):让位只对【同会话】(同 claude 宿主 pid)的更老 wait——
    #   同会话任一 wait claim 到任务、其后台进程完成都唤醒【同一前台会话】,让位安全。而【跨会话】的旧
    #   wait=孤儿/抢占(上次会话没退净 / 别的 tab / daemon 遗留 / resume 前身),让位给它=把耳朵交给
    #   【不唤醒本会话】的进程 → 它 claim 走任务后本会话永远收不到通知、任务卡 processing(reap 因
    #   _owner_live 是 role 级、本会话 wait 仍在岗 → 要拖到 age>ttl 4000s 才退回)。故跨会话/追不到宿主
    #   的旧 wait 一律【takeover 杀掉接管】(符合"一 role 一执行体"铁律)。宿主 pid 判据比 session_id 准
    #   (--resume 复用 sid,但每个活 claude.exe 是独立 pid);见 proc._proc_claude_ancestor。
    mine_sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    mine_host = _claude_ancestor()[0]               # 本 wait 的启动宿主 claude 会话 pid(P1 reg + 宿主自检用)
    if takeover:
        _kill_prior_waits(role)
    else:
        try:
            mine_ct = psutil.Process().create_time() if psutil else 0.0
        except Exception:
            mine_ct = 0.0
        mine_pid = os.getpid()

        def _same_session(pid):
            """判某 wait 进程是否与本会话同源。判据优先级(稳→次稳→保守):
              ① CLAUDE_CODE_SESSION_ID env 相同(最稳·不受父链断影响);
              ② 回退 claude 宿主 pid 相同;
              ③ 两者都判不了 → 保守【当同会话】(让位不杀=退回 08-03 纯 yield,宁可偶尔靠 reap 兜
                 也【绝不误杀同会话 wait 致 kill-churn】——实测教训:纯 host-pid 判据会把父链偶发
                 断裂的同会话背景 wait 误判跨会话杀掉)。"""
            psid = _proc_session_id(pid)
            if psid and mine_sid:
                return psid == mine_sid
            phost = _proc_claude_ancestor(pid)
            if phost is not None and mine_host is not None:
                return phost == mine_host
            return True

        same_older, cross = [], []
        for p in _prior_wait_procs(role):
            if _same_session(p.pid):
                try:
                    if (p.create_time(), p.pid) < (mine_ct, mine_pid):
                        same_older.append(p)         # 同会话且更老 → 让位(治 kill-churn)
                except Exception:
                    pass
            else:
                cross.append(p)                      # 确证跨会话 → 孤儿/抢占,接管防聋
        for p in cross:                              # 杀掉跨会话孤儿(它们持本 role 耳朵=本会话聋)
            try:
                p.kill()
            except Exception:
                pass
        if same_older:                               # 同会话已有更老 wait 在岗 → 让位不重挂
            h = same_older[0]
            try:
                _touch_presence(role, presence_mode, dict(presence_extra or {}))  # 顺手刷心跳
            except Exception:
                pass
            print(json.dumps({"already_armed": True, "role": role, "holder_pid": h.pid,
                              "note": "已有同会话在岗 wait,本次让位不重挂(防 kill-churn);"
                                      "确要强制换新用 --takeover"}, ensure_ascii=False))
            return
        if cross:
            sys.stderr.write("[wait] 清掉 %d 个跨会话/孤儿 wait(它们持 %s 耳朵会致本会话聋),本会话接管。\n"
                             % (len(cross), role))
    extra = dict(presence_extra or {}); extra["wait_timeout"] = timeout
    # ── wait 注册表登记(P1·2026-08-10 用户令·独立文件):登记本 wait 身份供竞争/宿主自检/观测 ──
    # 走到这=本 wait 决定挂(P0 yield 已让路给同会话更老 wait);覆盖旧 reg(接管者/被杀孤儿的),generation+1。
    _reg_path = _wait_reg_path(role)
    try:
        _gen = int(json.loads(_reg_path.read_text(encoding="utf-8")).get("generation", 0)) + 1
    except Exception:
        _gen = 1
    try:
        _write_json(_reg_path, {"role": role, "wait_pid": os.getpid(),
                                "host_claude_pid": mine_host, "host_session_id": mine_sid,
                                "started": _now(), "timeout": timeout, "last_heartbeat": _now(),
                                "status": "listening", "generation": _gen})
    except Exception:
        pass
    # 掉线尸检 trace(2026-07-28 会话可靠性立项):进场记 start,干净退场补 exit;
    # 事后 trace 有 start 无 exit = wait 进程【异常死亡】(被杀/休眠丢/宿主壳回收),guard 可据此分类根因,
    # 不再靠猜。单文件覆盖写(只留最近一次),历史轨迹靠 events.jsonl 不重复存。
    _tracef = BUS / role / "wait_trace.json"
    _trace = {"pid": os.getpid(), "started": _now(), "timeout": timeout, "exit": None}
    _write_json(_tracef, _trace)
    _hb_stop = _start_wait_heartbeat(role, presence_mode, extra,      # 阻塞期周期心跳 + P1 宿主自检
                                     host_pid=mine_host, reg_path=_reg_path, my_pid=os.getpid())
    try:
        _touch_presence(role, presence_mode, extra)       # 声明身份 + 心跳
        obj = _wait_core(role, timeout, include_approval)
    except Exception as e:
        # ② 不可恢复问题(bus 路径暂失 / 迁移中 / 未预期异常):睡 300s 再自恢复一次,绝不崩溃/秒返空转。
        #    睡眠期间后台 wait 仍"在跑"=agent 保持在岗、透明熬过瞬时故障,恢复后自动接上(心跳线程也在跳)。
        sys.stderr.write("⚠ wait 遇不可恢复问题:%s → 睡 300s 再自恢复重试...\n" % (str(e)[:150]))
        time.sleep(300)
        try:
            _touch_presence(role, presence_mode, extra)
            obj = _wait_core(role, timeout, include_approval)
        except Exception as e2:
            sys.stderr.write("⚠ 自恢复后仍异常:%s —— 返回 idle,交下一轮重挂。\n" % (str(e2)[:150]))
            obj = None
    finally:
        _hb_stop.set()
        try:                                          # P1:正常退出清掉自己持有的 reg(reg 只在 wait 活跃期存在)
            if json.loads(_reg_path.read_text(encoding="utf-8")).get("wait_pid") == os.getpid():
                _reg_path.unlink()
        except Exception:
            pass
    try:
        _trace.update(exit=("task" if obj else "idle"), exited=_now())
        _write_json(_tracef, _trace)
    except Exception:
        pass
    print(json.dumps(obj if obj is not None else {"idle": True, "role": role}, ensure_ascii=False))


def cmd_wait(args):
    # --include-approval:有人值守的 tab 显式领取 requires_approval 高风险任务(默认不领,留 inbox 待审)
    to = args.timeout
    if to is None or to <= 0:               # 哨兵 --timeout 0 → _idle_timeout_policy(默认 block:阻塞到消息·真事件仍即时接活)
        to = _idle_timeout_policy()
    _serve_wait(args.role, to, include_approval=args.include_approval,
                takeover=getattr(args, "takeover", False))


def cmd_serve(args):
    """通用 worker 入口:自动认出本 tab 的 role(需先 hello 过一次),再阻塞等任务。"""
    role = _resolve_self_role()
    if not role:
        _fail("未识别本 tab 身份,请先运行一次:agentbus hello --role <本tab名>")
        return
    _serve_wait(role, args.timeout, takeover=True)   # serve=前台执行体显式接管,保留杀清旧 wait 语义


def cmd_request(args):
    """请求-应答原语(ask-and-await):给 B 发子任务并【阻塞等 B 的应答】,按 task id 关联。
    底层就是 send + 等 <from>/done/<id>(done 读任务 from 已把结果路由回这,无需改 done)。
    daemon 模式下 B 的 claude -p 执行中可直接 shell 调 request 去"问 C 并等"。
    死锁/嵌套三层防范:① 禁自请求(to==from)② chain 软环检测(目标已在调用链→拒)
    ③ 强制有限超时(硬兜底:即便环没检出,全链最终超时收敛,绝不永久挂)。
    chain 跨 claude -p 靠 worker 自觉透传 --chain '<我收到任务里的 chain,逗号拼接>'。"""
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    if args.to == frm:                       # 防护1:自请求必死锁(自己阻塞等自己)
        _fail("self-request forbidden (to==from): %s" % frm)
    chain = [c for c in (args.chain.split(",") if args.chain else []) if c]
    if args.to in chain:                     # 防护2:目标已在调用链上 → 环(A→B→A、A→B→C→B 都挡)
        _fail("cycle detected: %s already in chain %s" % (args.to, chain))
    tid = _gen_id(args.to)
    task = {
        "id": tid, "from": frm, "to": args.to,
        "title": args.title or "", "task": args.task,
        "created": _now(), "status": "pending",
        "reply_mode": "sync", "chain": chain + [frm],   # 把自己加进 chain 往下传
    }
    _deliver(args.to, frm, task, audit_event="request")   # 走统一投递契约(P1:原径写绕过审计/告警/培训闸)
    # 防护3:阻塞等应答,强制有限超时
    rep = _wait_for_reply(frm, tid, args.timeout)
    print(json.dumps(rep if rep is not None else {"timeout": True, "id": tid, "to": args.to}, ensure_ascii=False))


def _find_task(role, tid):
    """按 id 找任务文件。P1(2026-07-28):①禁前缀匹配——短 id startswith 会错配同前缀
    任务(_gen_id 时间戳开头,同毫秒/复制粘贴截断都可能撞),回执路由给错人;只认精确 id。
    ②查找含 archive/——done 崩在归档后回执前(或回执补发)时,任务已在 archive,
    仍能找回原 from 正确路由,不再落 unknown。"""
    for sub in ("processing", "inbox", "failed", "archive"):
        p = BUS / role / sub / (tid + ".json")
        if p.exists():
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
    if p and p.parent.name != "archive":   # 已在 archive(重复 done/回执补发)→ 只发回执不再搬运(P1)
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
