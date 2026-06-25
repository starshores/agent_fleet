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
    os.replace(tmp, path)  # 原子落地,避免 watcher 读到半截


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
    _write_json(proc, obj)
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
    print("\n==== 你的基本能力(每个 agent 都该会;详见 agent_fleet/AGENTS.md + BUS_ONBOARDING.md)====")
    print("\n[基本能力 1] 事件自醒(可对话又不漏消息;交互 tab 建议挂上):")
    print("把下面这条当【后台任务】跑(别前台阻塞):有人给你发消息/派活时它就完成 → 你的会话被自动唤起去处理:")
    print('  python "%s" wait --role %s --timeout 3600' % (abpy, role))
    print("【铁律】每次被唤起、处理完,必须【再挂一个】同样的 wait —— 不重挂就掉线,之后的消息收不到。")
    print('收到 kind="notice" 的完成通知:读它 notice_of 指向的 %s/done/<id>.json 取完整结果,再 done 掉该 notice(读完即弃,勿当任务执行)。' % role)
    print("\n[基本能力 2] 待办记录(要用户确认/拍板的事别只在对话里说——会滚走漏掉,用它投进待办池):")
    print('  python "%s" ask-user --from %s --title "<要用户定的事>" --detail "<上下文/选项>"' % (abpy, role))
    print("  → 落进 UI 待办池的🙋待确认队列,用户在一处统一处理、不再错过。【凡需用户拍板都走这个,是标配动作】。")


def cmd_who(args):
    """谁在跑 + 是谁:交叉核对 presence 心跳 与 实际 claude 进程。"""
    fresh = args.max_age
    alive = _alive_claude_pids()
    roles = _list_roles()
    claimed = set()
    print("%-14s %-22s %-9s %8s  %s" % ("role", "verdict", "claude_pid", "心跳age", "cwd"))
    print("-" * 88)
    for r in roles:
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
    """重复/陈旧 WebUI = 在跑 server.py 但【没占住 port】的 python 进程(如 31468:旧实例没占端口却还跑 supervisor)。
    安全铁律:只在【确实有一个 server.py 进程占住 port】时,才认定其余没占端口的为陈旧;否则一个不动(可能正在启动)。
    再加 min_age 秒龄保护(太年轻可能在 bind 中,跳过)。返回 [(pid, age_s), ...]。绝不返回占端口的那个。"""
    if psutil is None:
        return []
    webui = {}
    for p in psutil.process_iter(["name", "cmdline", "create_time"]):
        try:
            if "python" not in (p.info.get("name") or "").lower():
                continue
            if any("server.py" in str(x) for x in (p.cmdline() or [])):
                webui[p.pid] = p.info.get("create_time")
        except Exception:
            continue
    if len(webui) <= 1:
        return []
    owner = None
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == "LISTEN" and c.laddr and c.laddr.port == port and c.pid:
                owner = c.pid
                break
    except Exception:
        return []                      # 拿不到端口归属 → 不动(安全)
    if owner is None or owner not in webui:
        return []                      # 没有明确占端口的 server.py → 不动(可能在启动/已切到非 server.py)
    now = time.time()
    stale = []
    for pid, ct in webui.items():
        if pid == owner:
            continue                   # 占端口的=在岗,绝不杀
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
    if not actions:
        print("supervise: fleet.json 无 daemon 条目,且无孤儿 daemon / 陈旧 WebUI"); return
    for a, r in actions:
        print("[%s] %s" % (a, r))


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
    armed = stale = off = 0
    wait_roles = _wait_proc_roles()   # 一次性预扫,循环内复用
    print("%-14s %-8s %s" % ("role", "自醒", "说明"))
    print("-" * 78)
    for role in _list_roles():
        st, detail = _self_wake_state(role, wait_roles=wait_roles)
        mark = {"armed": "✓在岗", "stale": "⚠掉线", "off": "—未醒"}[st]
        print("%-14s %-8s %s" % (role, mark, detail))
        armed += st == "armed"; stale += st == "stale"; off += st == "off"
    print("-" * 78)
    print("自醒在岗 %d / 掉线 %d / 未醒 %d" % (armed, stale, off))
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
                  if kw in r.lower() or kw in (v.get("desc", "") or "").lower()
                  or kw in " ".join(v.get("caps", [])).lower()}
    wait_roles = _wait_proc_roles()
    mark = {"armed": "✓在岗", "stale": "⚠掉线", "off": "—未醒"}
    hdr = ("花名册·匹配「%s」(%d):" % (kw, len(agents))) if kw else ("花名册(agents.json · %d 个角色):" % len(agents))
    print(hdr)
    print("-" * 78)
    if not agents:
        print("(无匹配——可能无此专责;考虑广播,或 register 新专家)"); return
    for role, v in agents.items():
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
    p = sub.add_parser("done"); p.add_argument("--id", required=True); p.add_argument("--role", default=None); p.add_argument("--result", required=True); p.add_argument("--status", default="ok", choices=["ok", "error"]); p.add_argument("--notify", default="auto", choices=["auto", "always", "never"]); p.set_defaults(fn=cmd_done)
    p = sub.add_parser("peek"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_peek)
    p = sub.add_parser("replies"); p.add_argument("--role", required=True); p.add_argument("--clear", action="store_true"); p.set_defaults(fn=cmd_replies)
    p = sub.add_parser("hello"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_hello)
    p = sub.add_parser("who"); p.add_argument("--max-age", type=int, default=900); p.set_defaults(fn=cmd_who)
    p = sub.add_parser("tag"); p.add_argument("--pid", type=int, required=True); p.add_argument("--label", required=True); p.add_argument("--note", default=""); p.add_argument("--public", action="store_true"); p.set_defaults(fn=cmd_tag)
    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("reap"); p.add_argument("--role", default=None); p.add_argument("--ttl", type=float, default=4000.0); p.add_argument("--grace", type=float, default=120.0); p.add_argument("--max-requeue", type=int, default=3, dest="max_requeue"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_reap)
    p = sub.add_parser("supervise"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_supervise)
    p = sub.add_parser("webui-guard"); p.add_argument("--port", type=int, default=8799); p.add_argument("--min-age", type=float, default=30.0, dest="min_age"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_webui_guard)
    p = sub.add_parser("prune"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_prune)
    p = sub.add_parser("selfwake"); p.set_defaults(fn=cmd_selfwake)
    p = sub.add_parser("caps"); p.add_argument("--role", default=None); p.set_defaults(fn=cmd_caps)
    p = sub.add_parser("roster"); p.add_argument("--grep", default=None); p.set_defaults(fn=cmd_roster)
    p = sub.add_parser("ask-user"); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", required=True); p.add_argument("--detail", default=""); p.add_argument("--priority", default="中"); p.add_argument("--deadline", default=""); p.add_argument("--ref", default=None); p.set_defaults(fn=cmd_ask_user)
    p = sub.add_parser("sessions"); p.add_argument("--project", default=None); p.add_argument("--limit", type=int, default=0); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_sessions)
    p = sub.add_parser("edge-acquire"); p.add_argument("--who", default=None); p.add_argument("--timeout", type=float, default=120.0); p.add_argument("--ttl", type=float, default=300.0); p.set_defaults(fn=cmd_edge_acquire)
    p = sub.add_parser("edge-release"); p.add_argument("--who", default=None); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_edge_release)
    p = sub.add_parser("dispatch"); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--name", default="adhoc"); p.add_argument("--cwd", default=None); p.add_argument("--timeout", type=float, default=3600.0); p.set_defaults(fn=cmd_dispatch)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
