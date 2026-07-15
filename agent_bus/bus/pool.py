# -*- coding: utf-8 -*-
"""bus.pool —— 工作组池化(Phase 1a/1b):共享组 inbox + O_EXCL pull-claim + fan-out/collect
+ park 休眠命令 + 内存感知并发上限(mem-cap)。依赖下层 config/io/roster/proc/presence/selfwake/mailbox。
无环:pool 复用 mailbox 的领取原语,不反向依赖 core。"""
import os, sys, json, time

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, METRICS, cfg, _now
from bus.io import _gen_id, _write_json, _oldest, _audit, record_metric, read_metrics
from bus.roster import (_agents_file, _load_agents, _save_agents, _role_dir, _list_roles,
                        _retired_roles, _is_test_role, _PSEUDO_ROLE_LABELS, _pseudo_role_label, HUMAN_ATTENDED)
from bus.proc import _claude_ancestor, _proc_scan, _alive_claude_pids, _age_seconds
from bus.presence import _touch_presence, _set_current_task, _pidtags_file, _load_pidtags, _owner_live
from bus.selfwake import _wait_proc_roles, _parked_path, _is_parked, _park_expired, _self_wake_state, _ancestor_pids
from bus.mailbox import _claim, _claim_and_print, _resolve_self_role, _oldest_claimable


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
