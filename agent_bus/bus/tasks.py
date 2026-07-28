# -*- coding: utf-8 -*-
"""bus.tasks —— 长期任务/待办域一体化:长期任务线看板(tasklines)+ 待办池(ask-user/answer)。

tasklines.json = 长期任务线看板运行态(gitignore)；决策点 ref → todos.json 条目显示已答/待答。
todos.json = ask-user 待办池(非阻塞·capture-only)；cmd_ask_user 投池(bus 成员门禁 _is_bus_member)、
cmd_answer 闭环回路(答复路由回提问 agent + 标完成)。落磁盘=防 compact+跨会话。

依赖：config(BUS/_now)· io(_gen_id/_write_json/_audit)· core(_warn_unreachable)· roster(_load_agents)。无反向依赖=无环。
"""
import json, os, sys, time

from bus.config import BUS, _now
from bus.io import _gen_id, _write_json, _audit, json_lock
from bus.core import _warn_unreachable
from bus.roster import _load_agents


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


def cmd_answer(args):
    """待办回路(闭环 ask-user,补 capture-only 缺口):用户答复某条🙋待确认项 →
    答复【自动路由回提问的 agent】(confirm_from)让它接着干 + 标该待办完成。
    谁替用户跑:协调位(Master/MasterAgent)转达,或 WebUI 回复框(MasterUI 接 /api 调本命令)。"""
    tf = BUS / "todos.json"
    with json_lock(tf):   # 锁跨 RMW(P0):ask-user/answer/sweep/WebUI 四方并发裸写会互相覆盖丢待办
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
    with json_lock(f):   # 锁跨 RMW(P0)+ _write_json 原子落地(替换原手写 tmp+replace)
        try:
            items = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
            if not isinstance(items, list):
                items = []
        except Exception:
            items = []
        items.append(item)
        _write_json(f, items)
    _audit("ask_user", id=tid, frm=frm, title=item["title"][:40])
    print("已投入待办池(待用户确认):《%s》[%s] from %s" % (item["title"], tid, frm))


# ── B3(2026-07-21):todos.json 完成态 auto-archive ───────────────────────────
# Master数分定位:todos.json 膨胀根因=完成态条目从不清出(191/235 是完成态)→ 文件胖 + "待办停滞"看板虚高。
# 归档=把完成态移到 todos_archive.json(留痕不删),池只留活的(待办/进行中)。防复堆。
_DONE_STATUSES = ("完成", "已完成")


def _todo_age_min(ts, now):
    """ISO(_now 格式 %Y-%m-%dT%H:%M:%S)→ 距 now 的分钟;解析不出返 None。"""
    try:
        return (now - time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))) / 60.0
    except Exception:
        return None


def _archive_done_todos(older_than_min=0, apply=False):
    """把 todos.json 完成态(完成/已完成)移到 todos_archive.json(append·留痕)。
    older_than_min>0 只归档 updated 早于该阈值的(近期完成态留给用户过目);0=全部完成态。
    先落 archive 再瘦 todos(原子写 + 备份 todos.json.pre-archive.bak)。返回处置报告。只动完成态·不碰活的。"""
    tf = BUS / "todos.json"
    with json_lock(tf):   # 锁跨 RMW(P0):归档瘦身与 ask-user/answer/sweep 并发时不丢新投待办
        try:
            todos = json.loads(tf.read_text(encoding="utf-8"))
            if not isinstance(todos, list):
                todos = []
        except Exception:
            return {"before": 0, "archived": 0, "after": 0, "dry": not apply, "err": "todos.json 读失败"}
        now = time.time()

        def _archivable(t):
            if t.get("status") not in _DONE_STATUSES:
                return False
            if older_than_min <= 0:
                return True
            age = _todo_age_min(t.get("updated") or t.get("created") or "", now)
            return age is None or age >= older_than_min

        done = [t for t in todos if _archivable(t)]
        kept = [t for t in todos if not _archivable(t)]
        rep = {"before": len(todos), "archived": len(done), "after": len(kept), "dry": not apply}
        if apply and done:
            af = BUS / "todos_archive.json"
            try:
                arch = json.loads(af.read_text(encoding="utf-8")) if af.exists() else []
                if not isinstance(arch, list):
                    arch = []
            except Exception:
                arch = []
            arch.extend(done)
            (BUS / "todos.json.pre-archive.bak").write_text(
                json.dumps(todos, ensure_ascii=False, indent=1), encoding="utf-8")
            _write_json(af, arch)   # 先落归档(留痕)
            _write_json(tf, kept)   # 再瘦待办池
            _audit("todos_archive", archived=len(done), after=len(kept))
        return rep


def cmd_todos_archive(args):
    """B3:归档 todos.json 完成态到 todos_archive.json(池只留活的·防完成态堆积膨胀 + 消"待办停滞"虚高)。
    --older-min N 只归档完成早于 N 分钟的(默认 0=全部完成态);默认 dry-run,--apply 才落地。"""
    rep = _archive_done_todos(older_than_min=getattr(args, "older_min", 0) or 0,
                              apply=getattr(args, "apply", False))
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if rep.get("dry"):
        print("(dry-run·加 --apply 落地;落地会备份 todos.json.pre-archive.bak)")
