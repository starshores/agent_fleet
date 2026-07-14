#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""agentbus — 文件总线,让多个 Claude Code CLI tab(各跑一个 role)互相分派任务。

设计:无服务端,纯文件 + watchdog 事件(不 sleep polling)。一个 role 一个 tab。

目录(默认 ~/.claude/agent_bus/):
  agents.json                  role 注册表
  <role>/inbox/<id>.json       派给该 role、待处理的任务
  <role>/processing/<id>.json  该 role 正在处理(wait 领取后移到这)
  <role>/archive/<id>.json     处理完归档
  <role>/done/<id>.json        别的 role 回填给"我"的结果(我是发起方时收在这)

命令:
  register --role Master
  send --to SQLAGent --task "..." [--from Master] [--title "..."]   -> 打印 task id
  wait  --role SQLAGent [--timeout 110]   阻塞到有任务(事件触发),领取并打印 JSON;超时打印 {"idle":true}
  done  --id <id> --role SQLAGent --result "..." [--status ok|error]  回填结果给发起方,归档
  peek  --role SQLAGent          列出该 role inbox+processing 待办(不领取)
  replies --role Master [--clear] 读取回填给我的结果(--clear 读后清空)
  list                           所有 role 的队列深度
用法约定:role 名可含中文;--from 缺省取环境变量 AGENTBUS_ROLE,再缺省 "unknown"。
"""
import argparse, json, os, sys, time, threading, socket, subprocess
from pathlib import Path

try:
    import psutil
except Exception:
    psutil = None

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")   # 2026-07-10 补:警告/报错(⚠…)走 stderr,漏它=出问题时最需读的恰 garble
except Exception:
    pass
os.environ.setdefault("PYTHONUTF8", "1")   # 传给 agentbus 起的子 python(dispatch/daemon/ad-hoc),从源头覆盖 GBK

# ---- 共享基础层(BUS 2.0 P1):config / io / roster 已抽到 bus/ 包,此处 re-export 保兼容公共面 ----
# (dispatch_agent / longrange_net / pool_dispatcher / worker_daemon / ui.server 都 import agentbus as ab
#  并用 ab.BUS / ab.cfg / ab._now / ab._write_json / ab._gen_id 等,名字经 re-export 不变。)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bus.config import BUS, METRICS, cfg, _now  # noqa: E402
from bus.io import _gen_id, _write_json, _oldest, _audit, record_metric, read_metrics  # noqa: E402
from bus.roster import (  # noqa: E402
    _agents_file, _load_agents, _save_agents, _role_dir, _list_roles,
    _retired_roles, _is_test_role, _PSEUDO_ROLE_LABELS, _pseudo_role_label,
)
from bus.proc import _claude_ancestor, _proc_scan, _alive_claude_pids, _age_seconds  # noqa: E402
from bus.presence import _touch_presence, _set_current_task, _pidtags_file, _load_pidtags, _owner_live  # noqa: E402
from bus.selfwake import _wait_proc_roles, _parked_path, _is_parked, _park_expired, _self_wake_state, _ancestor_pids  # noqa: E402
from bus.roster import HUMAN_ATTENDED  # noqa: E402
from bus.notify import _toast_throttled, _fire_toast, _maybe_toast, _emit_completion_notice  # noqa: E402



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

def cmd_register(args):
    role = args.role
    _role_dir(role)
    a = _load_agents()
    a[role] = {"registered": _now(), "desc": args.desc or ""}
    _save_agents(a)
    print("registered role:", role)


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


def _claim(role, taskpath):
    """原子领取:把任务从 inbox 移到 processing,返回 task dict。
    多监听者(tab /loop + daemon)并存时只有一个 rename 成功,其余返回 None(没抢到),
    所以绝不会重复执行同一任务。坏文件返回 {"error":...}。"""
    proc = BUS / role / "processing" / taskpath.name
    for attempt in range(4):
        try:
            os.replace(str(taskpath), str(proc))  # 原子;抢输的那个抛 FileNotFoundError
            break
        except FileNotFoundError:
            return None  # 已被别的监听者领走 / 文件没了——不重试
        except OSError:
            # Windows 下源文件被 watcher/AV 瞬时占用会抛 PermissionError(OSError 子类)→ 领取竞态。
            # 源还在就短重试(Master 实测反馈);源没了=被领走,放弃。
            if not taskpath.exists():
                return None
            if attempt == 3:
                return None  # 重试仍失败,本轮放弃(任务留 inbox,下轮再领)
            time.sleep(0.04 * (attempt + 1))
    try:
        obj = json.loads(proc.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": "bad task file: %s" % e}
    obj["status"] = "processing"
    obj["claimed"] = _now()
    try:
        _write_json(proc, obj)   # 更新 status/claimed 供 who/WebUI 看
    except Exception as e:
        # 任务已原子移入 processing(上面 os.replace 成功),状态改写只是尽力而为——
        # 写失败(瞬时锁等持久化失败)绝不能崩 wait,否则协调位自醒掉线漏消息(Master 报的惨案)。
        _audit("claim_write_warn", id=obj.get("id", taskpath.stem), role=role, err=str(e)[:80])
    _audit("claim", id=obj.get("id", taskpath.stem), role=role, frm=obj.get("from"))
    _set_current_task(role, obj.get("id", taskpath.stem), obj.get("title"))  # A3:presence 记"当前在做",who/WebUI 可见
    return obj


def _claim_and_print(role, taskpath):
    print(json.dumps(_claim(role, taskpath), ensure_ascii=False))


def _resolve_self_role():
    """靠本 tab 的 claude 进程 pid 反查它声明过的 role(presence.json 里 claude_pid 匹配)。"""
    pid, _, _ = _claude_ancestor()
    if not pid or not BUS.exists():
        return None
    for d in BUS.iterdir():
        pf = d / "presence.json"
        if d.is_dir() and pf.exists():
            try:
                pr = json.loads(pf.read_text(encoding="utf-8"))
                if pr.get("claude_pid") == pid:
                    return d.name
            except Exception:
                pass
    return None


def _oldest_claimable(inbox, include_approval=False):
    """inbox 里最老的【可自动领取】任务。requires_approval=true 的高风险任务默认跳过
    (统一的'先审后做'闸:tab serve / daemon / wait 都走这里,所以闸对 tab 和 daemon 一致生效),
    留在 inbox 等人工 `wait --include-approval` 在有人值守的 tab 里领取执行。"""
    for p in sorted(inbox.glob("*.json")):
        if include_approval:
            return p
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
            if o.get("requires_approval"):
                continue  # 高风险:不自动领,跳过
        except Exception:
            pass
        return p
    return None


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


def _serve_wait(role, timeout, presence_mode="listening", presence_extra=None, include_approval=False):
    _kill_prior_waits(role)   # per-role 幂等:先清同 role 旧 wait,防累积(Master数分 2026-07-02)
    extra = dict(presence_extra or {}); extra["wait_timeout"] = timeout
    _touch_presence(role, presence_mode, extra)  # 声明身份 + 心跳
    obj = _wait_core(role, timeout, include_approval)
    print(json.dumps(obj if obj is not None else {"idle": True, "role": role}, ensure_ascii=False))


def cmd_wait(args):
    # --include-approval:有人值守的 tab 显式领取 requires_approval 高风险任务(默认不领,留 inbox 待审)
    _serve_wait(args.role, args.timeout, include_approval=args.include_approval)


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
    reply_mode = None; task_kind = None
    if p:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            sender = obj.get("from", "unknown"); title = obj.get("title", ""); real_id = obj.get("id", tid)
            reply_mode = obj.get("reply_mode"); task_kind = obj.get("kind")
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
    if reply_mode != "sync" and task_kind != "notice" and args.notify != "never":
        _emit_completion_notice(sender, role, real_id, args.status, title)
    print("done -> %s (status=%s)" % (sender, args.status))
    # D(2026-07-08·防自醒-loss):done 后若本 role 无 armed wait 进程 → 提示重挂(前台CLI干完活易忘)
    try:
        if role and role not in ("用户", "unknown") and role not in _wait_proc_roles():
            print("⚠ 自醒提示:你(%s)当前无 armed wait 进程,done 后请【后台 run_in_background】重挂 "
                  "`wait --role %s --timeout 3600`,否则空闲后新消息事件唤不醒你。" % (role, role))
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


def cmd_tag(args):
    """按 PID 打"非 fleet"标注(如"其他事务"):不是角色/无 inbox,只让 who 认得它、清掉未声明噪音。
    默认 **私有**:只存 label,**不存 cwd/备注、也不去探进程**(隐私:其他事务的信息对总线隐藏)。
    --public 才存可选备注。"""
    pid = args.pid
    private = not args.public
    entry = {"label": args.label, "private": private, "tagged_at": _now()}
    if not private and args.note:
        entry["note"] = args.note  # 仅 public 标注才留备注;private 一律不留任何细节
    tags = _load_pidtags()
    tags[str(pid)] = entry
    BUS.mkdir(parents=True, exist_ok=True)
    _write_json(_pidtags_file(), tags)
    print("已标注 pid %s = %s%s" % (pid, args.label, "(隐藏信息,不探不存细节)" if private else ""))


def cmd_hello(args):
    """会话自报身份:在本 tab 里跑一次,声明"我是 role X"。
    顺带打印【基本能力 onboarding】——事件自醒 + 待办记录(ask-user),新 agent 一注册就知道,不必翻文档。"""
    if getattr(args, "preview", False):
        # 纯预览(WebUI 提示词审核页 server.py 调 `ab hello --preview`):不带 --role 也能跑,
        # 只打印 onboarding 文本,【绝不】写 presence / 绑 pid / 改 mode —— 只读、零副作用。
        _print_onboarding(args.role or "<你的role>")
        return
    role = args.role
    if not role:
        sys.stderr.write("hello 需要 --role(仅预览可省:hello --preview)\n"); return
    _role_dir(role)
    a = _load_agents()
    a.setdefault(role, {})["registered"] = a.get(role, {}).get("registered") or _now()
    _save_agents(a)
    try:
        _parked_path(role).unlink()   # 重开 hello=活跃 → 清 park/休眠标(秒回活跃·⑤)
        print("(已清除 park/休眠标——重新活跃)")
    except FileNotFoundError:
        pass
    except Exception:
        pass
    _touch_presence(role, "idle")
    pf = BUS / role / "presence.json"
    print(pf.read_text(encoding="utf-8"))
    _print_onboarding(role)


def _print_onboarding(role):
    """打印【基本能力 onboarding】文本(事件自醒 / 待办 ask-user / 专一性 / 卡住别硬扛)。
    正常 hello 与 hello --preview 共用;纯打印、无副作用。role 可为占位符(preview 时)。"""
    abpy = os.path.abspath(__file__)
    py = sys.executable or "python"   # 绝对解释器路径,PATH-proof:后台 shell 偶发 PATH 缺 python 会让裸 `python` 启动 127→自醒掉线(Master数分/我都撞过)
    print("\n【★★ 你的第一个动作·立刻照做,别往下读、也别先扎进任务 ★★】")
    print("你现在【还没在岗·收不到任何消息】。【开始任何任务之前】,先把下面这条 wait 挂成后台任务、再验在岗——【先在岗,再开工】。")
    print("就算你是被叫来干某个活的:也【先挂 wait 再干活】,否则干活期间收不到新消息、干完也没挂=直接掉线,bus 上没人联系得到你:")
    print('  ① 后台跑(交互 tab 用 run_in_background、别前台阻塞): "%s" "%s" wait --role %s --timeout 3600' % (py, abpy, role))
    print('  ② 验在岗: "%s" "%s" selfwake   (看本 role 是否 ✓在岗;没起来就再挂+再验,直到确认)' % (py, abpy))
    print("  ③ 确认 ✓在岗后,才开始做你被交代的任务;之后每次被唤起、处理完,都要【再挂一次】wait(不重挂=掉线)。")
    print("  (注:hello 只登记身份+打印本说明,【不会替你把自醒跑起来】——必须你自己执行①②③。下面是完整能力说明。)")
    print("\n==== 你的基本能力(每个 agent 都该会;详见 agent_fleet/AGENTS.md + BUS_ONBOARDING.md)====")
    print("\n[基本能力 1] 事件自醒(可对话又不漏消息;交互 tab 建议挂上):")
    print("把下面这条当【后台任务】跑(别前台阻塞):有人给你发消息/派活时它就完成 → 你的会话被自动唤起去处理:")
    print('  "%s" "%s" wait --role %s --timeout 3600' % (py, abpy, role))
    print("【铁律①】每次被唤起、处理完,必须【再挂一个】同样的 wait —— 不重挂就掉线,之后的消息收不到。")
    print("【铁律②】用上面的【绝对解释器路径】(别用裸 `python`,省掉 PATH 类失败)。")
    print("【铁律③·最重要】后台重挂会【偶发 127 静默失败】(harness 后台 shell 启动层抽风,绝对路径也治不了,非你的错)——")
    print("   所以重挂【发了不算数】:挂完【验证】自己在岗(跑 `\"%s\" \"%s\" selfwake` 看本 role 是否 ✓在岗),没起来就【再挂+再验】直到确认在岗。否则=静默掉聋。" % (py, abpy))
    print('收到 kind="notice" 的完成通知:读它 notice_of 指向的 %s/done/<id>.json 取完整结果,再 done 掉该 notice(读完即弃,勿当任务执行)。' % role)
    print("【铁律④·上下文卫生】被唤起后【若 inbox 无新活】(空唤醒/只是 ACK 通知/idle 超时)→【静默重挂 + 最多一行待命 + 绝不重新 dump selfwake/who/花名册/状态】。")
    print("   每次空醒都 dump 状态=自己给自己灌上下文→更快撑到 1M 顶→被迫 compact→失能,是失能的【自我加速器】。口诀:【空唤醒→静默重挂→不重述状态】。")
    print("\n[基本能力 2] 待办记录(要用户确认/拍板的事别只在对话里说——会滚走漏掉,用它投进待办池):")
    print('  "%s" "%s" ask-user --from %s --title "<要用户定的事>" --detail "<上下文/选项>"' % (py, abpy, role))
    print("  → 落进 UI 待办池的🙋待确认队列,用户在一处统一处理、不再错过。【凡需用户拍板都走这个,是标配动作】。")
    print("\n[专一性纪律·防失能] 只做你的专长(见你的 caps/desc);【跨领域的活别自接】——")
    print('  用 `"%s" "%s" roster --grep <关键词>` 找对口角色转给它,不确定就问 Master/培训师 路由。' % (py, abpy))
    print("  原因:一肩挑全科会让上下文从源头就杂、越堆越乱,最终撑爆窗口→ compact 有损→ 失能退役")
    print("  (这是【业务侧某 agent】的真实教训:无分工时代被迫全科,积重难返。做减法=保专一=防失能)。")
    print("\n[基本能力 3] 卡住别硬扛——查能力池 / 请培训师补训(【是你的活、但有棘手子块】时):")
    print("  硬扛最贵(易做坏 + 烧窗口)。两条出路:")
    print('  ① 查能力池看谁会:`"%s" "%s" caps` / `"%s" "%s" roster --grep <关键词>`——若其实是别人专长,轻问或路由给他(换人做);' % (py, abpy, py, abpy))
    print('  ② 请培训师补训这块:`"%s" "%s" send --to 培训师 --from %s --task "我在做X、卡在Y这棘手块,请按能力池给我补训"`(培训师也【按需补训老人】,教我做)。' % (py, abpy, role))
    print("  口诀:棘手 → 先查能力池 → 换人做 或 请培训师教我做,别硬扛。")


def _who_data():
    """结构化版 who:权威核对 presence 心跳 与 实际 claude 进程,给 WebUI 健康模块吃。
    返回 {as_of, psutil, roles:[...], alive_claude, claimed_pids, tagged, unclaimed_pids, edge_lock}。
    每个 role 记 registered/caps(花名册真相)+ present/mode/claude_pid/daemon_pid/alive/age_s/verdict/cwd/current_*。
    注:presence 只存 claude_pid+cwd,不存 session-jsonl id —— 会话↔角色的对齐由 WebUI 侧按 cwd 关联(同 cwd 多 agent 时标注歧义,不猜)。"""
    alive = _alive_claude_pids()
    roles = _list_roles()
    retired_set = _retired_roles()
    try:
        agents = _load_agents()
    except Exception:
        agents = {}
    claimed = set()
    out_roles = []
    for r in roles:
        ent = agents.get(r) if isinstance(agents, dict) else None
        rec = {
            "role": r,
            "registered": ent.get("registered") if isinstance(ent, dict) else None,
            "caps": (ent.get("caps") if isinstance(ent, dict) else None) or [],
            "retired": r in retired_set, "present": False, "mode": None,
            "claude_pid": None, "daemon_pid": None, "alive": None, "age_s": None,
            "verdict": None, "cwd": None, "current_task": None, "current_title": None,
            "session_id": None,
        }
        plabel = _pseudo_role_label(r)
        if plabel:                    # 伪角色/系统桶:非执行体,不判在线/掉线,单列标注
            rec["pseudo"] = True; rec["verdict"] = "◇ " + plabel; out_roles.append(rec); continue
        if r in retired_set:
            rec["verdict"] = "⊘ 退役"; out_roles.append(rec); continue
        pk = _is_parked(r)
        if pk:   # 故意暂离(park/休眠)优先短路:与 selfwake 单一源一致,别把故意退出的会话当"崩溃残留"
            rec["parked"] = True
            ext = "·已到期待unpark" if _park_expired(pk) else ((("·%s天" % pk.get("days")) if pk.get("days") else ""))
            note = ("·" + pk["note"]) if pk.get("note") else ""
            rec["verdict"] = "🅿️ 休眠(用户故意暂离%s%s)" % (ext, note)
            out_roles.append(rec); continue
        pf = BUS / r / "presence.json"
        if not pf.exists():
            rec["verdict"] = "未声明(无 presence)"; out_roles.append(rec); continue
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            rec["verdict"] = "presence 损坏"; out_roles.append(rec); continue
        rec["present"] = True
        pid = pr.get("claude_pid"); rec["claude_pid"] = pid
        age = _age_seconds(pr.get("last_seen", "")); rec["age_s"] = age
        mode = pr.get("mode", "?"); rec["mode"] = mode
        rec["cwd"] = pr.get("cwd")
        rec["session_id"] = pr.get("session_id")   # 权威 session→role(取代 regex 猜)
        rec["current_task"] = pr.get("current_task")
        rec["current_title"] = pr.get("current_title")
        wt = pr.get("wait_timeout") or 110
        listen_fresh = (age is not None and age <= wt + 60)
        if mode == "daemon":
            dpid = pr.get("daemon_pid"); rec["daemon_pid"] = dpid
            d_alive = psutil.pid_exists(dpid) if (dpid and psutil is not None) else None
            rec["alive"] = d_alive
            rec["verdict"] = ("✓ daemon在听" if d_alive else ("✗ daemon已退" if dpid else "daemon?"))
            out_roles.append(rec); continue
        proc_alive = (pid in alive) if (pid and psutil is not None) else None
        rec["alive"] = proc_alive
        if pid:
            claimed.add(pid)
        if psutil is None:
            rec["verdict"] = "心跳%ss前(mode=%s)" % (age if age is not None else "?", mode)
        elif pid and not proc_alive:
            rec["verdict"] = "✗ 进程已退(残留)"
        elif mode == "listening" and proc_alive and listen_fresh:
            rec["verdict"] = "✓ worker在听"
        elif mode == "listening" and proc_alive:
            rec["verdict"] = "⚠ worker静默(超心跳%ss)" % (age if age is not None else "?")
        elif proc_alive:
            rec["verdict"] = "已声明(hello),未起循环"
        else:
            rec["verdict"] = "心跳%ss前(无pid)" % (age if age is not None else "?")
        out_roles.append(rec)
    tags = _load_pidtags() if psutil is not None else {}
    rest = sorted(alive - claimed) if psutil is not None else []
    tagged = [{"pid": pid, "label": tags[str(pid)].get("label"), "private": bool(tags[str(pid)].get("private"))}
              for pid in rest if str(pid) in tags]
    unknown = [pid for pid in rest if str(pid) not in tags]
    h = _edge_holder()
    return {
        "as_of": _now(), "psutil": psutil is not None, "roles": out_roles,
        "alive_claude": sorted(alive) if psutil is not None else [],
        "claimed_pids": sorted(claimed), "tagged": tagged, "unclaimed_pids": unknown,
        "edge_lock": ({"who": h.get("who"), "age_s": _age_seconds(h.get("acquired_at", ""))} if h else None),
    }


def cmd_who(args):
    """谁在跑 + 是谁:交叉核对 presence 心跳 与 实际 claude 进程。"""
    if getattr(args, "json", False):
        print(json.dumps(_who_data(), ensure_ascii=False)); return
    fresh = args.max_age
    alive = _alive_claude_pids()
    roles = _list_roles()
    retired_set = _retired_roles()
    claimed = set()
    print("%-14s %-22s %-9s %8s  %s" % ("role", "verdict", "claude_pid", "心跳age", "cwd"))
    print("-" * 88)
    for r in roles:
        plabel = _pseudo_role_label(r)
        if plabel:                  # 伪角色/系统桶:非执行体,不当掉线/未醒,单列标注
            print("%-14s %-22s  %s" % (r, "◇ 伪角色", plabel)); continue
        if r in retired_set:        # 退役:标注、不当残留/异常(进程退是应该的)
            print("%-14s %-22s" % (r, "⊘ 退役")); continue
        pk = _is_parked(r)          # 故意暂离(park/休眠):进程死是目的非崩溃,别显"残留"(与 _who_data/selfwake 单一源一致)
        if pk:
            ext = "·已到期待unpark" if _park_expired(pk) else ((("·%s天" % pk.get("days")) if pk.get("days") else ""))
            note = ("·" + pk["note"]) if pk.get("note") else ""
            print("%-14s %-22s %s" % (r, "🅿️ 休眠", "用户故意暂离%s%s" % (ext, note))); continue
        pf = BUS / r / "presence.json"
        if not pf.exists():
            print("%-14s %-22s" % (r, "未声明(无 presence)"))
            continue
        try:
            pr = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            print("%-14s %-22s" % (r, "presence 损坏")); continue
        pid = pr.get("claude_pid")
        age = _age_seconds(pr.get("last_seen", ""))
        mode = pr.get("mode", "?")
        wt = pr.get("wait_timeout") or 110
        listen_fresh = (age is not None and age <= wt + 60)
        if mode == "daemon":
            # 无可见 tab 的后台守护进程:按 daemon_pid 判活(不算进 claude 认领集)
            dpid = pr.get("daemon_pid")
            d_alive = psutil.pid_exists(dpid) if (dpid and psutil is not None) else None
            verdict = ("✓ daemon在听" if d_alive else ("✗ daemon已退" if dpid else "daemon?"))
            cur = pr.get("current_task")
            last = ("▶ 在做:%s" % (pr.get("current_title") or cur)) if cur else (pr.get("cwd") or "-")
            print("%-14s %-22s %-9s %7ss  %s" % (
                r, verdict, "d:%s" % (dpid or "?"), str(age if age is not None else "?"), last))
            continue
        proc_alive = (pid in alive) if (pid and psutil is not None) else None
        if pid:
            claimed.add(pid)
        if psutil is None:
            verdict = "心跳%ss前(mode=%s)" % (age if age is not None else "?", mode)
        elif pid and not proc_alive:
            verdict = "✗ 进程已退(残留)"
        elif mode == "listening" and proc_alive and listen_fresh:
            verdict = "✓ worker在听"
        elif mode == "listening" and proc_alive:
            verdict = "⚠ worker静默(超心跳%ss)" % (age if age is not None else "?")
        elif proc_alive:
            verdict = "已声明(hello),未起循环"
        else:
            verdict = "心跳%ss前(无pid)" % (age if age is not None else "?")
        cur = pr.get("current_task")
        last = (pr.get("cwd") or "-") + (("  ▶ 在做:%s" % (pr.get("current_title") or cur)) if cur else "")
        print("%-14s %-22s %-9s %7ss  %s" % (
            r, verdict, str(pid or "-"), str(age if age is not None else "?"), last))
    # 在跑但没认领的 claude 进程:区分"已标注(非fleet)"和"真未声明"
    if psutil is not None:
        tags = _load_pidtags()
        rest = sorted(alive - claimed)
        tagged = [(pid, tags[str(pid)]) for pid in rest if str(pid) in tags]
        unknown = [pid for pid in rest if str(pid) not in tags]
        print("-" * 88)
        if tagged:
            print("已标注(非fleet):", ", ".join(
                "%s=%s%s" % (pid, t.get("label", "?"), "🔒" if t.get("private") else "")
                for pid, t in tagged))
        print("在跑的 claude 进程: %d 个;fleet角色: %d;已标注: %d;未声明: %d %s" % (
            len(alive), len(claimed), len(tagged), len(unknown), unknown if unknown else ""))
    else:
        print("(psutil 不可用,无法核对进程;仅凭心跳)")
    h = _edge_holder()
    if h:
        age = _age_seconds(h.get("acquired_at", ""))
        print("Edge 锁: %s 持有(%ss%s)" % (h.get("who"), age, "·疑似陈旧" if (age and age > 300) else ""))


def cmd_list(args):
    a = _load_agents()
    roles = sorted(set(list(a.keys()) + _list_roles()))
    if not roles:
        print("(no roles registered)"); return
    print("%-16s %6s %10s %5s" % ("role", "inbox", "processing", "done"))
    for r in roles:
        d = BUS / r
        ib = len(list((d / "inbox").glob("*.json"))) if (d / "inbox").exists() else 0
        pr = len(list((d / "processing").glob("*.json"))) if (d / "processing").exists() else 0
        dn = len(list((d / "done").glob("*.json"))) if (d / "done").exists() else 0
        print("%-16s %6d %10d %5d" % (r, ib, pr, dn))


def cmd_reap(args):
    """孤儿回收:扫各 role 的 processing,把【owner 已死/超期】的滞留任务退回 inbox。
    判据(每条 processing 任务):
      age = now - claimed(没 claimed 退用 created)
      stuck = (owner 不在听 且 age>grace) 或 (age>ttl 硬上限,连"活着但真卡住"也兜)
    退回时 requeue_count+1、status→pending、抹掉 claimed;超过 max-requeue 视为毒任务,
    挪到 <role>/failed/ 不再 requeue(避免坏任务无限重派打爆舰队)。
    grace 默认 120s(避开刚领取时 presence 短暂空窗的误判);ttl 默认 4000s(> daemon claude -p 的 3600s 超时,正常长任务不误伤)。"""
    roles = [args.role] if args.role else _list_roles()
    actions = []
    for role in roles:
        proc = BUS / role / "processing"
        if not proc.exists():
            continue
        live = _owner_live(role)
        for p in sorted(proc.glob("*.json")):
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                actions.append(("坏文件跳过", role, p.name)); continue
            age = _age_seconds(obj.get("claimed") or obj.get("created") or "")
            if age is None:
                continue
            stuck = (not live and age > args.grace) or (age > args.ttl)
            if not stuck:
                continue
            rq = int(obj.get("requeue_count", 0)) + 1
            tid = obj.get("id", p.stem)
            if rq > args.max_requeue:
                if not args.dry_run:
                    failed = BUS / role / "failed"; failed.mkdir(parents=True, exist_ok=True)
                    obj["status"] = "failed_max_requeue"; obj["reaped"] = _now()
                    _write_json(failed / p.name, obj)
                    try: p.unlink()
                    except Exception: pass
                actions.append(("毒任务→failed(超%d次)" % args.max_requeue, role, tid)); continue
            if not args.dry_run:
                obj["status"] = "pending"; obj["requeue_count"] = rq
                obj.pop("claimed", None); obj["requeued_at"] = _now()
                _write_json(BUS / role / "inbox" / p.name, obj)
                try: p.unlink()
                except Exception: pass
            why = "owner存活但超ttl卡死" if live else "owner进程已退"
            actions.append(("退回inbox(第%d次,%s,age%ds)" % (rq, why, age), role, tid))
    if not actions:
        print("reap: 无孤儿任务(processing 干净,或 owner 都在听且未超 ttl)"); return
    for act, role, tid in actions:
        print("[%s] %s  %s" % (act, role, tid))
    if args.dry_run:
        print("(dry-run,未实际改动)")


def cmd_prune(args):
    """清理空的遗留 role 目录:无 presence.json 且 inbox/processing/done/archive/held/failed 全空。
    有 presence 或任何任务文件的 role 一律保留(不误删在用/在跑的)。"""
    import shutil
    removed, kept = [], []
    for role in _list_roles():
        d = BUS / role
        if (d / "presence.json").exists():
            continue
        has_files = any((d / sub).exists() and any((d / sub).glob("*.json"))
                        for sub in ("inbox", "processing", "done", "archive", "held", "failed"))
        if has_files:
            continue
        if args.dry_run:
            removed.append(role); continue
        try:
            shutil.rmtree(d); removed.append(role)
        except Exception as e:
            kept.append((role, str(e)))
    print("prune: 可清理空目录 →", ", ".join(removed) if removed else "(无)")
    if kept:
        print("  删除失败:", kept)
    if args.dry_run and removed:
        print("  (dry-run,未删)")


def _daemon_alive(role):
    """该 role 是否有活着的后台 daemon(presence mode=daemon 且 daemon_pid 在跑)。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return False
    try:
        pr = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return False
    dpid = pr.get("daemon_pid")
    if pr.get("mode") != "daemon" or not dpid:
        return False
    return bool(psutil is not None and psutil.pid_exists(dpid))


def _orphan_daemon_pids(fleet_roles):
    """【孤儿 daemon】= 还在跑 worker_daemon.py --role X、但 X 不在 fleet 清单里的进程(从 fleet 摘角色后的残留)。
    只认真正 python 跑 worker_daemon 的进程(排除 bash -c 壳);前台 CLI(非 worker_daemon)/ dispatch_agent 不算,绝不误杀。
    返回 [(pid, role), ...]。"""
    out = []
    if psutil is None:
        return out
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if "python" not in (p.info.get("name") or "").lower():
                continue
            cl = p.cmdline()
            if not any("worker_daemon" in str(x) for x in cl):
                continue
            role = None
            for i, x in enumerate(cl):
                if x == "--role" and i + 1 < len(cl):
                    role = cl[i + 1]
                    break
            if role and role not in fleet_roles:
                out.append((p.pid, role))
        except Exception:
            continue
    return out


def _stale_webui_pids(port=8799, min_age=30):
    """重复/陈旧 WebUI = 在跑【agent_fleet 的 ui/server.py】但没占住 port 的 python 进程(如 31468 旧实例)。
    安全铁律(业务侧 agent 报过误杀 sql_agent server.py 的惨案后强化):
      ① 候选【只认 cmdline 含 /ui/server.py】—— 精确匹配 WebUI 本体,绝不把别的 server.py(如 sql_agent 占 5678)算进来;
      ② 绝不杀【占着任何监听端口】的进程(那是合法服务,如 sql_agent);
      ③ 只在确有进程占住 8799 时才认定其余为陈旧;④ min_age 保护(太年轻可能在 bind)。返回 [(pid, age_s)]。"""
    if psutil is None:
        return []
    webui = {}
    for p in psutil.process_iter(["name", "cmdline", "create_time"]):
        try:
            if "python" not in (p.info.get("name") or "").lower():
                continue
            cl = " ".join(str(x) for x in (p.cmdline() or [])).replace("\\", "/").lower()
            if "/ui/server.py" in cl:          # 精确:只认 WebUI 的 ui/server.py,排除 sql_agent/server.py 等
                webui[p.pid] = p.info.get("create_time")
        except Exception:
            continue
    if len(webui) <= 1:
        return []
    owner = None
    port_owners = set()                        # 占着【任何】监听端口的 pid —— 一律不杀(合法服务)
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == "LISTEN" and c.pid:
                port_owners.add(c.pid)
                if c.laddr and c.laddr.port == port:
                    owner = c.pid
    except Exception:
        return []                      # 拿不到端口归属 → 不动(安全)
    if owner is None or owner not in webui:
        return []                      # 没有明确占 8799 的 WebUI → 不动(可能在启动)
    now = time.time()
    stale = []
    for pid, ct in webui.items():
        if pid == owner:
            continue                   # 占 8799 的=在岗,绝不杀
        if pid in port_owners:
            continue                   # 占着别的监听端口=合法服务(如 sql_agent 5678),绝不杀
        age = now - (ct or now)
        if age < min_age:
            continue                   # 太年轻,可能在 bind,跳过
        stale.append((pid, int(age)))
    return stale


def cmd_webui_guard(args):
    """重复 WebUI 守卫:清掉没占端口的陈旧 server.py 实例(防 31468 那类流氓 supervisor + 省 CPU)。
    只杀【不占 port 且 >min_age】的;占端口的在岗实例绝不动;拿不到端口归属时一个不动。"""
    stale = _stale_webui_pids(args.port, args.min_age)
    if not stale:
        print("WebUI 守卫:无重复/陈旧实例(或无法确认端口归属 → 未动)"); return
    for pid, age in stale:
        if args.dry_run:
            print("[陈旧 WebUI 将清(dry-run)] pid=%s age=%ss" % (pid, age)); continue
        try:
            psutil.Process(pid).kill()
            _audit("reap_stale_webui", pid=pid, age=age)
            print("[陈旧 WebUI 已清] pid=%s age=%ss" % (pid, age))
        except Exception as e:
            print("[清陈旧 WebUI 失败] pid=%s: %s" % (pid, e))


# ── 系统级 bus 健康巡检(push 补 pull 盲区:丢失/卡死的消息没有 agent 会自报)──────────
# Master 定调(2026-06-25):能自动追回的先自动修(孤儿回执→推断 owner 自动重路由),
# 修不了/需人定的才投🙋待办池,去重、别让巡检变噪音源。
_MGMT_OWNER = {"MasterUI": "MasterAgent", "培训师": "MasterAgent"}
# 卡死硬上限(分):owner 进程还活着但任务真卡这么久也兜底判卡死。远大于 daemon claude -p 的
# 3600s 超时与正常长任务,避免误伤;跨夜掉自醒(进程活·心跳陈)不会触达这里→不再误判卡死。
_STUCK_TTL_MIN = 1440

def _infer_orphan_owner(reply):
    """从孤儿回执推断原请求方:管理位(MasterUI/培训师)的活多来自 MasterAgent;
    其余已登记角色(协调位自身除外)默认由总控 Master 兜底。推不出返回 None(→需人定,投待办)。"""
    frm = reply.get("from")
    if not frm or frm == "unknown" or frm in ("Master", "MasterAgent"):
        return None
    if frm in _MGMT_OWNER:
        return _MGMT_OWNER[frm]
    if frm in _load_agents():
        return "Master"
    return None

def _health_anomalies(stuck_min=90):
    """纯检测无副作用。orphans=回执丢进 unknown/(没 owner 会自报);
    stuck=认领后久未完成的 processing(>stuck_min,worker 可能死/卡——阈值取大避免误报正常长任务)。"""
    now = time.time()
    orphans, stuck = [], []
    for sub in ("inbox", "done"):
        d = BUS / "unknown" / sub
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if _is_test_role(o.get("from")):     # 测试发送方的孤儿=噪声,不计
                continue
            orphans.append({"path": str(p), "id": p.stem, "from": o.get("from"),
                            "title": o.get("title") or "", "owner": _infer_orphan_owner(o), "obj": o})
    for d in BUS.iterdir():
        if not d.is_dir() or d.name.startswith((".", "_")) or d.name == "unknown" or _is_test_role(d.name):
            continue
        if _is_parked(d.name):   # 故意暂离(park/休眠):进程死是目的非崩溃,残留 processing 不报"卡死/owner死"
            continue              # (与 selfwake/who 单一源一致·park 压误报);真需重派由 reap/unpark 后处理
        pd = d / "processing"
        if not pd.exists():
            continue
        live = _owner_live(d.name)   # owner 进程是否还在(不看心跳,与 reaper 同口径)
        for p in pd.glob("*.json"):
            age = (now - p.stat().st_mtime) / 60
            # 只在【owner 进程已死 且 age≥stuck_min】或【age≥硬 ttl(真卡这么久也兜)】时算卡死。
            # owner 进程还活着(哪怕掉了自醒/在阻塞跑长任务)且未超 ttl → 不算卡死:掉自醒由
            # selfwake-guard 专管,别误 framing 成"任务卡死·要不要重派"。否则跨夜全队掉线时
            # 每个在飞 processing 都被判卡死冲爆待确认池(2026-07-10 用户案:33 项待确认 27 是此噪声)。
            if not ((not live and age >= stuck_min) or age >= _STUCK_TTL_MIN):
                continue
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            stuck.append({"role": d.name, "id": p.stem, "age_min": int(age),
                          "from": o.get("from"), "title": o.get("title") or "",
                          "body": (o.get("task") or ""), "owner_dead": not live})
    return {"orphans": orphans, "stuck": stuck}

def _health_sweep_core(stuck_min=90, apply=False):
    """检测 + 处置。apply=False 只报(dry-run)。返回处置报告。去重:已在 todos.json 的 ref 不重投。"""
    a = _health_anomalies(stuck_min)
    tf = BUS / "todos.json"
    try:
        todos = json.loads(tf.read_text(encoding="utf-8")) if tf.exists() else []
        if not isinstance(todos, list):
            todos = []
    except Exception:
        todos = []
    seen = {t.get("ref") for t in todos if t.get("ref")}
    rep = {"orphans_rerouted": [], "orphans_to_todo": [], "stuck_to_todo": [], "skipped_dup": 0, "resolved_stale": 0, "skipped_empty": 0}

    # 自愈反收:撤销【异常已解决】的健康待办(避免过时派发——任务早已处理,待办却还在池里被派发)。
    # 仍存在的异常 id;不在其中=已解决→把对应 health:* 待办标"完成"(进历史,不再占活跃)。
    if apply and todos:
        live = {"health:stuck:" + s["id"] for s in a["stuck"]} | {"health:orphan:" + o["id"] for o in a["orphans"]}
        for t in todos:
            ref = t.get("ref") or ""
            if ref.startswith("health:") and t.get("status") != "完成" and ref not in live:
                t["status"] = "完成"; t["updated"] = _now()
                t["detail"] = (t.get("detail", "") + " 〔bus健康:异常已解决,自动关闭〕").strip()
                rep["resolved_stale"] += 1

    def _add_todo(title, detail, ref):
        if any(t.get("ref") == ref and t.get("status") != "完成" for t in todos):
            rep["skipped_dup"] += 1     # 已有【未完成】的同 ref 待办才算重复;已完成的允许新异常重新开
            return False
        todos.append({"id": "t-%d-%s" % (int(time.time() * 1000), os.urandom(2).hex()),
                      "title": title, "detail": detail, "source": "确认·bus健康", "priority": "中",
                      "status": "待办", "deadline": "", "created": _now(), "updated": _now(),
                      "task_id": None, "dispatched_to": None, "confirm_from": "bus健康", "ref": ref})
        seen.add(ref)
        return True

    for o in a["orphans"]:
        if o["owner"]:                       # 可自动追回:重路由到推断的 owner/done
            if apply:
                obj = o["obj"]
                obj["recovered_from_unknown"] = True
                obj["recovered_orig_to"] = obj.get("to")
                dest = BUS / o["owner"] / "done"
                dest.mkdir(parents=True, exist_ok=True)
                _write_json(dest / (o["id"] + ".json"), obj)
                try:
                    os.remove(o["path"])     # 移出 unknown/=天然去重,下轮不再检出
                except Exception:
                    pass
                _audit("health_reroute_orphan", id=o["id"], frm=o["from"], owner=o["owner"])
            rep["orphans_rerouted"].append({"id": o["id"], "from": o["from"], "owner": o["owner"]})
        else:                                # 推不出 owner:需人定,投待办
            _ob = o.get("obj") or {}
            if not (o.get("title") or "").strip() and not (_ob.get("task") or "").strip():
                rep["skipped_empty"] += 1    # 空指向:回执标题+正文皆空=无物可认领的死信,不投(否则冲爆待确认池)
            else:
                if apply:
                    _add_todo("孤儿回执需认领:from %s" % o["from"],
                              "回执丢进 unknown/、原请求方不可考。标题「%s」。要不要指认 owner/重投?" % o["title"][:80],
                              "health:orphan:" + o["id"])
                rep["orphans_to_todo"].append({"id": o["id"], "from": o["from"]})

    for s in a["stuck"]:                     # 卡死任务:需人定(重派/催/判死),不自动杀
        if not (s.get("title") or "").strip() and not (s.get("body") or "").strip():
            rep["skipped_empty"] += 1        # 空指向:任务标题+正文皆空=无物可判的噪声,不投
            continue
        if apply:
            _add_todo("任务卡死:%s 的 processing 已 %dmin" % (s["role"], s["age_min"]),
                      "任务 from=%s「%s」认领后久未完成,worker 可能死/卡。要不要重派/催/判死?" % (s["from"], s["title"][:80]),
                      "health:stuck:" + s["id"])
        rep["stuck_to_todo"].append({"role": s["role"], "id": s["id"], "age_min": s["age_min"]})

    if apply and (rep["orphans_to_todo"] or rep["stuck_to_todo"] or rep["resolved_stale"]):
        _write_json(tf, todos)               # 复用鲁棒落地(含 os.replace 重试)
    return rep

def cmd_health_sweep(args):
    """系统级 bus 健康巡检:孤儿回执自动追回、卡死任务投待办需人定。--apply 才动手,默认 dry-run。"""
    rep = _health_sweep_core(stuck_min=getattr(args, "stuck_min", 90) or 90, apply=getattr(args, "apply", False))
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def _stale_todos(threshold_min=1440):
    """扫 todos.json 里停"待"态(待办/待确认…)、停留 > threshold 的停滞项(防丢/回收提醒·Master 2026-07-02 提)。
    只报 状态+归属(source)+停留时长+标题+deadline —— 不碰业务内容。按停留时长降序。"""
    tf = BUS / "todos.json"
    out = []
    try:
        items = json.loads(tf.read_text(encoding="utf-8"))
    except Exception:
        return out
    for it in (items if isinstance(items, list) else []):
        if "待" not in (it.get("status") or ""):     # 只看未完成
            continue
        age_s = _age_seconds(it.get("updated") or it.get("created") or "")
        if age_s is None or int(age_s / 60) < threshold_min:
            continue
        out.append({"id": it.get("id"), "title": (it.get("title") or "")[:40],
                    "source": it.get("source"), "priority": it.get("priority"),
                    "status": it.get("status"), "deadline": it.get("deadline"),
                    "age_min": int(age_s / 60)})
    out.sort(key=lambda x: x["age_min"], reverse=True)
    return out


def _collect_health(stale_min=30, stuck_min=90, todo_stale_min=1440):
    """只读汇总 bus 健康各项指标(不改任何状态)。返回 dict,供 cmd_health 渲染 / WebUI 复用。"""
    now = time.time()
    role_dirs = [d for d in sorted(BUS.iterdir())
                 if d.is_dir() and not d.name.startswith((".", "_")) and d.name != "unknown"
                 and not _is_test_role(d.name)]
    h = {"as_of": _now(), "warns": []}
    # 1) 自醒(退役 role 跳过——不计掉线,否则误报)
    wr = _wait_proc_roles()
    retired_set = _retired_roles()
    armed = recent = stale = off = retired_n = parked_n = 0; stale_roles = []
    for d in role_dirs:
        if d.name in retired_set:
            retired_n += 1; continue
        stt, _ = _self_wake_state(d.name, wait_roles=wr)   # parked 先判(不依赖 presence),故不能因无 presence 提前 continue
        if stt == "parked": parked_n += 1; continue        # 🅿️休眠:故意暂离,不计掉线/未醒/告警
        if not (d / "presence.json").exists():
            continue
        if stt == "armed": armed += 1
        elif stt == "recent": recent += 1            # 有心跳无wait·瞬时态·不计告警
        elif stt == "stale": stale += 1; stale_roles.append(d.name)
        else: off += 1
    h["selfwake"] = {"armed": armed, "recent": recent, "stale": stale, "off": off, "parked": parked_n, "retired": retired_n, "stale_roles": stale_roles}
    if stale_roles: h["warns"].append("自醒掉线 %d(%s)" % (stale, ",".join(stale_roles)))
    # 2) 队列
    q = {"inbox": 0, "processing": 0, "held": 0, "failed": 0}
    held_l, failed_l = [], []
    for d in role_dirs:
        for k in q:
            qd = d / k
            if qd.exists():
                n = len(list(qd.glob("*.json"))); q[k] += n
                if k == "held" and n: held_l.append("%s:%d" % (d.name, n))
                if k == "failed" and n: failed_l.append("%s:%d" % (d.name, n))
    h["queues"] = dict(q, held_roles=held_l, failed_roles=failed_l)
    if held_l: h["warns"].append("held 待批 %d" % q["held"])
    if failed_l: h["warns"].append("failed 失败 %d" % q["failed"])
    # 3) 消息丢失巡检
    a = _health_anomalies(stuck_min)
    stale_inbox = []
    for d in role_dirs:
        for p in (d / "inbox").glob("*.json"):
            age = (now - p.stat().st_mtime) / 60
            if age > stale_min:
                stale_inbox.append({"role": d.name, "age_min": int(age)})
    h["loss"] = {"orphans": len(a["orphans"]), "stuck": a["stuck"], "stale_inbox": stale_inbox}
    if a["orphans"]: h["warns"].append("孤儿回执 %d" % len(a["orphans"]))
    if a["stuck"]: h["warns"].append("卡死任务 %d" % len(a["stuck"]))
    if stale_inbox: h["warns"].append("久滞 inbox %d" % len(stale_inbox))
    # 4) daemon 舰队
    try:
        daemons = json.loads((BUS / "fleet.json").read_text(encoding="utf-8")).get("daemons", [])
    except Exception:
        daemons = []
    fleet_roles = {d.get("role") for d in daemons if d.get("role")}
    dead = [r for r in fleet_roles if not _daemon_alive(r)]
    orphan_d = _orphan_daemon_pids(fleet_roles) if psutil else []
    h["fleet"] = {"expected": len(fleet_roles), "alive": len(fleet_roles) - len(dead),
                  "dead": dead, "orphans": ["%s(pid%s)" % (r, p) for p, r in orphan_d]}
    if dead: h["warns"].append("daemon 缺 %d(%s)" % (len(dead), ",".join(dead)))
    if orphan_d: h["warns"].append("孤儿 daemon %d" % len(orphan_d))
    # 5) WebUI
    owner = None; stale_webui = _stale_webui_pids(8799, 30) if psutil else []
    if psutil:
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.status == "LISTEN" and c.laddr and c.laddr.port == 8799 and c.pid:
                    owner = c.pid; break
        except Exception:
            pass
    h["webui"] = {"owner_pid": owner, "stale_dupes": len(stale_webui)}
    if not owner: h["warns"].append("WebUI 未在岗")
    if stale_webui: h["warns"].append("陈旧 WebUI %d" % len(stale_webui))
    # 6) edge-lock
    eh = _edge_holder()
    h["edge_lock"] = {"who": eh.get("who")} if eh else None
    # 7) 待办池停滞(防丢/回收提醒):停"待"态 > 阈值(默认24h)的项,按 状态+归属+停留时长
    st = _stale_todos(todo_stale_min)
    h["stale_todos"] = st
    if st: h["warns"].append("待办停滞 %d(>%dh)" % (len(st), todo_stale_min // 60))
    h["healthy"] = not h["warns"]
    return h


def cmd_health(args):
    """【只读】bus 健康总检:自醒 / 队列 / 消息丢失(孤儿·卡死·久滞)/ daemon 舰队 / WebUI / edge锁,
    一屏看全 + ⚠/✓ + 总判定。不改任何状态(要处置走 supervise / health-sweep --apply)。--json 出结构化。"""
    h = _collect_health(stale_min=getattr(args, "stale_min", 30) or 30,
                        stuck_min=getattr(args, "stuck_min", 90) or 90,
                        todo_stale_min=getattr(args, "todo_stale_min", 1440) or 1440)
    if getattr(args, "json", False):
        print(json.dumps(h, ensure_ascii=False, indent=1)); return
    L = ["=== Bus 健康检查 (as of %s) ===" % h["as_of"]]
    sw = h["selfwake"]
    L.append("\n[自醒]   在岗 %d / 待确认 %d / 掉线 %d / 未醒 %d / 退役 %d" % (sw["armed"], sw.get("recent", 0), sw["stale"], sw["off"], sw.get("retired", 0)))
    if sw["stale_roles"]: L.append("  ⚠ 掉线需重挂: " + ", ".join(sw["stale_roles"]))
    q = h["queues"]
    L.append("\n[队列]   inbox 待领 %d / processing 处理中 %d / held 待批 %d / failed 失败 %d"
             % (q["inbox"], q["processing"], q["held"], q["failed"]))
    if q["held_roles"]: L.append("  ⚠ 待批: " + ", ".join(q["held_roles"]))
    if q["failed_roles"]: L.append("  ⚠ 失败: " + ", ".join(q["failed_roles"]))
    ls = h["loss"]
    L.append("\n[消息丢失巡检]")
    L.append("  %s 孤儿回执(unknown/): %d" % ("⚠" if ls["orphans"] else "✓", ls["orphans"]))
    L.append("  %s 卡死任务(>%dmin): %d" % ("⚠" if ls["stuck"] else "✓", getattr(args, "stuck_min", 90) or 90, len(ls["stuck"])))
    for s in ls["stuck"]:
        L.append("       - %s/%s %dmin「%s」" % (s["role"], s["id"][-6:], s["age_min"], (s["title"] or "")[:28]))
    L.append("  %s 久滞 inbox(>%dmin): %d" % ("⚠" if ls["stale_inbox"] else "✓", getattr(args, "stale_min", 30) or 30, len(ls["stale_inbox"])))
    for s in ls["stale_inbox"]:
        L.append("       - %s %dmin" % (s["role"], s["age_min"]))
    f = h["fleet"]
    L.append("\n[daemon] fleet 期望 %d / 在岗 %d / 孤儿 %d" % (f["expected"], f["alive"], len(f["orphans"])))
    if f["dead"]: L.append("  ⚠ 期望但未在岗: " + ", ".join(f["dead"]))
    if f["orphans"]: L.append("  ⚠ 孤儿 daemon: " + ", ".join(f["orphans"]))
    w = h["webui"]
    L.append("\n[WebUI]  8799 %s / 陈旧重复 %d" % (("在岗 pid=%s" % w["owner_pid"]) if w["owner_pid"] else "⚠ 没人占", w["stale_dupes"]))
    L.append("\n[edge锁] " + ("持有者=%s" % h["edge_lock"]["who"] if h["edge_lock"] else "空闲"))
    stt = h.get("stale_todos") or []
    L.append("\n[待办池停滞] %s 停「待」态 >%dh: %d" % ("⚠" if stt else "✓", (getattr(args, "todo_stale_min", 1440) or 1440) // 60, len(stt)))
    for s in stt[:10]:
        d = ("·%dh" % (s["age_min"] // 60)) if s["age_min"] >= 60 else ("·%dmin" % s["age_min"])
        L.append("       - [%s] 归属=%s 停留%s%s「%s」" % (s.get("status"), s.get("source"), d, ("·⏰" + s["deadline"]) if s.get("deadline") else "", s["title"]))
    if len(stt) > 10: L.append("       … 另 %d 条(全量走 `ab stale`)" % (len(stt) - 10))
    L.append("\n" + "-" * 52)
    L.append("总判定: ✓ 全绿,bus 健康" if h["healthy"] else "总判定: ⚠ %d 项需注意 — %s" % (len(h["warns"]), "; ".join(h["warns"])))
    print("\n".join(L))


def cmd_stale(args):
    """【只读·防丢回收提醒】列待办池停"待"态 > 阈值(默认24h·--hours 调)的停滞项,
    按 状态+归属(source)+停留时长 降序,只报不碰业务内容。Master/MasterAgent 定期跑此回收(Master 2026-07-02 提)。"""
    th = (getattr(args, "hours", 24) or 24) * 60
    st = _stale_todos(th)
    if getattr(args, "json", False):
        print(json.dumps(st, ensure_ascii=False, indent=1)); return
    if not st:
        print("✓ 待办池无停滞项(停「待」态 >%dh = 0)" % (th // 60)); return
    print("⚠ 待办池停滞项(停「待」态 >%dh):%d 条 · 按停留时长降序 · 只报状态/归属/时长(不含业务内容)" % (th // 60, len(st)))
    print("-" * 72)
    for s in st:
        d = ("%dh" % (s["age_min"] // 60)) if s["age_min"] >= 60 else ("%dmin" % s["age_min"])
        dl = ("⏰" + s["deadline"] + " ") if s.get("deadline") else ""
        print("[%s] 停留%-7s 归属=%-12s %s「%s」" % (s.get("status"), d, s.get("source"), dl, s["title"]))
        print("        id=%s  pri=%s" % (s.get("id"), s.get("priority")))
    print("-" * 72)
    print("处置:派发/跟催走 send;需用户拍板走 ask-user;已无效则标完成。")


# ── 长期任务线看板(2026-07-06,Master 提·用户要"展示")──────────────────────────────
# tasklines.json = 运行态磁盘文件(gitignore·不进bus核心git=洁净基准[含业务明细+owner];落磁盘
# =防compact+跨会话·Master 拍板存储方案A)。每条决策点带 ref → 对应 todos.json(ask-user 待办池)
# 条目,看板据 ref 显示 已答/待答。展示两阶段:阶段1=本 CLI(ab tasklines);阶段2=Master数分 WebUI 面板。
def _tasklines_path():
    return BUS / "tasklines.json"

def _load_tasklines():
    p = _tasklines_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        tl = d.get("tasklines", []) if isinstance(d, dict) else []
        return tl if isinstance(tl, list) else []
    except Exception:
        return []

def _decision_status_map():
    """ref → todos.json 里对应条目的 status(决策点是否已被用户答/标完成)。"""
    f = BUS / "todos.json"
    m = {}
    try:
        items = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        for it in (items if isinstance(items, list) else []):
            r = it.get("ref")
            if r:
                m[r] = it.get("status")
    except Exception:
        pass
    return m

def cmd_tasklines(args):
    """【只读·长期任务线看板】列各 owner 的持续/长期任务线:状态/owner/节奏/待决策数/更新日。
    --id X 看单条详情(依赖/决策点/真相源)。数据源 tasklines.json(持久·跨会话·防compact)。
    决策点据 ref 交叉 todos.json 显示 已答/待答(决策走 ask-user 待办池,不滚走)。"""
    tls = _load_tasklines()
    dsm = _decision_status_map()
    _done = ("完成", "已完成", "done")
    if getattr(args, "json", False):
        print(json.dumps(tls, ensure_ascii=False, indent=1)); return
    if not tls:
        print("(长期任务线看板为空 · tasklines.json 无数据)"); return
    tid = getattr(args, "id", None)
    if tid:
        tl = next((t for t in tls if t.get("id") == tid), None)
        if not tl:
            print("无此任务线:%s(ab tasklines 看全部)" % tid); return
        print("═" * 72)
        print("【%s】%s" % (tl.get("id"), tl.get("title")))
        print("  owner=%s   节奏=%s   更新=%s" % (tl.get("owner"), tl.get("cadence"), tl.get("updated")))
        print("  状态:%s" % (tl.get("status") or ""))
        if tl.get("current"):
            print("  当前:%s" % tl.get("current"))
        if tl.get("deps"):
            print("  依赖:")
            for d in tl["deps"]:
                print("    - %s" % d)
        if tl.get("decisions"):
            print("  待决策点(→ 待办池):")
            for de in tl["decisions"]:
                stt = dsm.get(de.get("ref"))
                mark = "✓已答" if stt in _done else ("⧗待用户" if stt else "·未投池")
                print("    [%s] %s" % (mark, de.get("q")))
                if de.get("note"):
                    print("            ↳ %s" % de["note"])
        if tl.get("truth_source"):
            print("  真相源:%s" % tl.get("truth_source"))
        print("═" * 72)
        return
    print("━" * 78)
    print("长期任务线看板 · %d 条 · 数据源 tasklines.json(持久·跨会话·防compact)" % len(tls))
    print("━" * 78)
    for t in tls:
        decs = t.get("decisions", [])
        pending = sum(1 for d in decs if dsm.get(d.get("ref")) not in _done)
        dbadge = ("⧗待决策 %d/%d" % (pending, len(decs))) if decs else "—"
        print("【%s】%s" % (t.get("id"), t.get("title")))
        print("      owner=%-14s 节奏=%-16s 决策=%-10s 更新=%s"
              % (t.get("owner"), t.get("cadence"), dbadge, t.get("updated")))
        print("      状态:%s" % (t.get("status") or ""))
    print("━" * 78)
    print("详情:ab tasklines --id <TL-x>   ·   决策点走 ask-user 待办池(ab stale / WebUI 可看)")


# ── 退役 agent 档案馆/花名册(2026-06-29,用户提:番号下掉+记录保留+可复用恢复)──────────
# retired_roster.json = list 归档记录(与 agents.json 活跃在册分开);退役=从 agents.json 摘除番号
# (selfwake/who/roster 不再显示、不占活跃名额)+ mailbox 移 _retired/ 留痕 + 完整记录进档案馆。
def _retired_roster_path():
    return BUS / "retired_roster.json"

def _load_retired_roster():
    p = _retired_roster_path()
    try:
        r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        return r if isinstance(r, list) else []
    except Exception:
        return []

def cmd_retire(args):
    """把一个活跃 role 归档进退役档案馆:① 记录存 retired_roster.json(role/会话ids/起退时间/贡献记忆指针/
    退役原因/接替者/状态)② 从 agents.json 摘除番号(下掉、不占活跃名额、可复用)③ mailbox 移 _retired/ 留痕。
    会话 jsonl 本身不动(只记 id)。"""
    role = args.role
    a = _load_agents()
    rec_src = a.get(role, {}) if isinstance(a.get(role), dict) else {}
    entry = {
        "role": role,
        "session_ids": [s.strip() for s in (args.session_ids or "").split(",") if s.strip()],
        "registered": rec_src.get("registered", ""),
        "retired": rec_src.get("retired") or _now()[:10],
        "succeeded_by": args.succeeded_by or rec_src.get("succeeded_by"),
        "reason": args.reason or "",
        "contribution_memory": args.memory or "",
        "desc": rec_src.get("desc", ""),
        "caps": rec_src.get("caps", []),
        "status": "archived",
        "archived_at": _now(),
    }
    roster = _load_retired_roster()
    roster.append(entry)
    _write_json(_retired_roster_path(), roster)
    # 番号下掉:从 agents.json 移除
    if role in a:
        del a[role]; _save_agents(a)
    # mailbox 移 _retired/ 留痕(_retired 前缀→_list_roles 不扫,从活跃视图消失)
    src = BUS / role
    moved = False
    if src.is_dir():
        dst_parent = BUS / "_retired"; dst_parent.mkdir(exist_ok=True)
        dst = dst_parent / role
        try:
            if dst.exists():
                import shutil as _sh; _sh.rmtree(dst, ignore_errors=True)
            os.replace(str(src), str(dst)); moved = True
        except Exception as e:
            print("  ⚠ mailbox 移动失败(已记档+下掉番号):%s" % e)
    # 从 fleet 移除(若在)
    try:
        f = json.loads((BUS / "fleet.json").read_text(encoding="utf-8"))
        n0 = len(f.get("daemons", []))
        f["daemons"] = [d for d in f.get("daemons", []) if d.get("role") != role]
        if len(f["daemons"]) != n0:
            _write_json(BUS / "fleet.json", f)
    except Exception:
        pass
    _audit("retire", role=role, succeeded_by=entry["succeeded_by"], reason=entry["reason"][:60])
    print(json.dumps({"retired": role, "番号下掉": role not in _load_agents(),
                      "mailbox移_retired": moved, "档案馆": str(_retired_roster_path()),
                      "session_ids": entry["session_ids"], "status": "archived·可复用"}, ensure_ascii=False, indent=1))

def cmd_revive(args):
    """可复用回路:从退役档案馆把番号调回 agents.json 活跃在册(恢复 desc/caps)+ mailbox 移回 + 标记 revived。"""
    role = args.role
    roster = _load_retired_roster()
    idx = None
    for i in range(len(roster) - 1, -1, -1):   # 取最近一条 archived
        if roster[i].get("role") == role and roster[i].get("status") == "archived":
            idx = i; break
    if idx is None:
        print(json.dumps({"error": "档案馆无 %s 的 archived 记录" % role}, ensure_ascii=False)); return
    rec = roster[idx]
    a = _load_agents()
    a[role] = {"registered": _now(), "desc": rec.get("desc", ""), "caps": rec.get("caps", []),
               "revived_from_retired": rec.get("retired")}
    _save_agents(a)
    # mailbox 移回
    src = BUS / "_retired" / role
    if src.is_dir():
        try: os.replace(str(src), str(BUS / role))
        except Exception as e: print("  ⚠ mailbox 移回失败:%s" % e)
    rec["status"] = "revived"; rec["revived_at"] = _now()
    _write_json(_retired_roster_path(), roster)
    _audit("revive", role=role)
    print(json.dumps({"revived": role, "回活跃在册": role in a, "状态": "revived"}, ensure_ascii=False, indent=1))

def cmd_retired(args):
    """列退役档案馆(花名册):番号/退役时间/接替者/原因/会话ids/贡献记忆/状态。"""
    roster = _load_retired_roster()
    if not roster:
        print("退役档案馆为空。"); return
    print("退役 agent 档案馆(%d 条):" % len(roster))
    print("-" * 78)
    for r in roster:
        print("⊘ %-14s 退役%s  接替=%s  状态=%s" % (
            r.get("role"), r.get("retired", ""), r.get("succeeded_by") or "-", r.get("status", "")))
        print("   原因: %s" % (r.get("reason", "") or "-")[:90])
        print("   会话ids: %s" % (", ".join(r.get("session_ids", [])) or "-"))
        print("   贡献记忆: %s | 专长: %s" % (r.get("contribution_memory", "") or "-", (r.get("desc", "") or "-")[:50]))
    print("-" * 78)
    print("复用某番号:ab revive --role <名>(从档案馆调回 agents.json 活跃在册)")


def cmd_supervise(args):
    """daemon 自愈 + 期望态对账:读 agent_bus/fleet.json 的期望 daemon 清单,重启其中【已死】的(存活跳过),
    并【回收孤儿 daemon】(worker_daemon 进程但 role 已不在 fleet —— 从 fleet 摘角色后的残留)。
    只管清单里列出的 role(不列的不动)。靠 start_worker.ps1 拉起(幂等 + tab/daemon 互斥)。
    单次执行;周期化由常驻 WebUI 的 supervisor 线程或计划任务调用本命令。"""
    try:
        fleet = json.loads((BUS / "fleet.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"error": "读 fleet.json 失败: %s" % e}, ensure_ascii=False)); return
    daemons = fleet.get("daemons", [])
    sw = BUS / "start_worker.ps1"
    actions = []
    for d in daemons:
        role = d.get("role")
        if not role:
            continue
        if _daemon_alive(role):
            actions.append(("活着跳过", role)); continue
        cwd = d.get("cwd") or cfg("default_worker_cwd")
        wt = d.get("wait_timeout", 300)
        if args.dry_run:
            actions.append(("将重启(dry-run)", role)); continue
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(sw),
                                "-Role", role, "-Cwd", str(cwd), "-WaitTimeout", str(wt)],
                               capture_output=True, text=True, timeout=40)
            out = (r.stdout or "").strip()
            actions.append(("已拉起" if "STARTED" in out else ("被拒/跳过:" + out), role))
        except Exception as e:
            actions.append(("重启失败:%s" % e, role))
    # 期望态对账:回收【孤儿 daemon】——worker_daemon 进程但 role 已不在 fleet(从 fleet 摘角色后残留的)。
    # 只杀 python worker_daemon 进程;前台 CLI(非 worker_daemon)/dispatch_agent 一律不碰。
    fleet_roles = {d.get("role") for d in daemons if d.get("role")}
    for pid, role in _orphan_daemon_pids(fleet_roles):
        if args.dry_run:
            actions.append(("孤儿daemon将回收(dry-run,pid=%s)" % pid, role)); continue
        try:
            psutil.Process(pid).kill()
            _audit("reap_orphan_daemon", role=role, pid=pid)
            actions.append(("孤儿daemon已回收(pid=%s,role不在fleet)" % pid, role))
        except Exception as e:
            actions.append(("孤儿daemon回收失败(pid=%s):%s" % (pid, e), role))
    # 重复 WebUI 守卫:清掉没占 8799 的陈旧 server.py 实例(31468 那类)。安全:只在能确认端口归属时动、不杀在岗的。
    for pid, age in _stale_webui_pids(8799, 30):
        if args.dry_run:
            actions.append(("陈旧WebUI将清(dry-run,pid=%s,age=%ss)" % (pid, age), "WebUI")); continue
        try:
            psutil.Process(pid).kill()
            _audit("reap_stale_webui", pid=pid, age=age)
            actions.append(("陈旧WebUI已清(pid=%s,age=%ss,未占8799)" % (pid, age), "WebUI"))
        except Exception as e:
            actions.append(("陈旧WebUI清理失败(pid=%s):%s" % (pid, e), "WebUI"))
    # bus 健康巡检(孤儿回执自动追回 / 卡死任务投待办):节奏比 supervise(90s)长——加 600s 时间闸,避免噪音/空耗。
    try:
        marker = BUS / ".health_sweep_last"
        last = marker.stat().st_mtime if marker.exists() else 0
        if time.time() - last >= 600:
            rep = _health_sweep_core(stuck_min=90, apply=not args.dry_run)
            if not args.dry_run:
                marker.write_text(_now(), encoding="utf-8")
            nfix = len(rep["orphans_rerouted"]); ntodo = len(rep["orphans_to_todo"]) + len(rep["stuck_to_todo"])
            if nfix or ntodo:
                actions.append(("健康巡检:追回孤儿回执 %d、投待办 %d%s" % (
                    nfix, ntodo, "(dry-run)" if args.dry_run else ""), "bus健康"))
    except Exception as e:
        actions.append(("健康巡检异常:%s" % e, "bus健康"))
    if not actions:
        print("supervise: fleet.json 无 daemon 条目,且无孤儿 daemon / 陈旧 WebUI / 健康异常"); return
    for a, r in actions:
        print("[%s] %s" % (a, r))


def cmd_answer(args):
    """待办回路(闭环 ask-user,补 capture-only 缺口):用户答复某条🙋待确认项 →
    答复【自动路由回提问的 agent】(confirm_from)让它接着干 + 标该待办完成。
    谁替用户跑:协调位(Master/MasterAgent)转达,或 WebUI 回复框(MasterUI 接 /api 调本命令)。"""
    tf = BUS / "todos.json"
    try:
        todos = json.loads(tf.read_text(encoding="utf-8")) if tf.exists() else []
        if not isinstance(todos, list):
            todos = []
    except Exception:
        todos = []
    item = next((t for t in todos if t.get("id") == args.todo), None)
    if not item:
        print(json.dumps({"error": "无此待办 id: %s" % args.todo}, ensure_ascii=False)); return
    target = item.get("confirm_from")
    if not target or target in ("bus健康", "unknown"):
        print(json.dumps({"error": "该待办无可回的提问方(confirm_from=%s),无处回路" % target}, ensure_ascii=False)); return
    frm = args.frm or "用户"
    _warn_unreachable(target)   # 提问方不在岗就告警;答复仍写进它 inbox,它上线即见
    tid = _gen_id(target)
    ref = item.get("ref")
    task = {"id": tid, "from": frm, "to": target,
            "title": "[待办答复] " + (item.get("title") or "")[:60],
            "task": "用户对你提问《%s》的答复:%s%s" % (
                item.get("title") or "", args.reply, ("\n(原任务 ref: %s)" % ref) if ref else ""),
            "created": _now(), "status": "pending", "answer_of": item.get("id"), "ref": ref}
    _write_json(BUS / target / "inbox" / (tid + ".json"), task)
    _audit("answer", todo=args.todo, to=target, frm=frm)
    item["status"] = "完成"; item["updated"] = _now()
    item["detail"] = (item.get("detail", "") + " 〔已答复→%s:%s〕" % (target, args.reply[:40])).strip()
    _write_json(tf, todos)
    print("答复已路由给 %s(任务 %s),待办 %s 标完成。" % (target, tid, args.todo))


def cmd_route(args):
    """对口路由助手(强化专一性·防全科自接):按关键词在花名册 desc/caps 找【最对口】角色,
    推荐派给谁——命中多优先、在岗优先、退役/测试排除。派活前先 route 找对口,别自接跨领域活/别乱广播。"""
    terms = [t.lower() for t in (args.keywords or []) if t.strip()]
    if not terms:
        print("用法: ab route <关键词...>(按专长/能力找对口角色)"); return
    agents = _load_agents()
    retired = _retired_roles()
    wait_roles = _wait_proc_roles()
    scored = []
    for role, v in agents.items():
        if role in retired or _is_test_role(role) or not isinstance(v, dict):
            continue
        hay = (role + " " + (v.get("desc") or "") + " " + " ".join(v.get("caps", []))).lower()
        score = sum(hay.count(t) for t in terms)
        if score > 0:
            st = _self_wake_state(role, wait_roles=wait_roles)[0]
            scored.append((score, 0 if st == "armed" else 1, role, st, (v.get("desc") or "")[:58]))
    if not scored:
        print("无对口专责命中「%s」——考虑广播,或 ab register 新专家。" % " ".join(terms)); return
    scored.sort(key=lambda x: (-x[0], x[1]))   # 命中多优先、在岗优先
    mark = {"armed": "✓在岗", "recent": "◐待确认", "stale": "⚠掉线", "off": "—未醒", "parked": "🅿️休眠"}
    print("对口路由推荐(命中「%s」):" % " ".join(terms))
    for i, (sc, _o, role, st, desc) in enumerate(scored[:5]):
        tag = " ← 推荐(对口+在岗)" if i == 0 and st == "armed" else (" ← 最对口但掉线,先让它重挂" if i == 0 else "")
        print("  %-12s %s 命中%d  %s%s" % (role, mark.get(st, "?"), sc, desc, tag))
    print("→ 对口直派:ab send --to <角色> --from <你> ...(别自接跨领域、别乱广播)")


# ---------------- self-wake / park / 进程祖先(已抽到 bus/selfwake.py · 见文件头 import)----------------


def cmd_whoami(args):
    """反查【本会话/进程】绑定的 role(同 cwd 多 role 时不靠 cwd 猜):
    ① 按 env CLAUDE_CODE_SESSION_ID 匹配各 role presence.session_id(权威);
    ② fallback 按 claude_pid(本进程的 claude 祖先 pid)匹配;
    查到→打印 role(exit 0);查不到→空 + exit 2(=真新未绑定 tab,恢复入口据此才默认 MasterAgent)。
    用途:新 CLI 恢复入口/PostCompact hook 先 whoami 认清真身份。"""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    roles = _list_roles()
    def _pres(r):
        pf = BUS / r / "presence.json"
        try:
            return json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else None
        except Exception:
            return None
    hit = None; how = None
    if sid:
        for r in roles:
            pr = _pres(r)
            if pr and pr.get("session_id") == sid:
                hit = r; how = "session_id"; break
    if not hit:
        anc = _ancestor_pids()
        for r in roles:
            pr = _pres(r)
            if pr and pr.get("claude_pid") in anc:
                hit = r; how = "claude_pid(祖先)"; break
    if getattr(args, "json", False):
        print(json.dumps({"role": hit, "matched_by": how, "session_id": sid}, ensure_ascii=False))
    else:
        print(hit or "")
    if not hit:
        sys.exit(2)


def cmd_selfwake(args):
    """自醒检测:列出每个 role 的事件自醒是否在岗(daemon watchdog / 会话后台 wait)。"""
    armed = recent = stale = off = retired = pseudo = parked = 0
    wait_roles = _wait_proc_roles()   # 一次性预扫,循环内复用
    retired_set = _retired_roles()
    print("%-14s %-8s %s" % ("role", "自醒", "说明"))
    print("-" * 78)
    for role in _list_roles():
        plabel = _pseudo_role_label(role)
        if plabel:                     # 伪角色/系统桶:非执行体,不计掉线/未醒,单列标注
            print("%-14s %-8s %s" % (role, "◇伪角色", plabel))
            pseudo += 1; continue
        if role in retired_set:        # 退役:不计掉线(否则误报),只标注
            sb = _load_agents().get(role, {}).get("succeeded_by")
            print("%-14s %-8s %s" % (role, "⊘退役", "已退役" + ("·接替=" + sb if sb else "") + "(留痕不删,不派活)"))
            retired += 1; continue
        st, detail = _self_wake_state(role, wait_roles=wait_roles)
        mark = {"armed": "✓在岗", "recent": "◐待确认", "stale": "⚠掉线", "off": "—未醒", "parked": "🅿️休眠"}.get(st, "?")
        print("%-14s %-8s %s" % (role, mark, detail))
        armed += st == "armed"; recent += st == "recent"; stale += st == "stale"; off += st == "off"; parked += st == "parked"
    print("-" * 78)
    print("自醒在岗 %d / 待确认 %d / 掉线 %d / 未醒 %d / 休眠 %d / 退役 %d / 伪角色 %d" % (armed, recent, stale, off, parked, retired, pseudo))
    if recent:
        print("◐待确认=有心跳无wait进程(刚fire待重挂/交互中·瞬时非告警);持续则下轮转掉线。")
    if stale:
        print("⚠ 掉线需重挂:daemon→`supervise`/`start_worker`;协调位会话→重新挂后台 `wait --role <自己>`。")


def _guard_prev_path():
    return BUS / ".selfwake_guard_prev.json"


def _load_guard_prev():
    """上一轮巡检判 stale 的 role 集 + 时间戳(用于连续2次确认降噪)。"""
    p = _guard_prev_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return set(d.get("roles", [])), d.get("ts")
    except Exception:
        return set(), None


def _save_guard_prev(roles):
    try:
        tmp = _guard_prev_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "roles": sorted(roles)}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, _guard_prev_path())
    except Exception:
        pass


def cmd_selfwake_guard(args):
    """【B·自醒巡检报警·2026-07-08 Master拍板】周期扫自醒,发现掉自醒(claude活/无wait/心跳超期)的
    agent → ①往其 inbox 投一条【重挂提醒】(dedup:已有未领的提醒就不重投)②WinRT toast 报警(汇总)。
    只报警+提醒,不代挂(外部无法替前台CLI重挂,见方案评估)。--dry-run 只报不动。
    周期驱动:可由 WebUI 常驻线程 / cron 每几分钟跑一次,把"18h没人发现"变"几分钟内报警"。"""
    wait_roles = _wait_proc_roles()
    retired_set = _retired_roles()
    stale = []
    for role in _list_roles():
        if _pseudo_role_label(role) or role in retired_set or _is_parked(role):
            continue                          # parked=故意暂离 → 不巡检报警(用户手动关 tab 的关键配套)
        st, detail = _self_wake_state(role, wait_roles=wait_roles)
        if st == "stale":
            stale.append((role, detail))
    # 2-scan 确认降噪(2026-07-08):cycling agent 在 post-fire 无-wait 瞬间窗口会被误判 stale(wait 不刷
    # 心跳→>1h没re-arm的健康cycling者也显stale)。只对【连续2次扫描都掉线】的报警=瞬时假阳性(1次)被滤掉、
    # 真掉线(持续)照报。代价:检测延到~2轮(~6min),仍远好于原来靠人偶然发现。
    cur = {r for r, _ in stale}
    prev, prev_ts = _load_guard_prev()
    prev_fresh = prev_ts is not None and (time.time() - prev_ts) < 900  # 仅15min内的上轮基线算数
    confirmed = (cur & prev) if prev_fresh else set()
    _save_guard_prev(cur)
    unconfirmed = cur - confirmed
    if not stale:
        print("✓ 自醒巡检:全员在岗,无掉自醒"); return
    if not confirmed:
        print("自醒巡检:%d 疑掉线,均【首次/未连续确认】→本轮不报(待下轮确认,消瞬时假阳性):%s"
              % (len(stale), "、".join(sorted(cur))))
        return
    reminded, skipped = [], []
    marker = "[自醒守卫·请重挂]"
    stale_confirmed = [(r, d) for r, d in stale if r in confirmed]
    for role, detail in stale_confirmed:
        inbox = BUS / role / "inbox"
        # dedup:该 role inbox 已有未领的守卫提醒 → 跳过不重投
        dup = False
        try:
            for f in inbox.glob("*.json"):
                try:
                    if marker in (json.loads(f.read_text(encoding="utf-8")).get("title", "") or ""):
                        dup = True; break
                except Exception:
                    continue
        except Exception:
            pass
        if dup:
            skipped.append(role); continue
        if args.dry_run:
            reminded.append(role); continue
        tid = _gen_id(role)
        task = {
            "id": tid, "from": "MasterAgent", "to": role,
            "title": marker + " 你掉了自醒",
            "task": ("自醒巡检【连续2次】发现你掉了自醒(%s)。看到即请【后台 run_in_background】跑 "
                     "`python C:/apphome/agent_fleet/agent_bus/agentbus.py wait --role %s --timeout 3600`,"
                     "再 selfwake 验在岗(别用 shell &)。" % (detail, role)),
            "created": _now(), "status": "pending",
        }
        _write_json(inbox / (tid + ".json"), task)
        reminded.append(role)
    _audit("selfwake_guard", stale=len(stale), confirmed=len(confirmed), reminded=len(reminded))
    if not args.dry_run and reminded:
        _fire_toast("⚠ %d 个 agent 掉自醒(连续2次确认)" % len(confirmed),
                    "已提醒重挂:" + "、".join(reminded) + ("(另%d已提醒过)" % len(skipped) if skipped else ""))
    print("⚠ 自醒巡检:%d 掉线(%d 连续确认 / %d 首次待下轮) → %s%d 已投重挂提醒%s%s" % (
        len(stale), len(confirmed), len(unconfirmed), ("[dry-run] " if args.dry_run else ""), len(reminded),
        (" · %d 已提醒过跳过" % len(skipped)) if skipped else "",
        "" if args.dry_run else " + toast报警"))
    for role, detail in stale:
        print("   - %s%s:%s" % (role, "" if role in confirmed else "(待确认)", detail))


def cmd_dispatch(args):
    """派一个【一次性 ad-hoc agent】去做单条临时任务:有总线沟通能力、不监听队列、干完即逝、可重新派。
    与 send(投进某 role 的队列等它领)不同 —— dispatch 立刻 detached 起一个一次性执行体(claude -p),
    跑完把结果回填到派发方 <from>/done/<id>(用 `replies --role <from>` 收)。失败就再 dispatch 一个。"""
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    tid = _gen_id("adhoc")
    cwd = args.cwd or cfg("default_worker_cwd")
    rec = {
        "id": tid, "from": frm, "name": args.name or "adhoc",
        "title": args.title or "", "task": args.task, "cwd": cwd,
        "timeout": args.timeout, "created": _now(), "kind": "adhoc",
    }
    dd = BUS / "_dispatch"
    dd.mkdir(parents=True, exist_ok=True)
    _write_json(dd / (tid + ".json"), rec)
    _audit("dispatch", id=tid, frm=frm, name=rec["name"], title=rec["title"])
    da = str(Path(__file__).parent / "dispatch_agent.py")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen([sys.executable, da, "--id", tid], creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(BUS))
    except Exception as e:
        print(json.dumps({"error": "dispatch 起一次性 agent 失败: %s" % e}, ensure_ascii=False)); return
    print(tid)


# ---------------- Edge 操作互斥锁(CDP 焦点冲突止血,v1.2)----------------
# 多 agent 挂同一 Edge 操作云文档时 OS 焦点独占→串台。约定:Edge/CDP 操作前 edge-acquire、用完 edge-release。
# bus 放 edge.lock(O_EXCL 原子抢);抢不到 watchdog 等释放(不轮询);stale 回收(持锁者进程死/超 ttl 强制抢)。
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
                try: lp.unlink()
                except Exception: pass
                _audit("edge_steal", who=who, victim=(holder or {}).get("who"))
                continue  # 抢占陈旧锁后重试
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


# 会话标记(2026-07-07 用户提:ab sessions 仅元数据模块要能打标记·标"可恢复"等)。
# session_tags.json = 运行态磁盘文件(gitignore·不进 bus 核心 git);按 session-id 存
# {tags:[...], note, cwd, title, tagged_by, tagged_at}。标时顺带快照 cwd/title,便于会话 jsonl 日后
# 被归档/prune 移出 projects/ 后,--tagged 仍能列出并给出 resume 所需的 cwd。
def _session_tags_path():
    return BUS / "session_tags.json"


def _load_session_tags():
    p = _session_tags_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_session_tags(d):
    p = _session_tags_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _bus_ids_and_cwds():
    """(bus会话id集, bus cwd集) —— 用于判一个历史会话是不是【bus 会话】。
    bus id = 任一 presence.json 的 session_id(活/停都算,文件持久)+ 退役档案里记的 session_ids;
    bus cwd = 任一 presence.json 的 cwd(bus agent 跑活的目录,即各角色自举的代码目录)。
    判定:session_id∈ids 或 cwd∈cwds → bus(含 agent 的旧会话);否则非bus(用户个人会话,如个人项目目录下的)。"""
    ids, cwds = set(), set()
    try:
        for pf in BUS.glob("*/presence.json"):
            try:
                d = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("session_id"):
                ids.add(d["session_id"])
            if d.get("cwd"):
                cwds.add(os.path.normcase(d["cwd"]))
    except Exception:
        pass
    try:
        for e in _load_retired_roster():
            for sid in (e.get("session_ids", []) or []):
                ids.add(sid)
    except Exception:
        pass
    return ids, cwds


def _live_session_roles():
    """{session_id: role} —— 当前【在运行】的会话:presence.json 有 session_id 且其 claude_pid/daemon_pid
    进程还活着。用于把"死的历史 jsonl"和"正在跑的会话"区分开(活死实时算,不存,进程一死即翻'死')。"""
    live = {}
    try:
        for pf in BUS.glob("*/presence.json"):
            try:
                d = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                continue
            sid = d.get("session_id")
            if not sid:
                continue
            alive = False
            if psutil is not None:
                for k in ("claude_pid", "daemon_pid"):
                    pid = d.get(k)
                    if pid and psutil.pid_exists(pid):
                        alive = True
                        break
            else:
                alive = True  # 没 psutil 不敢判死,保守当活
            if alive:
                live[sid] = d.get("role")
    except Exception:
        pass
    return live


def _session_meta(sid):
    """按 session-id 在 ~/.claude/projects 找该会话 jsonl,读头部取 (cwd, title)。找不到→(None,None)。"""
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return None, None
    for pdir in root.iterdir():
        f = pdir / (sid + ".jsonl")
        if not f.exists():
            continue
        cwd = title = label = None
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, ln in enumerate(fh):
                    if i > 80:
                        break
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue
                    if not cwd and r.get("cwd"):
                        cwd = r.get("cwd")
                    if not title and r.get("type") == "ai-title":
                        title = r.get("aiTitle")
                    if not label and r.get("type") == "user":
                        m = r.get("message", {})
                        c = m.get("content") if isinstance(m, dict) else None
                        if isinstance(c, str):
                            label = c
                        elif isinstance(c, list):
                            label = " ".join(b.get("text", "") for b in c
                                              if isinstance(b, dict) and b.get("type") == "text")
                    if cwd and title:
                        break
        except Exception:
            pass
        return cwd, ((title or label or "").strip() or None)
    return None, None


def cmd_sessions(args):
    """列 Claude Code 历史会话(~/.claude/projects/<cwd编码>/<session-id>.jsonl),仅元数据、可【打标记】。
    - 列表:ab sessions [--project X] [--limit N] [--tagged](只看已标记)[--json];标记的会话显示 🏷标签。
    - 打标记:ab sessions --tag <会话id> [--label 可恢复] [--note "..."](默认标签=可恢复;顺带快照 cwd/title)。
    - 撤标记:ab sessions --untag <会话id> [--label X](不带 --label 撤该会话全部标签)。
    给 UI"可恢复会话"视图提供数据;真正 resume 在终端跑 `claude --resume <id>`(UI 只展示+给命令)。"""
    import datetime

    # ── 打标记 / 撤标记(mutate 分支)──
    sid_tag = getattr(args, "tag", None)
    sid_untag = getattr(args, "untag", None)
    if sid_tag or sid_untag:
        tags = _load_session_tags()
        label = (getattr(args, "label", None) or "").strip()
        if sid_tag:
            sid = sid_tag
            entry = tags.get(sid) or {"tags": [], "note": "", "cwd": "", "title": "",
                                      "tagged_by": "", "tagged_at": ""}
            lab = label or "可恢复"
            if lab not in entry["tags"]:
                entry["tags"].append(lab)
            if getattr(args, "note", None):
                entry["note"] = args.note.strip()
            cwd, title = _session_meta(sid)          # 快照元数据(防日后归档丢)
            if cwd:
                entry["cwd"] = cwd
            if title:
                entry["title"] = title[:70]
            entry["tagged_by"] = _resolve_self_role() or os.environ.get("AGENTBUS_ROLE", "unknown")
            entry["tagged_at"] = _now()
            tags[sid] = entry
            _save_session_tags(tags)
            _audit("session_tag", id=sid, label=lab)
            print("已标记会话 %s:🏷%s%s%s" % (
                sid, "/".join(entry["tags"]),
                ("  note=" + entry["note"]) if entry["note"] else "",
                ("  cwd=" + entry["cwd"]) if entry.get("cwd") else "  (jsonl 不在 projects/·可能已归档)" if not cwd else ""))
        else:
            sid = sid_untag
            if sid in tags:
                if label:
                    tags[sid]["tags"] = [t for t in tags[sid].get("tags", []) if t != label]
                    if tags[sid]["tags"]:
                        pass
                    else:
                        del tags[sid]
                else:
                    del tags[sid]
                _save_session_tags(tags)
                _audit("session_untag", id=sid, label=label or "*")
                print("已撤标记会话 %s(%s)" % (sid, ("label=" + label) if label else "全部标签"))
            else:
                print("会话 %s 无标记,无需撤" % sid)
        return

    # ── 列表 ──
    tags = _load_session_tags()
    live = _live_session_roles()          # {session_id: role} 当前在运行的
    bus_ids, bus_cwds = _bus_ids_and_cwds()
    only_tagged = getattr(args, "tagged", False)
    only_live = getattr(args, "live", False)
    only_dead = getattr(args, "dead", False)
    only_nonbus = getattr(args, "non_bus", False)
    only_bus = getattr(args, "bus", False)
    root = Path.home() / ".claude" / "projects"
    out = []
    if root.exists():
        for pdir in sorted(root.iterdir()):
            if not pdir.is_dir():
                continue
            if args.project and args.project.lower() not in pdir.name.lower():
                continue
            for f in pdir.glob("*.jsonl"):
                try:
                    st = f.stat()
                except Exception:
                    continue
                cwd = title = label = None
                try:
                    with open(f, encoding="utf-8", errors="replace") as fh:
                        for i, ln in enumerate(fh):
                            if i > 80:
                                break
                            ln = ln.strip()
                            if not ln:
                                continue
                            try:
                                r = json.loads(ln)
                            except Exception:
                                continue
                            if not cwd and r.get("cwd"):
                                cwd = r.get("cwd")
                            if not title and r.get("type") == "ai-title":
                                title = r.get("aiTitle")
                            if not label and r.get("type") == "user":
                                m = r.get("message", {})
                                c = m.get("content") if isinstance(m, dict) else None
                                if isinstance(c, str):
                                    label = c
                                elif isinstance(c, list):
                                    label = " ".join(b.get("text", "") for b in c
                                                      if isinstance(b, dict) and b.get("type") == "text")
                            if cwd and title and label:
                                break
                except Exception:
                    pass
                tg = tags.get(f.stem) or {}
                lr = live.get(f.stem)
                is_bus = (f.stem in bus_ids) or (bool(cwd) and os.path.normcase(cwd) in bus_cwds)
                out.append({
                    "id": f.stem,
                    "project": pdir.name,
                    "cwd": cwd or "",
                    "title": ((title or label or "").strip() or "(无标题)")[:70],
                    "last": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "last_epoch": st.st_mtime,
                    "size_kb": round(st.st_size / 1024, 1),
                    "tags": tg.get("tags", []),
                    "note": tg.get("note", ""),
                    "state": "运行中" if lr else "死",
                    "live_role": lr,
                    "is_bus": is_bus,
                })
    seen = {s["id"] for s in out}
    # 已标记但 jsonl 不在 projects/(被归档/prune 移走)的会话:仍列出(用标记时快照的 cwd/title)
    for sid, tg in tags.items():
        if sid in seen or not tg.get("tags"):
            continue
        if args.project and args.project.lower() not in (tg.get("cwd", "") or "").lower():
            continue
        lr = live.get(sid)
        is_bus = (sid in bus_ids) or (bool(tg.get("cwd")) and os.path.normcase(tg.get("cwd")) in bus_cwds)
        out.append({
            "id": sid, "project": "(已归档/不在projects)", "cwd": tg.get("cwd", ""),
            "title": (tg.get("title") or "(无标题)")[:70], "last": "", "last_epoch": 0,
            "size_kb": 0, "tags": tg.get("tags", []), "note": tg.get("note", ""), "archived": True,
            "state": "运行中" if lr else "死", "live_role": lr, "is_bus": is_bus,
        })
    if only_tagged:
        out = [s for s in out if s.get("tags")]
    if only_live:
        out = [s for s in out if s.get("state") == "运行中"]
    if only_dead:
        out = [s for s in out if s.get("state") == "死"]
    if only_nonbus:
        out = [s for s in out if not s.get("is_bus")]
    if only_bus:
        out = [s for s in out if s.get("is_bus")]
    out.sort(key=lambda x: (x.get("state") != "运行中", -x["last_epoch"]))   # 运行中置顶,再按最近
    n_live = sum(1 for s in out if s.get("state") == "运行中")
    n_nonbus = sum(1 for s in out if not s.get("is_bus"))
    if args.limit and args.limit > 0:
        out = out[:args.limit]
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return
    hdr = ("非bus会话" if only_nonbus else ("bus会话" if only_bus else
           ("已标记会话" if only_tagged else ("运行中会话" if only_live else
            ("死/可恢复会话" if only_dead else "Claude Code 历史会话")))))
    print("%s(~/.claude/projects/,共 %d · 运行中 %d · 死 %d · 非bus %d):" % (hdr, len(out), n_live, len(out) - n_live, n_nonbus))
    print("%-8s %-6s %-9s %-38s %-11s %s" % ("状态", "归属", "最近", "会话id", "🏷标签", "标题"))
    for s in out:
        tagstr = ("/".join(s["tags"]))[:11] if s.get("tags") else "-"
        last = s["last"][5:] if s["last"] else "(归档)"
        state = ("▶运行·" + (s.get("live_role") or "?")) if s.get("state") == "运行中" else "·死"
        owner = "bus" if s.get("is_bus") else "个人"
        print("%-8s %-6s %-9s %-38s %-11s %s" % (state[:8], owner, last, s["id"], tagstr, s["title"]))
        if s.get("note"):
            print("%48s ↳ %s" % ("", s["note"]))
    print("\n▶运行中=有活claude进程(bus在岗agent);·死=无活进程(真可恢复)。归属:bus=agent会话/个人=非bus(你自己的会话)。")
    print("筛选:--live / --dead / --tagged / --non-bus(只看个人)/ --bus   ·   标记:ab sessions --tag <id> [--label 可恢复]")


def _is_bus_member(role):
    """bus 成员判定(2026-07-10 门禁):有 presence.json(hello 过)或在 agents.json 注册。
    非成员(如与 bus 无关的编码项目会话)不得往共享待办池投 ask-user——防非 bus 决策泄漏污染
    bus 协调视图(曾有无关项目会话读全局 CLAUDE.md 后,把其项目内的问题灌进了 bus)。
    非成员应在自己项目里征询用户。"""
    if not role or role in ("unknown", "用户"):
        return False
    if (BUS / role / "presence.json").exists():
        return True
    try:
        return role in _load_agents()
    except Exception:
        return False


def cmd_ask_user(args):
    """agent 把一条【待用户确认】项投进 UI 待办池(todos.json),不滚走。capture-only,无自动回路。
    结构与 WebUI todo_add 对齐(todos.json 是 list;status 用合法值"待办"让用户照常能在 UI 标完成);
    额外带 confirm_from/ref 供将来回路。source 标"确认·<谁问的>"便于 UI 筛选。
    门禁(2026-07-10):--from 必须是 bus 注册成员,否则拒——防非 bus 项目泄漏进共享待办池。"""
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    if not _is_bus_member(frm):
        print("✗ ask-user 拒绝:'%s' 不是 bus 注册成员(无 presence、不在 agents.json)。\n"
              "  bus 待办池只接受 bus 成员的待确认项;与 bus 无关的项目/会话请用你自己项目的方式征询用户。\n"
              "  若你确是 bus 角色:先 `agentbus.py hello --role <你>` 注册后再 ask-user。" % frm)
        sys.exit(2)
    f = BUS / "todos.json"
    try:
        items = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    tid = "t-%d-%s" % (int(time.time() * 1000), os.urandom(2).hex())
    prio = args.priority if args.priority in ("高", "中", "低") else "中"
    item = {
        "id": tid,
        "title": (args.title or "").strip() or "(待确认·无标题)",
        "detail": (args.detail or "").strip(),
        "source": "确认·" + frm,
        "priority": prio,
        "status": "待办",
        "deadline": (args.deadline or "").strip(),
        "created": _now(), "updated": _now(),
        "task_id": None, "dispatched_to": None,
        "confirm_from": frm, "ref": args.ref or None,
    }
    items.append(item)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)
    _audit("ask_user", id=tid, frm=frm, title=item["title"][:40])
    print("已投入待办池(待用户确认):《%s》[%s] from %s" % (item["title"], tid, frm))


def _load_caps_pool():
    f = BUS / "capabilities.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("capabilities", {})
        except Exception:
            return {}
    return {}


def cmd_roster(args):
    """花名册:列每个 agent 的 在岗状态 / 能力(caps) / 专长(desc) / 注册时间。
    agents.json 是花名册真相源;在岗按自醒检测(armed=在岗 / stale=掉线 / off=未醒)。"""
    agents = _load_agents()
    kw = (getattr(args, "grep", None) or "").strip().lower()
    if kw:  # 路由助手:按关键词在 role/专长(desc)/caps 里筛"谁管这事"→ 直接派给它,不广播
        agents = {r: v for r, v in agents.items()
                  if not (isinstance(v, dict) and v.get("retired"))   # 退役不进路由(别把活派给退役的)
                  and (kw in r.lower() or kw in (v.get("desc", "") or "").lower()
                       or kw in " ".join(v.get("caps", [])).lower())}
    wait_roles = _wait_proc_roles()
    mark = {"armed": "✓在岗", "recent": "◐待确认", "stale": "⚠掉线", "off": "—未醒", "parked": "🅿️休眠"}
    hdr = ("花名册·匹配「%s」(%d):" % (kw, len(agents))) if kw else ("花名册(agents.json · %d 个角色):" % len(agents))
    print(hdr)
    print("-" * 78)
    if not agents:
        print("(无匹配——可能无此专责;考虑广播,或 register 新专家)"); return
    for role, v in agents.items():
        if isinstance(v, dict) and v.get("retired"):   # 退役:标注、不显示在岗状态(它本就不该在岗)
            sb = v.get("succeeded_by")
            print("● %-12s ⊘退役  retired=%s%s" % (role, v.get("retired"), ("  接替=" + sb if sb else "")))
            print("   专长(存档): %s" % ((v.get("desc", "") or "").strip()[:100]))
            continue
        st = _self_wake_state(role, wait_roles=wait_roles)[0]
        caps = "/".join(v.get("caps", [])) or "(无)"
        desc = (v.get("desc", "") or "(专长未填)").strip()
        print("● %-12s %s  reg=%s" % (role, mark.get(st, "?"), (v.get("registered", "") or "")[:10]))
        print("   能力: %s" % caps)
        print("   专长: %s" % desc[:100])
    print("-" * 78)
    print("把会话里训出的专家入册:ab register --role <名> --desc \"<专长/分管>\"(再按需 ab caps 配能力)")


def cmd_caps(args):
    """能力池查询:`caps` 看全池+谁持有;`caps --role X` 看某角色被分配的能力。"""
    pool = _load_caps_pool()
    agents = _load_agents()
    if args.role:
        caps = (agents.get(args.role) or {}).get("caps") or []
        print("【%s】的能力(caps,最小授权):" % args.role)
        if not caps:
            print("  (未分配 caps,或角色不存在)"); return
        for c in caps:
            print("  - %-16s %s" % (c, pool.get(c, {}).get("desc", "(未在能力池登记)")))
        return
    print("能力池(capabilities.json)+ 现持有者:")
    for name, info in pool.items():
        holders = [r for r, v in agents.items() if name in ((v or {}).get("caps") or [])]
        print("  %-16s %s" % (name, info.get("desc", "")))
        print("       默认给: %s" % info.get("default_for", "-"))
        print("       现持有(%d): %s" % (len(holders), ", ".join(holders) if holders else "无"))


# ========================= 工作组池化(Phase 1a · 2026-07-10) =========================
# 共享组 inbox + 成员 pull-claim:活丢组队列、谁空谁自取 → 治负载偏/串行喂料/并行率低。
# 组目录用 "_groups/" 前缀(_ 开头,_list_roles/health 已跳过,不当 role)。成员=运行时状态(join/leave·不改配置)。
# 并发 claim 命门:用 O_EXCL 独占锁——实测本机 Windows 上 os.replace 争同源【会双跑】(200-300轮出2-8胜者),
# O_EXCL 独占创建恒 1 胜(gate① 压测坐实)。故组 claim 绝不能沿用 _claim 的 os.replace 争源手法。
_GROUPS_ROOT = BUS / "_groups"


def _group_dir(g):
    return _GROUPS_ROOT / g


def _group_members_file(g):
    return _group_dir(g) / "members.json"


def _load_group_members(g):
    f = _group_members_file(g)
    if not f.exists():
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return d.get("members", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
    except Exception:
        return []


def _group_caps_baseline(g):
    """组能力基线(首成员 caps 快照;join 时核一致=只有真·可互换复制体才组池)。无则 None。"""
    f = _group_members_file(g)
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return set(d.get("caps") or []) if isinstance(d, dict) else None
    except Exception:
        return None


def _role_caps(role):
    return set((_load_agents().get(role) or {}).get("caps") or [])


def _role_groups(role):
    """role 所属的所有组(扫 _groups/*/members.json)——给 _wait_core 决定还听哪些组 inbox。"""
    out = []
    if not _GROUPS_ROOT.exists():
        return out
    for d in sorted(_GROUPS_ROOT.iterdir()):
        try:
            if d.is_dir() and role in _load_group_members(d.name):
                out.append(d.name)
        except Exception:
            continue
    return out


def _save_group(g, members, caps, target=None):
    """写组 members.json。target_size(可选软目标·WebUI 激活进度分母):显式传则设,否则保留原值
    (leave/send 等不带 target 的调用不会抹掉已设的软目标)。members 动态 join/leave·无硬 roster。"""
    f = _group_members_file(g)
    prev = {}
    if f.exists():
        try:
            prev = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    rec = {"group": g, "members": members, "caps": sorted(caps or []), "updated": _now()}
    tgt = target if target is not None else (prev.get("target_size") if isinstance(prev, dict) else None)
    if tgt is not None:
        rec["target_size"] = tgt
    _write_json(f, rec)


def cmd_join_group(args):
    role, g = args.role, args.group
    _role_dir(role)   # 确保成员 role 目录(inbox/processing/…)存在——组任务 claim 要移进其 processing
    (_group_dir(g) / "inbox").mkdir(parents=True, exist_ok=True)
    (_group_dir(g) / "shared").mkdir(parents=True, exist_ok=True)   # 共享上下文契约(1a 前置·状态外置地基)
    members = _load_group_members(g)
    my_caps = _role_caps(role)
    base = _group_caps_baseline(g)
    if members and base is not None and my_caps != base:
        print("✗ join-group 拒绝:%s 的 caps=%s 与组「%s」基线 caps=%s 不一致(不可互换≠可池)。"
              % (role, sorted(my_caps) or "(空)", g, sorted(base) or "(空)"))
        sys.exit(2)
    if role not in members:
        members.append(role)
    _save_group(g, members, base if base is not None else my_caps, target=getattr(args, "target", None))
    _audit("join_group", role=role, group=g)
    tgt = getattr(args, "target", None)
    print("✓ %s 已加入组「%s」(成员 %d%s:%s)。下次 wait 重挂即开始 pull 该组 inbox。"
          % (role, g, len(members), ("/软目标%d" % tgt) if tgt else "", ", ".join(members)))


def cmd_leave_group(args):
    role, g = args.role, args.group
    members = [m for m in _load_group_members(g) if m != role]
    _save_group(g, members, _group_caps_baseline(g))
    _audit("leave_group", role=role, group=g)
    print("✓ %s 已离开组「%s」(剩 %d 成员:%s)。" % (role, g, len(members), ", ".join(members) or "无"))


# ---- park/休眠:用户手动关 idle tab 省内存的配套(标故意暂离·压 guard 误报·与 retire 严格区分)----
def cmd_park(args):
    """标 role【故意暂离(park/休眠)】——用户手动关 tab 省内存前跑,免 selfwake-guard 每轮误报掉线。
    与 retire 严格区分:park=临时·保留花名册/caps/mailbox 原封·只压警;retire=永久归档+revive需重onboard。
    池成员 park→自动 leave 所属组(成员数准确·不路由到暂离者);unpark 或重开 hello→秒回活跃。"""
    role = args.role
    _role_dir(role)
    rec = {"parked": True, "since": _now(), "by": args.by or os.environ.get("AGENTBUS_ROLE", "user"),
           "note": args.note or ""}
    if args.days:
        rec["days"] = args.days
        rec["until"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + args.days * 86400))
    _write_json(_parked_path(role), rec)
    left = []
    for g in _role_groups(role):    # ④ 池成员 park → 自动退组(池数准确)
        _save_group(g, [m for m in _load_group_members(g) if m != role], _group_caps_baseline(g))
        left.append(g)
    _audit("park", role=role, days=args.days or 0, note=(args.note or "")[:40])
    print("🅿️ %s 已 park(休眠·故意暂离%s%s)。selfwake-guard 不再报警、health/selfwake 显🅿️休眠态。%s`ab unpark --role %s` 或重开 hello 秒回活跃。"
          % (role, ("·%d天" % args.days) if args.days else "", ("·" + args.note) if args.note else "",
             ("已自动退出组:%s。" % "、".join(left)) if left else "", role))


def cmd_unpark(args):
    """清 park/休眠标 → 回活跃态。(池成员需重 join-group 才回池。)"""
    role = args.role
    p = _parked_path(role)
    if not p.exists():
        print("(%s 未处于 park 态)" % role); return
    try:
        p.unlink()
    except Exception as e:
        print("unpark 失败:%s" % e); return
    _audit("unpark", role=role)
    print("✓ %s 已 unpark——回活跃态(如是池成员需重 `ab join-group` 才回池;重挂 wait 恢复自醒)。" % role)


_GROUP_LOCK_TTL = 60   # 正常持锁=毫秒级(建锁→移 task→删锁);>60s 必是孤儿(锁主建锁后崩溃)

def _group_lock_stale(lock):
    """组 claim 锁是否【孤儿】:持锁 pid 已死,或超 ttl(正常持锁毫秒级)。复用 edge.lock 的 ttl/死持有者手法。
    锁内容读不出(刚创建还没写/坏)→ 用 mtime 兜底(刚建=age小=非stale·不误抢;久孤=age大=判stale·可回收)。"""
    try:
        d = json.loads(lock.read_text(encoding="utf-8"))
    except Exception:
        try:
            return (time.time() - lock.stat().st_mtime) > _GROUP_LOCK_TTL
        except Exception:
            return False
    age = _age_seconds(d.get("acquired_at", ""))
    if age is not None and age > _GROUP_LOCK_TTL:
        return True
    pid = d.get("pid")
    if pid and psutil is not None:
        return not psutil.pid_exists(pid)   # 持锁进程已死 → 孤儿锁,可回收
    return False


def _claim_group_task(role, g, include_approval=False):
    """从组 g 共享 inbox 用 O_EXCL 独占锁【并发安全】领一个任务到 role 的 processing。
    谁建成 <task>.lock 谁独占该 task(实测 os.replace 争同源本机会双跑,故必用 O_EXCL);
    抢输/无任务返回 None(干净落空,惊群败者据此续等·gate②)。
    stale 回收(2026-07-10 使用方 flag B):锁主建锁后崩溃会留孤儿 .lock 令任务永久 claim 返 None;
    遇已存在锁先判 stale(持锁 pid 死/超 ttl)→ 强清重抢一次,否则真被持有→落空。"""
    gin = _group_dir(g) / "inbox"
    if not gin.exists():
        return None
    t = _oldest_claimable(gin, include_approval)
    if not t:
        return None
    lock = t.with_name(t.name + ".lock")
    fd = None
    for attempt in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if attempt == 0 and _group_lock_stale(lock):   # 孤儿锁 → 强清重抢一次
                try:
                    lock.unlink()
                except Exception:
                    pass
                continue
            return None          # 真被别的成员独占(锁新鲜)→ 干净落空
        except OSError:
            return None
    if fd is None:
        return None
    try:
        os.write(fd, json.dumps({"who": role, "pid": os.getpid(), "acquired_at": _now()},
                                ensure_ascii=False).encode("utf-8"))   # 记 pid/时间供 stale 判定
    except Exception:
        pass
    os.close(fd)
    try:
        obj = _claim(role, t)   # 此刻仅持锁者到达,os.replace 到 role/processing 无并发争用=安全
    finally:
        try:
            lock.unlink()       # task 已移走,释放锁(留了也无害:_oldest_claimable 只认 *.json)
        except Exception:
            pass
    if obj:
        obj["via_group"] = g    # 留痕:此活来自组池 pull
    return obj


def cmd_send_group(args):
    """把任务丢进【组共享 inbox】(不指定成员)→ 谁空谁 pull-claim(负载均衡+并行+异步派发)。
    组任务应自带/引用全部上下文(状态外置·任一成员可接)。"""
    g = args.group
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    members = _load_group_members(g)
    gin = _group_dir(g) / "inbox"
    gin.mkdir(parents=True, exist_ok=True)
    if not members:
        sys.stderr.write("⚠ 组「%s」当前无成员(没人 join)——任务会留在组 inbox 等成员加入 pull。\n" % g)
    tid = _gen_id(g)
    task = {"id": tid, "from": frm, "to_group": g,
            "title": args.title or "", "task": args.task,
            "created": _now(), "status": "pending"}
    if getattr(args, "affinity", None):
        task["affinity"] = args.affinity   # 会话粘连提示(优先某成员;掉线/超时由 reap 回落组池)
    if args.requires_approval:
        task["requires_approval"] = True
    _write_json(gin / (tid + ".json"), task)
    _audit("send_group", id=tid, frm=frm, group=g, title=args.title or "")
    print(tid)


# ---- 2a:弹性池工人 —— 内存感知并发上限(使用方 2026-07-10 公式)+ 派发器 spin 前闸 ----
def _mem_cap(per_worker_gb=0.5, reserve_gb=2.0):
    """实时内存感知并发上限(每次 spin 前查 live 内存·非静态)。返回 dict 供派发器闸 + WebUI 看板。
    budget = avail − reserve;cap = budget // per_worker;
    硬闸:percent≥92 或 avail<reserve → cap=0(已在 swap 颠簸别再 spin,分片留组 inbox 等 pull 背压·宁排队别拖垮全机)。
    per_worker 建议派发器记实测 claude -p worker p90 RSS 动态回填(冷启默认 0.5G)。"""
    GB = 1024 ** 3
    if psutil is None:
        return {"avail_gb": None, "percent": None, "budget_gb": None, "mem_cap": 0,
                "hard_gate": True, "note": "无 psutil → cap=0 保守", "per_worker_gb": per_worker_gb, "reserve_gb": reserve_gb}
    vm = psutil.virtual_memory()
    avail = vm.available; reserve = reserve_gb * GB; per = max(per_worker_gb, 0.05) * GB
    budget = max(0, avail - reserve)
    cap = int(budget // per)
    hard = (vm.percent >= 92) or (avail < reserve)
    if hard:
        cap = 0
    return {"avail_gb": round(avail / GB, 2), "percent": vm.percent, "budget_gb": round(budget / GB, 2),
            "mem_cap": cap, "per_worker_gb": per_worker_gb, "reserve_gb": reserve_gb, "hard_gate": hard}


def cmd_mem_cap(args):
    print(json.dumps(_mem_cap(args.per_worker_gb, args.reserve_gb), ensure_ascii=False, indent=1))


# ---- 1b:fan-out(一逻辑任务拆 N 分片撒全组并行)+ 流式 collect(边到边产出,barrier 降为例外)----
def _fanout_dir(g):
    return _group_dir(g) / "fanout"


def _find_fanout_manifest(fid, group=None):
    cands = [group] if group else ([d.name for d in _GROUPS_ROOT.iterdir() if d.is_dir()] if _GROUPS_ROOT.exists() else [])
    for g in cands:
        mf = _fanout_dir(g) / (fid + ".json")
        if mf.exists():
            try:
                return json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def cmd_fanout(args):
    """把一逻辑任务拆 N 独立分片撒进组共享 inbox(并行 pull)。--shards:JSON 数组 / 换行分隔 / -(stdin)。
    每分片自带上下文(状态外置)。记 manifest 供 collect 收敛。打印 fanout_id。"""
    g = args.group
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    raw = args.shards
    if raw == "-":
        raw = sys.stdin.buffer.read().decode("utf-8")
    rs = raw.strip()
    if rs.startswith(("[", "{")):
        shards = json.loads(rs)
        if isinstance(shards, dict):
            shards = [shards]
    else:
        shards = [ln for ln in raw.splitlines() if ln.strip()]
    if not shards:
        sys.stderr.write("⚠ fanout:无分片(--shards 空)\n"); sys.exit(2)
    gin = _group_dir(g) / "inbox"; gin.mkdir(parents=True, exist_ok=True)
    fid = _gen_id("fanout")
    tids = []
    for i, sh in enumerate(shards):
        tid = "%s-s%d" % (fid, i)
        body = sh if isinstance(sh, str) else json.dumps(sh, ensure_ascii=False)
        _write_json(gin / (tid + ".json"),
                    {"id": tid, "from": frm, "to_group": g, "fanout_id": fid, "shard_index": i,
                     "title": (args.title or "") + (" [shard %d/%d]" % (i + 1, len(shards))),
                     "task": body, "created": _now(), "status": "pending"})
        tids.append(tid)
    _fanout_dir(g).mkdir(parents=True, exist_ok=True)
    _write_json(_fanout_dir(g) / (fid + ".json"),
                {"fanout_id": fid, "group": g, "from": frm, "n": len(tids), "shard_tids": tids, "created": _now()})
    _audit("fanout", id=fid, group=g, n=len(tids), frm=frm)
    print(fid)


def cmd_fanout_collect(args):
    """收敛 fanout 结果。默认【流式】:每个分片 done 回流到 <from>/done/ 就产出一条(边到边·不等齐)——
    治 Master 阻塞在 barrier join 的病根(用户 2026-07-10)。--wait=barrier(攒齐 N 个再返),降为例外。"""
    fid = args.id
    man = _find_fanout_manifest(fid, args.group)
    if not man:
        print(json.dumps({"error": "fanout manifest 未找到: %s" % fid}, ensure_ascii=False)); sys.exit(2)
    frm = man["from"]; tids = man["shard_tids"]; n = man["n"]
    done_dir = _role_dir(frm) / "done"
    stream = not args.wait                      # 默认流式;--wait 才 barrier
    got = {}

    def _collect_ready():
        new = []
        for tid in tids:
            if tid in got:
                continue
            f = done_dir / (tid + ".json")
            if f.exists():
                try:
                    obj = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    obj = {"error": "bad result", "id": tid}
                got[tid] = obj
                try:
                    f.unlink()                  # 消费,不污染 replies
                except Exception:
                    pass
                new.append(obj)
        return new

    def _emit(objs):
        for o in objs:
            print(json.dumps(o, ensure_ascii=False), flush=True)   # 边到边:一分片一行

    ready = _collect_ready()
    if stream:
        _emit(ready)
    if len(got) < n:
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

        done_dir.mkdir(parents=True, exist_ok=True)
        obs = Observer(); obs.schedule(H(), str(done_dir), recursive=False)
        obs_ok = True
        try:
            obs.start()
        except Exception:
            obs_ok = False
        try:
            deadline = time.monotonic() + args.timeout
            while len(got) < n:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if obs_ok:
                    ev.wait(timeout=remaining); ev.clear()
                else:
                    time.sleep(min(remaining, 1.0))
                if stream:
                    _emit(_collect_ready())
                else:
                    _collect_ready()
        finally:
            if obs_ok:
                try:
                    obs.stop(); obs.join()
                except Exception:
                    pass
    if not stream:
        print(json.dumps(list(got.values()), ensure_ascii=False))
    sys.stderr.write("[fanout-collect] %s:收到 %d/%d%s\n" % (fid, len(got), n, "" if len(got) >= n else "(超时,余未回)"))


def main():
    ap = argparse.ArgumentParser(prog="agentbus")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register"); p.add_argument("--role", required=True); p.add_argument("--desc", default=""); p.set_defaults(fn=cmd_register)
    p = sub.add_parser("send"); p.add_argument("--to", required=True); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--requires-approval", action="store_true", dest="requires_approval"); p.set_defaults(fn=cmd_send)
    # 工作组池化(Phase 1a):join/leave 组(运行时·不改配置)、send-group 丢共享组 inbox(谁空谁 pull)
    p = sub.add_parser("join-group"); p.add_argument("--role", required=True); p.add_argument("--group", required=True); p.add_argument("--target", type=int, default=None, help="可选软目标成员数(WebUI 激活进度分母,如 示例组=3)"); p.set_defaults(fn=cmd_join_group)
    p = sub.add_parser("leave-group"); p.add_argument("--role", required=True); p.add_argument("--group", required=True); p.set_defaults(fn=cmd_leave_group)
    p = sub.add_parser("park"); p.add_argument("--role", required=True); p.add_argument("--days", type=int, default=None); p.add_argument("--note", default=""); p.add_argument("--by", default=None); p.set_defaults(fn=cmd_park)
    p = sub.add_parser("unpark"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_unpark)
    p = sub.add_parser("send-group"); p.add_argument("--group", required=True); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--affinity", default=None, help="会话粘连:优先某成员(掉线/超时由 reap 回落组池)"); p.add_argument("--requires-approval", action="store_true", dest="requires_approval"); p.set_defaults(fn=cmd_send_group)
    p = sub.add_parser("fanout"); p.add_argument("--group", required=True); p.add_argument("--shards", required=True, help="JSON数组 / 换行分隔 / -(stdin)"); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.set_defaults(fn=cmd_fanout)
    p = sub.add_parser("fanout-collect"); p.add_argument("--id", required=True); p.add_argument("--group", default=None); p.add_argument("--wait", action="store_true", help="barrier:攒齐N个再返(默认流式边到边·barrier是例外)"); p.add_argument("--timeout", type=float, default=300.0); p.set_defaults(fn=cmd_fanout_collect)
    p = sub.add_parser("mem-cap"); p.add_argument("--per-worker-gb", type=float, default=0.5, dest="per_worker_gb"); p.add_argument("--reserve-gb", type=float, default=2.0, dest="reserve_gb"); p.set_defaults(fn=cmd_mem_cap)
    p = sub.add_parser("wait"); p.add_argument("--role", required=True); p.add_argument("--timeout", type=float, default=110.0); p.add_argument("--include-approval", action="store_true", dest="include_approval"); p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("serve"); p.add_argument("--timeout", type=float, default=590.0); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("request"); p.add_argument("--to", required=True); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--chain", default=""); p.add_argument("--timeout", type=float, default=300.0); p.set_defaults(fn=cmd_request)
    p = sub.add_parser("done"); p.add_argument("--id", required=True); p.add_argument("--role", default=None); p.add_argument("--result", required=True); p.add_argument("--status", default="ok", choices=["ok", "error"]); p.add_argument("--notify", default="auto", choices=["auto", "always", "never"]); p.add_argument("--to", default=None, help="显式回执目标(任务已被归档/reap、找不到原 from 时用)"); p.set_defaults(fn=cmd_done)
    p = sub.add_parser("peek"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_peek)
    p = sub.add_parser("replies"); p.add_argument("--role", required=True); p.add_argument("--clear", action="store_true"); p.set_defaults(fn=cmd_replies)
    p = sub.add_parser("hello"); p.add_argument("--role", default=None); p.add_argument("--preview", action="store_true", help="只读预览 onboarding 文本(WebUI 提示词审核页用),不带 --role 也能跑、无副作用"); p.set_defaults(fn=cmd_hello)
    p = sub.add_parser("who"); p.add_argument("--max-age", type=int, default=900); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_who)
    p = sub.add_parser("tag"); p.add_argument("--pid", type=int, required=True); p.add_argument("--label", required=True); p.add_argument("--note", default=""); p.add_argument("--public", action="store_true"); p.set_defaults(fn=cmd_tag)
    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("reap"); p.add_argument("--role", default=None); p.add_argument("--ttl", type=float, default=4000.0); p.add_argument("--grace", type=float, default=120.0); p.add_argument("--max-requeue", type=int, default=3, dest="max_requeue"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_reap)
    p = sub.add_parser("supervise"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_supervise)
    p = sub.add_parser("webui-guard"); p.add_argument("--port", type=int, default=8799); p.add_argument("--min-age", type=float, default=30.0, dest="min_age"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_webui_guard)
    p = sub.add_parser("health-sweep"); p.add_argument("--apply", action="store_true"); p.add_argument("--stuck-min", type=int, default=90, dest="stuck_min"); p.set_defaults(fn=cmd_health_sweep)
    p = sub.add_parser("health"); p.add_argument("--stale-min", type=int, default=30, dest="stale_min"); p.add_argument("--stuck-min", type=int, default=90, dest="stuck_min"); p.add_argument("--todo-stale-min", type=int, default=1440, dest="todo_stale_min"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_health)
    p = sub.add_parser("stale"); p.add_argument("--hours", type=int, default=24); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_stale)
    p = sub.add_parser("tasklines"); p.add_argument("--id", default=None); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_tasklines)
    p = sub.add_parser("prune"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_prune)
    p = sub.add_parser("selfwake"); p.set_defaults(fn=cmd_selfwake)
    p = sub.add_parser("selfwake-guard"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_selfwake_guard)
    p = sub.add_parser("whoami"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_whoami)
    p = sub.add_parser("caps"); p.add_argument("--role", default=None); p.set_defaults(fn=cmd_caps)
    p = sub.add_parser("roster"); p.add_argument("--grep", default=None); p.set_defaults(fn=cmd_roster)
    p = sub.add_parser("ask-user"); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", required=True); p.add_argument("--detail", default=""); p.add_argument("--priority", default="中"); p.add_argument("--deadline", default=""); p.add_argument("--ref", default=None); p.set_defaults(fn=cmd_ask_user)
    p = sub.add_parser("answer"); p.add_argument("--todo", required=True); p.add_argument("--reply", required=True); p.add_argument("--from", dest="frm", default=None); p.set_defaults(fn=cmd_answer)
    p = sub.add_parser("route"); p.add_argument("keywords", nargs="+"); p.set_defaults(fn=cmd_route)
    p = sub.add_parser("retire"); p.add_argument("--role", required=True); p.add_argument("--reason", default=""); p.add_argument("--succeeded-by", dest="succeeded_by", default=None); p.add_argument("--session-ids", dest="session_ids", default=""); p.add_argument("--memory", default=""); p.set_defaults(fn=cmd_retire)
    p = sub.add_parser("revive"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_revive)
    p = sub.add_parser("retired"); p.set_defaults(fn=cmd_retired)
    p = sub.add_parser("sessions"); p.add_argument("--project", default=None); p.add_argument("--limit", type=int, default=0); p.add_argument("--json", action="store_true"); p.add_argument("--tag", default=None, help="给会话id打标记"); p.add_argument("--untag", default=None, help="撤会话id标记"); p.add_argument("--label", default=None, help="标签(默认'可恢复')"); p.add_argument("--note", default=None, help="标记备注"); p.add_argument("--tagged", action="store_true", help="只列已标记的会话"); p.add_argument("--live", action="store_true", help="只列运行中(有活进程)"); p.add_argument("--dead", action="store_true", help="只列死的(可恢复/休眠)"); p.add_argument("--non-bus", action="store_true", dest="non_bus", help="只列非bus(个人)会话"); p.add_argument("--bus", action="store_true", help="只列bus(agent)会话"); p.set_defaults(fn=cmd_sessions)
    p = sub.add_parser("edge-acquire"); p.add_argument("--who", default=None); p.add_argument("--timeout", type=float, default=120.0); p.add_argument("--ttl", type=float, default=300.0); p.set_defaults(fn=cmd_edge_acquire)
    p = sub.add_parser("edge-release"); p.add_argument("--who", default=None); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_edge_release)
    p = sub.add_parser("dispatch"); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--name", default="adhoc"); p.add_argument("--cwd", default=None); p.add_argument("--timeout", type=float, default=3600.0); p.set_defaults(fn=cmd_dispatch)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
