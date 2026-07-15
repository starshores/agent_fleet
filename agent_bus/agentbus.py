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
    _retired_roles, _is_test_role, _PSEUDO_ROLE_LABELS, _pseudo_role_label, _case_collision,
)
from bus.proc import _claude_ancestor, _proc_scan, _alive_claude_pids, _age_seconds  # noqa: E402
from bus.presence import _touch_presence, _set_current_task, _pidtags_file, _load_pidtags, _owner_live  # noqa: E402
from bus.selfwake import _wait_proc_roles, _parked_path, _is_parked, _park_expired, _self_wake_state, _ancestor_pids  # noqa: E402
from bus.roster import HUMAN_ATTENDED  # noqa: E402
from bus.notify import _toast_throttled, _fire_toast, _maybe_toast, _emit_completion_notice  # noqa: E402
from bus.mailbox import _claim, _claim_and_print, _resolve_self_role, _oldest_claimable  # noqa: E402
from bus.pool import (  # noqa: E402
    _GROUPS_ROOT, _group_dir, _group_members_file, _load_group_members, _group_caps_baseline, _role_caps,
    _role_groups, _save_group, cmd_join_group, cmd_leave_group, cmd_park, cmd_unpark,
    _GROUP_LOCK_TTL, _group_lock_stale, _claim_group_task, cmd_send_group, _mem_cap, cmd_mem_cap,
    _fanout_dir, _find_fanout_manifest, cmd_fanout, cmd_fanout_collect,
)
from bus.core import (  # noqa: E402
    _warn_unreachable, cmd_send, _wait_core, _wait_for_reply, _kill_prior_waits, _serve_wait,
    cmd_wait, cmd_serve, cmd_request, _find_task, cmd_done, cmd_peek,
    cmd_replies,
)
from bus.edge import (  # noqa: E402
    _edge_lock_path, _edge_holder, _role_proc_alive, _edge_stale, _wait_file_gone,
    cmd_edge_acquire, cmd_edge_release,
)
from bus.roles import (  # noqa: E402
    _retired_roster_path, _load_retired_roster,
    cmd_retire, cmd_revive, cmd_retired,
    cmd_route, cmd_roster, cmd_caps,
)
from bus.exec import cmd_dispatch  # noqa: E402
from bus.sessions import cmd_sessions  # noqa: E402
from bus.tasks import cmd_tasklines, cmd_answer, cmd_ask_user  # noqa: E402
from bus.health import (  # noqa: E402
    cmd_reap, cmd_prune, cmd_webui_guard, cmd_health_sweep, cmd_health, cmd_stale, cmd_supervise,
)
from bus.status import _who_data, cmd_who, cmd_list  # noqa: E402



# ---------------- 目标可达校验 _warn_unreachable(已抽到 bus/core.py)----------------
def cmd_register(args):
    role = args.role
    dup = _case_collision(role)
    if dup:
        sys.stderr.write("⚠ 拒绝注册:角色名 %r 与既有 %r 仅大小写不同——Windows 文件系统不区分大小写、会共用同一 mailbox 目录(agents.json 却当两个角色→幽灵重复+路由错乱)。请改用既有名 %r,或换个真正不同的名。\n" % (role, dup, dup))
        sys.exit(2)
    _role_dir(role)
    a = _load_agents()
    a[role] = {"registered": _now(), "desc": args.desc or ""}
    _save_agents(a)
    print("registered role:", role)


# ---------------- 消息任务流 core:send/wait/done/request/serve/peek/replies(已抽到 bus/core.py · 见文件头 import)----------------
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
    dup = _case_collision(role)
    if dup:
        sys.stderr.write("⚠ 拒绝 hello:角色名 %r 与既有 %r 仅大小写不同——Windows 文件系统不区分大小写、会共用同一 mailbox(致幽灵重复+绑定错乱)。你多半想声明 %r,请用它的准确大小写重跑。\n" % (role, dup, dup))
        sys.exit(2)
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
    fb = '(若新 shell 提示 `ab: command not found`=PATH 未刷新,用全路径 "C:/apphome/agent_fleet/agent_bus/ab" <命令>,或兜底 "%s" "%s" <命令>)' % (py, abpy)
    print("\n【★★ 你的第一个动作·立刻照做,别往下读、也别先扎进任务 ★★】")
    print("你现在【还没在岗·收不到任何消息】。【开始任何任务之前】,先把下面这条 wait 挂成后台任务、再验在岗——【先在岗,再开工】。")
    print("就算你是被叫来干某个活的:也【先挂 wait 再干活】,否则干活期间收不到新消息、干完也没挂=直接掉线,bus 上没人联系得到你:")
    print('  ① 后台跑(交互 tab 用 run_in_background、别前台阻塞): ab wait --role %s --timeout 3600' % role)
    print('  ② 验在岗: ab selfwake   (看本 role 是否 ✓在岗;没起来就再挂+再验,直到确认)')
    print("  ③ 确认 ✓在岗后,才开始做你被交代的任务;之后每次被唤起、处理完,都要【再挂一次】wait(不重挂=掉线)。")
    print("  " + fb)
    print("  (注:hello 只登记身份+打印本说明,【不会替你把自醒跑起来】——必须你自己执行①②③。下面是完整能力说明。)")
    print("\n==== 你的基本能力(每个 agent 都该会;详见 agent_fleet/AGENTS.md + BUS_ONBOARDING.md)====")
    print("\n[基本能力 1] 事件自醒(可对话又不漏消息;交互 tab 建议挂上):")
    print("把下面这条当【后台任务】跑(别前台阻塞):有人给你发消息/派活时它就完成 → 你的会话被自动唤起去处理:")
    print('  ab wait --role %s --timeout 3600' % role)
    print("【铁律①】每次被唤起、处理完,必须【再挂一个】同样的 wait —— 不重挂就掉线,之后的消息收不到。")
    print("【铁律②】用短入口 `ab`(已固化【正确 env python + 真实项目根路径 + UTF-8】,免掉裸 `python`/长路径/junction/反斜杠诸坑)。" + fb)
    print("【铁律③·最重要】后台重挂会【偶发 127 静默失败】(harness 后台 shell 启动层抽风,非你的错)——")
    print("   所以重挂【发了不算数】:挂完【验证】自己在岗(跑 `ab selfwake` 看本 role 是否 ✓在岗),没起来就【再挂+再验】直到确认在岗。否则=静默掉聋。")
    print('收到 kind="notice" 的完成通知:读它 notice_of 指向的 %s/done/<id>.json 取完整结果,再 done 掉该 notice(读完即弃,勿当任务执行)。' % role)
    print("【铁律④·上下文卫生】被唤起后【若 inbox 无新活】(空唤醒/只是 ACK 通知/idle 超时)→【静默重挂 + 最多一行待命 + 绝不重新 dump selfwake/who/花名册/状态】。")
    print("   每次空醒都 dump 状态=自己给自己灌上下文→更快撑到 1M 顶→被迫 compact→失能,是失能的【自我加速器】。口诀:【空唤醒→静默重挂→不重述状态】。")
    print("\n[基本能力 2] 待办记录(要用户确认/拍板的事别只在对话里说——会滚走漏掉,用它投进待办池):")
    print('  ab ask-user --from %s --title "<要用户定的事>" --detail "<上下文/选项>"' % role)
    print("  → 落进 UI 待办池的🙋待确认队列,用户在一处统一处理、不再错过。【凡需用户拍板都走这个,是标配动作】。")
    print("\n[专一性纪律·防失能] 只做你的专长(见你的 caps/desc);【跨领域的活别自接】——")
    print('  用 `ab roster --grep <关键词>` 找对口角色转给它,不确定就问 Master/培训师 路由。')
    print("  原因:一肩挑全科会让上下文从源头就杂、越堆越乱,最终撑爆窗口→ compact 有损→ 失能退役")
    print("  (这是【业务侧某 agent】的真实教训:无分工时代被迫全科,积重难返。做减法=保专一=防失能)。")
    print("\n[基本能力 3] 卡住别硬扛——查能力池 / 请培训师补训(【是你的活、但有棘手子块】时):")
    print("  硬扛最贵(易做坏 + 烧窗口)。两条出路:")
    print('  ① 查能力池看谁会:`ab caps` / `ab roster --grep <关键词>`——若其实是别人专长,轻问或路由给他(换人做);')
    print('  ② 请培训师补训这块:`ab send --to 培训师 --from %s --task "我在做X、卡在Y这棘手块,请按能力池给我补训"`(培训师也【按需补训老人】,教我做)。' % role)
    print("  口诀:棘手 → 先查能力池 → 换人做 或 请培训师教我做,别硬扛。")


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


# ---------------- 工作组池化 / park / fan-out / mem-cap(已抽到 bus/pool.py · 见文件头 import)----------------
# BUS 2.0 命令分组(对齐 bus/ 一体化模块;供 `ab`/`ab commands`/无参时分组呈现·治 43 命令扁平难发现)
_CMD_GROUPS = [
    ("消息任务流 core", "send wait serve request done peek replies answer"),
    ("身份·自醒 presence/selfwake", "hello whoami selfwake selfwake-guard"),
    ("角色·能力 roles", "register retire revive retired caps roster route"),
    ("健康·运维 health", "who list health health-sweep reap supervise prune webui-guard mem-cap stale"),
    ("工作组池 pool", "join-group leave-group send-group fanout fanout-collect park unpark"),
    ("派遣执行 exec", "dispatch"),
    ("Edge 互斥", "edge-acquire edge-release"),
    ("长期任务·待办 tasks", "tasklines ask-user"),
    ("会话·标注", "sessions tag"),
]


def _print_grouped_help():
    print("agentbus / ab —— bus 命令(BUS 2.0 · 按一体化模块分组)")
    print("用法: ab <命令> [参数];`ab <命令> -h` 看某命令详情。短入口 ab 已固化正确 env python + 真实路径 + UTF-8。")
    for title, cmds in _CMD_GROUPS:
        print("\n  【%s】" % title)
        print("    " + "  ".join(cmds.split()))
    print("")


def cmd_commands(args):
    _print_grouped_help()


def main():
    if len(sys.argv) == 1:          # 无参 = 分组命令总览(UX:别抛 argparse 错)
        _print_grouped_help(); return
    ap = argparse.ArgumentParser(prog="agentbus")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("commands", help="分组命令总览"); p.set_defaults(fn=cmd_commands)

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
