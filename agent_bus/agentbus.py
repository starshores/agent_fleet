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
except Exception:
    pass

BUS = Path(os.path.expandvars(os.path.expanduser(os.environ.get("AGENTBUS_DIR", str(Path.home() / ".claude" / "agent_bus")))))

_CONFIG_CACHE = None


def cfg(key, default=None):
    """读机器相关路径配置(集中在 agent_bus/config.json,迁移/换机改这一处即可)。
    优先级:环境变量 AGENTBUS_<KEY大写> > config.json > default。
    被 worker_daemon / ui.server / recover / hook 复用,避免硬编码绝对路径散落各处。"""
    env = os.environ.get("AGENTBUS_" + key.upper())
    if env:
        return os.path.expandvars(os.path.expanduser(env))
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        try:
            _CONFIG_CACHE = json.loads((BUS / "config.json").read_text(encoding="utf-8"))
        except Exception:
            _CONFIG_CACHE = {}
    value = _CONFIG_CACHE.get(key, default)
    return os.path.expandvars(os.path.expanduser(value)) if isinstance(value, str) else value


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _role_dir(role):
    d = BUS / role
    for sub in ("inbox", "processing", "archive", "done"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def _agents_file():
    return BUS / "agents.json"


def _list_roles():
    """BUS 下的 role 目录(排除 __pycache__ / 以 . 或 _ 开头的非 role 目录)。"""
    if not BUS.exists():
        return []
    return sorted([p.name for p in BUS.iterdir()
                   if p.is_dir() and not p.name.startswith((".", "_"))])


def _retired_roles():
    """已退役 role 集合(agents.json 里 retired 字段为真)。退役语义:不再派活/onboarding、
    不当"掉线/缺失"误报、supervise 不拉起、留痕不删(贡献档案在记忆)。succeeded_by 记接替者。"""
    try:
        return {r for r, v in _load_agents().items() if isinstance(v, dict) and v.get("retired")}
    except Exception:
        return set()


def _is_test_role(name):
    """测试/临时角色:zz* / __* / 单字符 ?。统计/健康/校验默认过滤,免测试 send 污染真实指标
    (Master数分 实证:未claim 17 条里 6 条是 zztest_loss/__todotest 等测试,制造假"漏接")。"""
    return bool(name) and (name.startswith(("zz", "__")) or name == "?")


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
        if _self_wake_state(role)[0] != "armed":
            sys.stderr.write("⚠ 目标 %s 自醒非在岗——派了可能没人接(claim);"
                             "换个在岗的 / 让它重挂自醒,或确认它是人值守 tab。(仍写入其 inbox)\n" % role)


def _load_agents():
    f = _agents_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_agents(a):
    BUS.mkdir(parents=True, exist_ok=True)
    _agents_file().write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")


def _gen_id(role):
    return "%d-%s-%s" % (time.time_ns() // 1_000_000, role, os.urandom(3).hex())


def _write_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    # 原子落地,避免 watcher 读到半截。os.replace 在 Windows 下若目标被 watcher/AV/另一 waiter
    # 瞬时占用会抛 PermissionError(WinError 5)——短 backoff 重试,绝不让瞬时锁把调用方崩掉
    # (尤其自醒 wait 进程:Master 报过 _claim 改写时撞 WinError 5 → wait exit 1 → 协调位掉线漏消息)。
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == 4:
                try: tmp.unlink()           # 持久失败:清掉孤儿 .tmp 再抛
                except Exception: pass
                raise
            time.sleep(0.05 * (attempt + 1))


def _audit(event, **fields):
    """append-only 审计日志:send/claim/done 各记一行到 BUS/events.jsonl。
    全链路可追溯(replies 被 clear 后仍留痕);best-effort,绝不影响主流程。"""
    try:
        rec = {"ts": _now(), "event": event}
        rec.update(fields)
        with open(BUS / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------- BI 资源消耗指标(token / 成本 / 耗时)----------------
# 每个任务跑完(daemon / dispatch)往 BUS/metrics.jsonl 追一行,WebUI BI 模块按角色/全队聚合。
METRICS = BUS / "metrics.jsonl"


def record_metric(role, task_id, title, status, meta, kind="daemon"):
    """任务完成后记一条资源消耗。meta 来自 claude_run.run_streaming 的第三个返回值。
    best-effort,绝不影响主流程(埋点失败不能让任务回填失败)。"""
    try:
        meta = meta or {}
        rec = {
            "t": time.time(),                       # epoch 秒(给 BI 做时间窗聚合)
            "ts": _now(),                           # 可读本地时间
            "role": role,
            "task_id": task_id,
            "title": (title or "")[:80],            # 标题截断;私有 tab 不走 daemon 任务,不会入账
            "status": status,
            "kind": kind,                           # daemon / dispatch
            "tokens_in": int(meta.get("tokens_in", 0) or 0),
            "tokens_out": int(meta.get("tokens_out", 0) or 0),
            "cache_read": int(meta.get("cache_read", 0) or 0),
            "cache_creation": int(meta.get("cache_creation", 0) or 0),
            "cost_usd": float(meta.get("cost_usd", 0) or 0),
            "duration_ms": int(meta.get("duration_ms", 0) or 0),
            "num_turns": int(meta.get("num_turns", 0) or 0),
        }
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        # O_APPEND 原子追加单行,避免并发 daemon 收尾时互相截断
        fd = os.open(str(METRICS), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def read_metrics(limit=8000):
    """读 metrics.jsonl(尾部 limit 行),返回 dict 列表。文件不在/坏行跳过。"""
    if not METRICS.exists():
        return []
    try:
        lines = METRICS.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-limit:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _oldest(folder):
    files = sorted([p for p in folder.glob("*.json")])
    return files[0] if files else None


# ---------------- identity / presence ----------------

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


def _touch_presence(role, mode, extra=None):
    """写/更新 <role>/presence.json:声明身份 + 心跳(last_seen)。"""
    pf = BUS / role / "presence.json"
    prev = {}
    if pf.exists():
        try:
            prev = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    claude_pid, cwd, chain = _claude_ancestor()
    pres = {
        "role": role,
        "claude_pid": claude_pid,
        "shell_pid": os.getpid(),
        "cwd": cwd,
        "host": socket.gethostname(),
        "started": prev.get("started") or _now(),
        "last_seen": _now(),
        "mode": mode,
        # 权威 session→role 绑定:交互 tab 的 hello/心跳子进程继承 CLAUDE_CODE_SESSION_ID
        # (= 会话 jsonl id),据此 WebUI 看板精确对齐、不再 regex 猜 role。daemon 无此 env→None。
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID") or prev.get("session_id"),
    }
    if extra:
        pres.update(extra)
    _write_json(pf, pres)


def _set_current_task(role, tid, title):
    """把'当前在做的任务'merge 进 presence(A3:让 who/WebUI 显示 worker/tab 在忙什么,
    而非看着像空闲)。claim 时设、done 时清(tid=None)。merge 写,保留 presence 其它字段。
    注:_touch_presence 心跳不保留该字段——但领取与完成之间不发心跳,故 current_task 在执行期间稳定可见。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return
    try:
        pr = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return
    if tid:
        pr["current_task"] = tid
        pr["current_title"] = (title or "")[:80]
    else:
        pr.pop("current_task", None)
        pr.pop("current_title", None)
    try:
        _write_json(pf, pr)
    except Exception:
        pass


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


def _pidtags_file():
    return BUS / "pid_tags.json"


def _load_pidtags():
    f = _pidtags_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _age_seconds(ts):
    try:
        t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
        return max(0, int(time.time() - t))
    except Exception:
        return None


def _owner_live(role):
    """该 role 当前监听者的【进程】是否还在(给 reaper 判孤儿)。
    **只看进程在不在,不看心跳新鲜度**——这是踩过的坑:daemon 跑 claude -p 期间
    阻塞在 run_task、不发心跳,心跳会变陈但进程活着、任务正在做;若按"心跳陈了=死"去
    reap,会把【正在执行的任务】误判成孤儿退回 inbox → 重复执行(实测差点触发第二次外部写)。
    所以:进程真没了(pid 不存在)才算 owner 死;活着但卡住交给 reaper 的 ttl 硬上限去兜。
    listening:claude_pid 在跑;daemon:daemon_pid 在跑;idle/hello-only:没在听 → False
    (idle tab 不会推进 processing,其滞留任务该退回 inbox 等真 worker)。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return False
    try:
        pr = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return False
    mode = pr.get("mode")
    if psutil is None:
        # 没 psutil 只能退而求其次用心跳新鲜度(尽力,不如进程判准)
        age = _age_seconds(pr.get("last_seen", ""))
        wt = pr.get("wait_timeout") or 110
        return mode in ("listening", "daemon") and age is not None and age <= wt + 60
    if mode == "daemon":
        dpid = pr.get("daemon_pid")
        return bool(dpid and psutil.pid_exists(dpid))
    if mode == "listening":
        pid = pr.get("claude_pid")
        return bool(pid and pid in _alive_claude_pids())
    return False  # idle / 其它:没在听的循环


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
    t = _oldest_claimable(inbox, include_approval)
    if t:
        return _claim(role, t)

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
    obs.start()
    try:
        t = _oldest_claimable(inbox, include_approval)  # 启动 observer 后再查一次,消除竞态
        if not t:
            # 循环等到截止时间:被【无关/竞态】文件事件唤醒但无可领取任务时,清标志继续等
            # 剩余时间,而非直接返回 idle。否则一次 spurious 事件就让 wait 提前空返 →
            # 自醒会话被白白唤起(daemon 只多空转一圈,但协调位自醒每次唤起都费 token)。
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break                       # 真超时,无任务 → idle
                woke = ev.wait(timeout=remaining)
                t = _oldest_claimable(inbox, include_approval)
                if t:
                    break                       # 有可领取任务
                if not woke:
                    break                       # ev.wait 超时返回(非事件)→ idle
                ev.clear()                      # 被事件唤醒但没抢到(竞态/无关)→ 继续等剩余
    finally:
        obs.stop(); obs.join()

    return _claim(role, t) if t else None


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


def _serve_wait(role, timeout, presence_mode="listening", presence_extra=None, include_approval=False):
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
HUMAN_ATTENDED = ("Master", "MasterAgent")  # 人值守角色:它们的回填才值得弹给人


def _toast_throttled(min_gap=45):
    """节流:距上次 toast 不足 min_gap 秒就跳过(防 ok 完成刷屏)。状态存 BUS/.last_toast。"""
    f = BUS / ".last_toast"
    now = time.time()
    try:
        last = float(f.read_text(encoding="utf-8").strip())
    except Exception:
        last = 0.0
    if now - last < min_gap:
        return False
    try:
        f.write_text(str(now), encoding="utf-8")
    except Exception:
        pass
    return True


def _fire_toast(head, body):
    """真正弹一条 WinRT toast。绝不抛错、绝不阻塞(Popen 即走,不 wait)。"""
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("'", "’").replace('"', "”"))
    xml = ('<toast><visual><binding template="ToastGeneric">'
           "<text>" + esc(head) + "</text><text>" + esc(body) + "</text>"
           "</binding></visual></toast>")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
          "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
          "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
          "$x.LoadXml('" + xml + "');"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('ClaudeCode.NotifyTest')"
          ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def _maybe_toast(sender, status, title, result, notify, reply_mode=None):
    """完成通知分级(默认 auto):
      never → 不发;always → 发;
      auto  → error 必发(不节流);ok 仅当 sender 是人值守角色且过节流;
      reply_mode=sync(request 同步应答)→ 一律不发(调用方已同步拿到)。"""
    if notify == "never" or reply_mode == "sync":
        return
    if notify == "auto":
        if status == "ok":
            if sender not in HUMAN_ATTENDED:
                return
            if not _toast_throttled():
                return
        # status=error → 必发,不节流
    head = ("✅ 任务完成" if status == "ok" else "❌ 任务失败") + (" · " + title if title else "")
    body = (sender + " ← " + (result or ""))[:140]
    _fire_toast(head, body)


def _emit_completion_notice(sender, notifier_role, ref_id, status, title, result_loc=None):
    """若 sender 是【自醒在岗的协调位】,往其 inbox 投一条 kind:notice 轻量完成通知,触发其后台 wait 自醒。
    cmd_done 与 dispatch_agent 收尾共用(dispatch 直接写 done 不走 cmd_done,故此前不触发自醒——本函数补齐)。
    防环铁律:notice 自身完成不要调本函数(调用方负责对 kind=notice 不调)。
    投递唯一判据=【sender 自醒在岗(armed)】——任何挂了后台 wait 的角色都投,不限 Master/MasterAgent。
    (原 HUMAN_ATTENDED 白名单闸已移除:它会让 Master数分 等新 hello 的自醒协调位静默失联;Master数分 报的 bug。)
    best-effort,绝不影响主流程。返回是否投了 notice。"""
    try:
        if _self_wake_state(sender)[0] != "armed":
            return False
        loc = result_loc or ("%s/done/%s.json" % (sender, ref_id))
        nid = "notice-" + ref_id
        notice = {
            "id": nid, "from": notifier_role, "to": sender, "kind": "notice",
            "title": "✅ 完成:" + (title or ref_id),
            "task": ("[完成通知·读完即弃、勿当任务执行] %s 完成了你派的《%s》(status=%s)。"
                     "完整结果在 %s;读完直接 done 本通知即可。"
                     % (notifier_role, title or ref_id, status, loc)),
            "notice_of": ref_id, "notice_status": status,
            "created": _now(), "status": "pending",
        }
        _write_json(BUS / sender / "inbox" / (nid + ".json"), notice)
        _audit("notify_wake", id=ref_id, to=sender)
        return True
    except Exception:
        return False


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
    role = args.role
    _role_dir(role)
    a = _load_agents()
    a.setdefault(role, {})["registered"] = a.get(role, {}).get("registered") or _now()
    _save_agents(a)
    _touch_presence(role, "idle")
    pf = BUS / role / "presence.json"
    print(pf.read_text(encoding="utf-8"))
    abpy = os.path.abspath(__file__)
    py = sys.executable or "python"   # 绝对解释器路径,PATH-proof:后台 shell 偶发 PATH 缺 python 会让裸 `python` 启动 127→自醒掉线(Master数分/我都撞过)
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
        if r in retired_set:
            rec["verdict"] = "⊘ 退役"; out_roles.append(rec); continue
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
        if r in retired_set:        # 退役:标注、不当残留/异常(进程退是应该的)
            print("%-14s %-22s" % (r, "⊘ 退役")); continue
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
        pd = d / "processing"
        if not pd.exists():
            continue
        for p in pd.glob("*.json"):
            age = (now - p.stat().st_mtime) / 60
            if age < stuck_min:
                continue
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            stuck.append({"role": d.name, "id": p.stem, "age_min": int(age),
                          "from": o.get("from"), "title": o.get("title") or ""})
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
    rep = {"orphans_rerouted": [], "orphans_to_todo": [], "stuck_to_todo": [], "skipped_dup": 0, "resolved_stale": 0}

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
            if apply:
                _add_todo("孤儿回执需认领:from %s" % o["from"],
                          "回执丢进 unknown/、原请求方不可考。标题「%s」。要不要指认 owner/重投?" % o["title"][:80],
                          "health:orphan:" + o["id"])
            rep["orphans_to_todo"].append({"id": o["id"], "from": o["from"]})

    for s in a["stuck"]:                     # 卡死任务:需人定(重派/催/判死),不自动杀
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


def _collect_health(stale_min=30, stuck_min=90):
    """只读汇总 bus 健康各项指标(不改任何状态)。返回 dict,供 cmd_health 渲染 / WebUI 复用。"""
    now = time.time()
    role_dirs = [d for d in sorted(BUS.iterdir())
                 if d.is_dir() and not d.name.startswith((".", "_")) and d.name != "unknown"
                 and not _is_test_role(d.name)]
    h = {"as_of": _now(), "warns": []}
    # 1) 自醒(退役 role 跳过——不计掉线,否则误报)
    wr = _wait_proc_roles()
    retired_set = _retired_roles()
    armed = stale = off = retired_n = 0; stale_roles = []
    for d in role_dirs:
        if d.name in retired_set:
            retired_n += 1; continue
        if not (d / "presence.json").exists():
            continue
        stt, _ = _self_wake_state(d.name, wait_roles=wr)
        if stt == "armed": armed += 1
        elif stt == "stale": stale += 1; stale_roles.append(d.name)
        else: off += 1
    h["selfwake"] = {"armed": armed, "stale": stale, "off": off, "retired": retired_n, "stale_roles": stale_roles}
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
    h["healthy"] = not h["warns"]
    return h


def cmd_health(args):
    """【只读】bus 健康总检:自醒 / 队列 / 消息丢失(孤儿·卡死·久滞)/ daemon 舰队 / WebUI / edge锁,
    一屏看全 + ⚠/✓ + 总判定。不改任何状态(要处置走 supervise / health-sweep --apply)。--json 出结构化。"""
    h = _collect_health(stale_min=getattr(args, "stale_min", 30) or 30,
                        stuck_min=getattr(args, "stuck_min", 90) or 90)
    if getattr(args, "json", False):
        print(json.dumps(h, ensure_ascii=False, indent=1)); return
    L = ["=== Bus 健康检查 (as of %s) ===" % h["as_of"]]
    sw = h["selfwake"]
    L.append("\n[自醒]   在岗 %d / 掉线 %d / 未醒 %d / 退役 %d" % (sw["armed"], sw["stale"], sw["off"], sw.get("retired", 0)))
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
    L.append("\n" + "-" * 52)
    L.append("总判定: ✓ 全绿,bus 健康" if h["healthy"] else "总判定: ⚠ %d 项需注意 — %s" % (len(h["warns"]), "; ".join(h["warns"])))
    print("\n".join(L))


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
    mark = {"armed": "✓在岗", "stale": "⚠掉线", "off": "—未醒"}
    print("对口路由推荐(命中「%s」):" % " ".join(terms))
    for i, (sc, _o, role, st, desc) in enumerate(scored[:5]):
        tag = " ← 推荐(对口+在岗)" if i == 0 and st == "armed" else (" ← 最对口但掉线,先让它重挂" if i == 0 else "")
        print("  %-12s %s 命中%d  %s%s" % (role, mark.get(st, "?"), sc, desc, tag))
    print("→ 对口直派:ab send --to <角色> --from <你> ...(别自接跨领域、别乱广播)")


def _wait_proc_roles():
    """挂着 `agentbus wait --role X` 进程的 role 集合(协调位事件自醒判活;复用 _proc_scan 单次扫描+缓存)。
    只认真正的 python 进程(排除 bash -c 壳——壳 cmdline 也含 agentbus wait 串且退出后可能残留)。"""
    return _proc_scan()[1]


def _self_wake_state(role, alive_pids=None, wait_roles=None):
    """判一个 role 的事件自醒在不在岗。返回 (state, detail)。
    armed=在岗;stale=掉线(曾自醒但进程退/没重挂);off=未自醒(idle/无 presence)。
    daemon 按 daemon_pid 判活;协调位/会话按【有无活的 wait 进程】判活(wait 不刷心跳,不能靠心跳)。
    wait_roles:预扫好的"有 wait 进程的 role 集合"(循环调用务必传入,免每 role 全扫);None 则现扫一次。"""
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
    if wait_roles is None:
        wait_roles = _wait_proc_roles()
    if role in wait_roles:
        return ("armed", "会话自醒(后台 wait 进程在跑)")
    if mode == "listening":
        pid = pr.get("claude_pid")
        alive = bool(psutil is not None and pid and psutil.pid_exists(pid))
        if alive and fresh:
            return ("armed", "会话自醒(tab /loop serve,心跳 %ss)" % age)
        if alive:
            return ("stale", "会话在但无 wait 进程、心跳超期 %ss——自醒掉线(没重挂?)" % age)
        return ("stale", "进程已退,自醒掉线")
    return ("off", "未自醒(mode=%s)" % mode)


def cmd_selfwake(args):
    """自醒检测:列出每个 role 的事件自醒是否在岗(daemon watchdog / 会话后台 wait)。"""
    armed = stale = off = retired = 0
    wait_roles = _wait_proc_roles()   # 一次性预扫,循环内复用
    retired_set = _retired_roles()
    print("%-14s %-8s %s" % ("role", "自醒", "说明"))
    print("-" * 78)
    for role in _list_roles():
        if role in retired_set:        # 退役:不计掉线(否则误报),只标注
            sb = _load_agents().get(role, {}).get("succeeded_by")
            print("%-14s %-8s %s" % (role, "⊘退役", "已退役" + ("·接替=" + sb if sb else "") + "(留痕不删,不派活)"))
            retired += 1; continue
        st, detail = _self_wake_state(role, wait_roles=wait_roles)
        mark = {"armed": "✓在岗", "stale": "⚠掉线", "off": "—未醒"}[st]
        print("%-14s %-8s %s" % (role, mark, detail))
        armed += st == "armed"; stale += st == "stale"; off += st == "off"
    print("-" * 78)
    print("自醒在岗 %d / 掉线 %d / 未醒 %d / 退役 %d" % (armed, stale, off, retired))
    if stale:
        print("⚠ 掉线需重挂:daemon→`supervise`/`start_worker`;协调位会话→重新挂后台 `wait --role <自己>`。")


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


def cmd_sessions(args):
    """列【可恢复的 Claude Code 会话】(~/.claude/projects/<cwd编码>/<session-id>.jsonl)。
    给 UI 的"可恢复会话"视图提供数据;只读每个会话头部 ~80 行取元数据(cwd/aiTitle/首条/时间),不读全文。
    注:UI 无法替用户跑交互式 resume,只能展示 + 给 `claude --resume <id>` 命令;真正 resume 在终端。"""
    import datetime
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
                out.append({
                    "id": f.stem,
                    "project": pdir.name,
                    "cwd": cwd or "",
                    "title": ((title or label or "").strip() or "(无标题)")[:70],
                    "last": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "last_epoch": st.st_mtime,
                    "size_kb": round(st.st_size / 1024, 1),
                })
    out.sort(key=lambda x: -x["last_epoch"])
    if args.limit and args.limit > 0:
        out = out[:args.limit]
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return
    print("可恢复会话(~/.claude/projects/,按最近活动倒序,共 %d):" % len(out))
    print("%-11s %-30s %-38s %s" % ("最近", "项目(cwd编码)", "会话id", "标题"))
    for s in out:
        print("%-11s %-30s %-38s %s" % (s["last"][5:], s["project"][:30], s["id"], s["title"]))
    print("\n恢复:在对应 cwd 跑  claude --resume <会话id>(UI 只能看+给命令,交互 resume 在终端)")


def cmd_ask_user(args):
    """agent 把一条【待用户确认】项投进 UI 待办池(todos.json),不滚走。capture-only,无自动回路。
    结构与 WebUI todo_add 对齐(todos.json 是 list;status 用合法值"待办"让用户照常能在 UI 标完成);
    额外带 confirm_from/ref 供将来回路。source 标"确认·<谁问的>"便于 UI 筛选。"""
    f = BUS / "todos.json"
    try:
        items = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
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
    mark = {"armed": "✓在岗", "stale": "⚠掉线", "off": "—未醒"}
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


def main():
    ap = argparse.ArgumentParser(prog="agentbus")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register"); p.add_argument("--role", required=True); p.add_argument("--desc", default=""); p.set_defaults(fn=cmd_register)
    p = sub.add_parser("send"); p.add_argument("--to", required=True); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--requires-approval", action="store_true", dest="requires_approval"); p.set_defaults(fn=cmd_send)
    p = sub.add_parser("wait"); p.add_argument("--role", required=True); p.add_argument("--timeout", type=float, default=110.0); p.add_argument("--include-approval", action="store_true", dest="include_approval"); p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("serve"); p.add_argument("--timeout", type=float, default=590.0); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("request"); p.add_argument("--to", required=True); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--chain", default=""); p.add_argument("--timeout", type=float, default=300.0); p.set_defaults(fn=cmd_request)
    p = sub.add_parser("done"); p.add_argument("--id", required=True); p.add_argument("--role", default=None); p.add_argument("--result", required=True); p.add_argument("--status", default="ok", choices=["ok", "error"]); p.add_argument("--notify", default="auto", choices=["auto", "always", "never"]); p.add_argument("--to", default=None, help="显式回执目标(任务已被归档/reap、找不到原 from 时用)"); p.set_defaults(fn=cmd_done)
    p = sub.add_parser("peek"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_peek)
    p = sub.add_parser("replies"); p.add_argument("--role", required=True); p.add_argument("--clear", action="store_true"); p.set_defaults(fn=cmd_replies)
    p = sub.add_parser("hello"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_hello)
    p = sub.add_parser("who"); p.add_argument("--max-age", type=int, default=900); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_who)
    p = sub.add_parser("tag"); p.add_argument("--pid", type=int, required=True); p.add_argument("--label", required=True); p.add_argument("--note", default=""); p.add_argument("--public", action="store_true"); p.set_defaults(fn=cmd_tag)
    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("reap"); p.add_argument("--role", default=None); p.add_argument("--ttl", type=float, default=4000.0); p.add_argument("--grace", type=float, default=120.0); p.add_argument("--max-requeue", type=int, default=3, dest="max_requeue"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_reap)
    p = sub.add_parser("supervise"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_supervise)
    p = sub.add_parser("webui-guard"); p.add_argument("--port", type=int, default=8799); p.add_argument("--min-age", type=float, default=30.0, dest="min_age"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_webui_guard)
    p = sub.add_parser("health-sweep"); p.add_argument("--apply", action="store_true"); p.add_argument("--stuck-min", type=int, default=90, dest="stuck_min"); p.set_defaults(fn=cmd_health_sweep)
    p = sub.add_parser("health"); p.add_argument("--stale-min", type=int, default=30, dest="stale_min"); p.add_argument("--stuck-min", type=int, default=90, dest="stuck_min"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_health)
    p = sub.add_parser("prune"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_prune)
    p = sub.add_parser("selfwake"); p.set_defaults(fn=cmd_selfwake)
    p = sub.add_parser("caps"); p.add_argument("--role", default=None); p.set_defaults(fn=cmd_caps)
    p = sub.add_parser("roster"); p.add_argument("--grep", default=None); p.set_defaults(fn=cmd_roster)
    p = sub.add_parser("ask-user"); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", required=True); p.add_argument("--detail", default=""); p.add_argument("--priority", default="中"); p.add_argument("--deadline", default=""); p.add_argument("--ref", default=None); p.set_defaults(fn=cmd_ask_user)
    p = sub.add_parser("answer"); p.add_argument("--todo", required=True); p.add_argument("--reply", required=True); p.add_argument("--from", dest="frm", default=None); p.set_defaults(fn=cmd_answer)
    p = sub.add_parser("route"); p.add_argument("keywords", nargs="+"); p.set_defaults(fn=cmd_route)
    p = sub.add_parser("retire"); p.add_argument("--role", required=True); p.add_argument("--reason", default=""); p.add_argument("--succeeded-by", dest="succeeded_by", default=None); p.add_argument("--session-ids", dest="session_ids", default=""); p.add_argument("--memory", default=""); p.set_defaults(fn=cmd_retire)
    p = sub.add_parser("revive"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_revive)
    p = sub.add_parser("retired"); p.set_defaults(fn=cmd_retired)
    p = sub.add_parser("sessions"); p.add_argument("--project", default=None); p.add_argument("--limit", type=int, default=0); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_sessions)
    p = sub.add_parser("edge-acquire"); p.add_argument("--who", default=None); p.add_argument("--timeout", type=float, default=120.0); p.add_argument("--ttl", type=float, default=300.0); p.set_defaults(fn=cmd_edge_acquire)
    p = sub.add_parser("edge-release"); p.add_argument("--who", default=None); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_edge_release)
    p = sub.add_parser("dispatch"); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--name", default="adhoc"); p.add_argument("--cwd", default=None); p.add_argument("--timeout", type=float, default=3600.0); p.set_defaults(fn=cmd_dispatch)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
