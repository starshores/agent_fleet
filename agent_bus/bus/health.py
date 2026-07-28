# -*- coding: utf-8 -*-
"""bus.health —— 运维/健康域:孤儿回收(reap)· 会话归档(prune)· WebUI 守卫(webui-guard)
· 消息丢失巡检自愈(health-sweep)· 只读健康总检(health)· 停滞待办(stale)· daemon 舰队自愈(supervise)。

依赖：config(BUS/cfg/_now)· io(_audit/_write_json)· proc(_age_seconds)· roster(_list_roles/_retired_roles/
_is_test_role/_load_agents)· edge(_edge_holder)· presence(_owner_live)· selfwake(_is_parked/_self_wake_state/_wait_proc_roles)
· psutil/subprocess/shutil。无反向依赖=无环。
"""
import json, os, shutil, subprocess, time

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, cfg, _now
from bus.io import _audit, _write_json, json_lock
from bus.proc import _age_seconds
from bus.roster import _list_roles, _retired_roles, _is_test_role, _load_agents, _policy
from bus.edge import _edge_holder
from bus.presence import _owner_live
from bus.selfwake import _is_parked, _self_wake_state, _wait_proc_roles


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
            # P1(done/reap 崩溃窗口):done 先写回执后归档,崩在中间会留 processing 残骸——
            # 回执已在请求方 done/ = 任务其实做完了,归档补齐即可,绝不能 requeue 重跑。
            sender = obj.get("from")
            if sender and (BUS / sender / "done" / (tid + ".json")).exists():
                if not args.dry_run:
                    obj["reap_note"] = "receipt-exists→archived(done崩在归档前的残骸补齐)"
                    try:
                        _write_json(p, obj)
                        os.replace(str(p), str(BUS / role / "archive" / p.name))
                    except Exception:
                        pass
                actions.append(("回执已在→归档免重跑", role, tid)); continue
            if rq > args.max_requeue:
                if not args.dry_run:
                    failed = BUS / role / "failed"; failed.mkdir(parents=True, exist_ok=True)
                    obj["status"] = "failed_max_requeue"; obj["reaped"] = _now()
                    # 先原地改写再原子搬运(P1:原"写目标+删源"两步,崩在中间=双份双跑)
                    try:
                        _write_json(p, obj)
                        os.replace(str(p), str(failed / p.name))
                    except Exception:
                        pass
                actions.append(("毒任务→failed(超%d次)" % args.max_requeue, role, tid)); continue
            if not args.dry_run:
                obj["status"] = "pending"; obj["requeue_count"] = rq
                obj.pop("claimed", None); obj["requeued_at"] = _now()
                # 同上:先原地改写、再 os.replace 原子搬回 inbox——任一时刻文件只在一处,绝无双份
                try:
                    _write_json(p, obj)
                    os.replace(str(p), str(BUS / role / "inbox" / p.name))
                except Exception:
                    pass
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
# 单一源=role_profiles.json meta.policy(P1·2026-07-28 出代码进注册表);硬编码仅为注册表不可读时的降级默认。
_pol = _policy()
_MGMT_OWNER = dict(_pol.get("mgmt_owner") or {"MasterUI": "MasterAgent", "培训师": "MasterAgent"})
_BIZ_OWNER = _pol.get("biz_default_owner") or "Master"
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
        return _BIZ_OWNER
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
    with json_lock(tf):   # 锁跨 RMW(P0):ask-user/answer/archive/WebUI 并发裸写会丢待办;异常扫描已在锁外完成
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
    rep["hygiene"] = _hygiene_core(apply=apply)   # 卫生代谢随巡检同跑(supervise 90s 周期自动带上)
    return rep


def _hygiene_core(apply=False, done_days=14, dead_days=7, bak_days=14,
                  tmp_hours=24, lock_hours=1, jsonl_mb=20, terminal_days=90, trash_days=30):
    """消息/文件卫生代谢(2026-07-28 用户令·2.0 收尾 + P1 扩容:bus 会自己变脏,给它代谢)。
    默认 dry-run 只报数,apply 才动:
    ① 各 role done/ 回执超 done_days 天 → 移 <role>/done_archive/(replies 只看近窗;events.jsonl 审计仍全量);
    ② unknown 死信桶(inbox/processing/done)超 dead_days 天 → 移 unknown/_dead_letter/;
    ③ 运行态根 *.bak* 超 bak_days 天 → 移 BUS/.trash/;
    ④ 孤儿 *.tmp(>tmp_hours 小时·崩溃残渣,_write_json 正常毫秒级)→ 直接删;
    ⑤ 孤儿 *.lock / *.steal-*(>lock_hours 小时·持锁毫秒级,stale 夺取通常已自愈,此为终兜底)→ 直接删;
    ⑥ pid_tags.json 死 pid 条目 → 摘除(标注进程早已退,留着=who 视图僵尸);
    ⑦ events.jsonl / metrics.jsonl 超 jsonl_mb MB → 轮转为 <名>.<日期>.1(append-only 只会涨,留档不丢审计);
    ⑧ 终点站保留期:done_archive//_dead_letter/ 超 terminal_days 天 → 移 .trash;.trash 超 trash_days 天 → 真删
      (两段式:任何东西进 .trash 后仍有 trash_days 反悔窗,唯一真删点)。"""
    rep = {"done_archived": 0, "dead_lettered": 0, "bak_trashed": 0, "tmp_gone": 0, "lock_gone": 0,
           "pidtag_gone": 0, "jsonl_rotated": 0, "terminal_trashed": 0, "trash_purged": 0, "apply": apply}
    now = time.time()

    def _old(p, days):
        try:
            return (now - p.stat().st_mtime) > days * 86400
        except Exception:
            return False

    def _mv(f, dst_dir, key):
        rep[key] += 1
        if apply:
            try:
                dst_dir.mkdir(exist_ok=True)
                shutil.move(str(f), str(dst_dir / f.name))
            except Exception:
                rep[key] -= 1

    def _rm(f, key):
        rep[key] += 1
        if apply:
            try:
                f.unlink()
            except Exception:
                rep[key] -= 1

    for d in sorted(BUS.iterdir()):          # ① done 轮转(跳 .开头/_开头;unknown 走②)
        if not d.is_dir() or d.name.startswith((".", "_")) or d.name == "unknown":
            continue
        dn = d / "done"
        if dn.is_dir():
            for f in dn.glob("*.json"):
                if _old(f, done_days):
                    _mv(f, d / "done_archive", "done_archived")
    u = BUS / "unknown"                      # ② 死信桶
    if u.is_dir():
        for sub in ("inbox", "processing", "done"):
            sd = u / sub
            if sd.is_dir():
                for f in sd.glob("*.json"):
                    if _old(f, dead_days):
                        _mv(f, u / "_dead_letter", "dead_lettered")
    for f in BUS.glob("*.bak*"):             # ③ bak 清扫
        if f.is_file() and _old(f, bak_days):
            _mv(f, BUS / ".trash", "bak_trashed")
    # ④⑤ 孤儿 tmp/lock 残渣(根 + 各 role 目录及其子队列 + 组目录;崩溃才会留下)
    for pat, hours, key in (("*.tmp", tmp_hours, "tmp_gone"),
                            ("*.lock", lock_hours, "lock_gone"),
                            ("*.steal-*", lock_hours, "lock_gone")):
        for f in BUS.rglob(pat):
            if f.is_file() and _old(f, hours / 24.0):
                _rm(f, key)
    # ⑥ pid_tags 死 pid(psutil 可用才判;不可用跳过=不误删)
    try:
        import psutil
        ptf = BUS / "pid_tags.json"
        if ptf.exists():
            tags = json.loads(ptf.read_text(encoding="utf-8"))
            dead = [k for k in tags if not psutil.pid_exists(int(k))]
            if dead:
                rep["pidtag_gone"] = len(dead)
                if apply:
                    for k in dead:
                        tags.pop(k, None)
                    _write_json(ptf, tags)
    except Exception:
        pass
    # ⑦ 审计/埋点 jsonl 轮转(超限改名留档,新文件从零涨;全史=当前+轮转档)
    for name in ("events.jsonl", "metrics.jsonl"):
        f = BUS / name
        try:
            if f.exists() and f.stat().st_size > jsonl_mb * 1024 * 1024:
                rep["jsonl_rotated"] += 1
                if apply:   # 时间戳到秒:同日多次轮转不互相覆盖
                    os.replace(str(f), str(BUS / ("%s.%s.1" % (name, time.strftime("%Y%m%d-%H%M%S")))))
        except Exception:
            rep["jsonl_rotated"] = max(0, rep["jsonl_rotated"] - 1)
    # ⑧ 终点站保留期:done_archive / _dead_letter 超期 → .trash;.trash 超期 → 真删(唯一真删点)
    for d in sorted(BUS.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")):
            da = d / "done_archive"
            if da.is_dir():
                for f in da.glob("*.json"):
                    if _old(f, terminal_days):
                        _mv(f, BUS / ".trash", "terminal_trashed")
    dl = BUS / "unknown" / "_dead_letter"
    if dl.is_dir():
        for f in dl.glob("*.json"):
            if _old(f, terminal_days):
                _mv(f, BUS / ".trash", "terminal_trashed")
    tr = BUS / ".trash"
    if tr.is_dir():
        for f in tr.iterdir():
            if f.is_file() and _old(f, trash_days):
                _rm(f, "trash_purged")
    if apply and any(rep[k] for k in rep if k != "apply"):
        _audit("hygiene_sweep", **{k: v for k, v in rep.items() if k != "apply" and v})
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
