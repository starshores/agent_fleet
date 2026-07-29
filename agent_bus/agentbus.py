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
import argparse, os, sys  # 薄分发器:仅 main 解析/退出码用;json/time/threading/socket/subprocess 随各命令抽入 bus/ 后不再需
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
    cmd_route_reap, route_on_member_done, _route_send, _route_drain_group, _route_reap_group,
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
    _retired_roster_path, _load_retired_roster, _load_caps_pool,
    cmd_retire, cmd_revive, cmd_retired,
    cmd_route, cmd_roster, cmd_caps,
)
from bus.exec import cmd_dispatch  # noqa: E402
from bus.sessions import cmd_sessions, cmd_resume  # noqa: E402
from bus.tasks import cmd_tasklines, cmd_answer, cmd_ask_user, cmd_todos_archive  # noqa: E402
from bus.refs import cmd_refs  # noqa: E402
from bus.health import (  # noqa: E402
    cmd_reap, cmd_prune, cmd_webui_guard, cmd_health_sweep, cmd_health, cmd_stale, cmd_supervise,
    _daemon_alive,   # ui/api.py fleet 面板用 ab._daemon_alive——此前漏 re-export(fleet 空未炸的活雷·P1 补)
)
from bus.status import _who_data, cmd_who, cmd_list  # noqa: E402
from bus.storage import cmd_storage_audit, storage_audit  # noqa: E402
from bus.fleetlog import cmd_fleetlog, cmd_maint, cmd_fleet_state, fleet_log, fleet_state, maint_active  # noqa: E402
from bus.bootcheck import cmd_boot_check, boot_check, cmd_role_audit, cmd_memory_audit, cmd_caps_audit  # noqa: E402
from bus.identity import (  # noqa: E402
    cmd_register, cmd_tag, cmd_hello, cmd_whoami, cmd_selfwake, cmd_selfwake_guard, cmd_selfwake_exempt,
    cmd_train_done,
)

# ---- 稳定公共面契约(P1·2026-07-28):外部消费方(dispatch_agent / worker_daemon /
# pool_dispatcher / longrange_net / ui.api)经 `import agentbus as ab` 只应使用下列名字——
# 按 2026-07-28 全量 grep 实际使用面圈定。__all__ 之外的 re-export 仅为 bus/ 包内部兼容,
# 不承诺稳定,消费方新增依赖请先把名字加进这里(名单即契约·MasterUI↔MasterAgent 对称律:改动要知会消费方)。
__all__ = [
    # 路径/配置/时间
    "BUS", "METRICS", "cfg", "_now",
    # 原子IO/审计/BI指标
    "_write_json", "_gen_id", "_audit", "record_metric", "read_metrics",
    # 花名册/角色目录
    "_load_agents", "_list_roles", "_role_dir", "_is_test_role", "_load_caps_pool", "_load_retired_roster",
    # 进程/存在感/自醒
    "psutil", "_age_seconds", "_alive_claude_pids", "_touch_presence", "_load_pidtags",
    "_self_wake_state", "_wait_proc_roles", "_daemon_alive",
    # 消息流/通知
    "_wait_core", "_emit_completion_notice", "_fire_toast",
    # 组池/状态面
    "_group_dir", "_group_lock_stale", "_mem_cap", "_who_data",
    # 舰队状态/维修(WebUI 顶栏·2026-07-28)
    "fleet_state", "maint_active", "fleet_log",
    # 装载核验(2.3·WebUI 角色页可显每会话装载态)
    "boot_check",
    # 存储体检(2.4·WebUI 存储面板 P2 数据源)
    "storage_audit",
]



# BUS 2.0 命令分组(对齐 bus/ 一体化模块;供 `ab`/`ab commands`/无参时分组呈现·治 43 命令扁平难发现)
_CMD_GROUPS = [
    ("消息任务流 core", "send wait serve request done peek replies answer"),
    ("身份·自醒 presence/selfwake", "hello whoami selfwake selfwake-guard selfwake-exempt boot-check"),
    ("角色·能力 roles", "register retire revive retired caps roster route train-done role-audit caps-audit memory-audit"),
    ("健康·运维 health", "who list health health-sweep reap supervise prune webui-guard mem-cap stale storage-audit"),
    ("工作组池 pool", "join-group leave-group send-group(--route) route-reap fanout fanout-collect park unpark"),
    ("派遣执行 exec", "dispatch"),
    ("Edge 互斥", "edge-acquire edge-release"),
    ("长期任务·待办 tasks", "tasklines ask-user refs todos-archive"),
    ("会话·标注", "sessions tag resume"),
    ("舰队日志·维修 fleetlog", "fleetlog maint fleet-state"),
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
    p = sub.add_parser("send-group"); p.add_argument("--group", required=True); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--affinity", default=None, help="会话粘连:优先某成员(掉线/超时由 reap 回落组池)"); p.add_argument("--route", action="store_true", help="状态路由 push 模式:按成员 available(armed+空闲)自动挑一个 RR push 进其 inbox(单通道·无候选则排队);默认不带=pull 模型(丢组共享 inbox 谁空谁 pull)"); p.add_argument("--requires-approval", action="store_true", dest="requires_approval"); p.set_defaults(fn=cmd_send_group)
    p = sub.add_parser("route-reap"); p.add_argument("--group", default=None, help="指定组;不带=扫全部组"); p.set_defaults(fn=cmd_route_reap)
    p = sub.add_parser("fanout"); p.add_argument("--group", required=True); p.add_argument("--shards", required=True, help="JSON数组 / 换行分隔 / -(stdin)"); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.set_defaults(fn=cmd_fanout)
    p = sub.add_parser("fanout-collect"); p.add_argument("--id", required=True); p.add_argument("--group", default=None); p.add_argument("--wait", action="store_true", help="barrier:攒齐N个再返(默认流式边到边·barrier是例外)"); p.add_argument("--timeout", type=float, default=300.0); p.set_defaults(fn=cmd_fanout_collect)
    p = sub.add_parser("mem-cap"); p.add_argument("--per-worker-gb", type=float, default=0.5, dest="per_worker_gb"); p.add_argument("--reserve-gb", type=float, default=2.0, dest="reserve_gb"); p.set_defaults(fn=cmd_mem_cap)
    p = sub.add_parser("wait"); p.add_argument("--role", required=True); p.add_argument("--timeout", type=float, default=110.0, help="秒;0=哨兵值·自动时段策略(日2h/夜6h,见 _idle_timeout_policy),不是立即超时"); p.add_argument("--include-approval", action="store_true", dest="include_approval"); p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("serve"); p.add_argument("--timeout", type=float, default=590.0); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("request"); p.add_argument("--to", required=True); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--chain", default=""); p.add_argument("--timeout", type=float, default=300.0); p.set_defaults(fn=cmd_request)
    p = sub.add_parser("done"); p.add_argument("--id", required=True); p.add_argument("--role", default=None); p.add_argument("--result", required=True); p.add_argument("--status", default="ok", choices=["ok", "error"]); p.add_argument("--notify", default="auto", choices=["auto", "always", "never"]); p.add_argument("--to", default=None, help="显式回执目标(任务已被归档/reap、找不到原 from 时用)"); p.set_defaults(fn=cmd_done)
    p = sub.add_parser("peek"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_peek)
    p = sub.add_parser("replies"); p.add_argument("--role", required=True); p.add_argument("--clear", action="store_true"); p.set_defaults(fn=cmd_replies)
    p = sub.add_parser("hello"); p.add_argument("--role", default=None); p.add_argument("--preview", action="store_true", help="只读预览 onboarding 文本(WebUI 提示词审核页用),不带 --role 也能跑、无副作用"); p.add_argument("--pid", type=int, default=None, help="显式指定本 tab 的 claude 进程 pid(parent-walk/session 反查都失败时的兜底)"); p.set_defaults(fn=cmd_hello)
    p = sub.add_parser("train-done", help="培训师结业:置 trained=true 解除新角色待训警示(培训闸闭环)"); p.add_argument("--role", required=True); p.add_argument("--by", default=None); p.set_defaults(fn=cmd_train_done)
    p = sub.add_parser("who"); p.add_argument("--max-age", type=int, default=900); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_who)
    p = sub.add_parser("tag"); p.add_argument("--pid", type=int, required=True); p.add_argument("--label", required=True); p.add_argument("--note", default=""); p.add_argument("--public", action="store_true"); p.set_defaults(fn=cmd_tag)
    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("reap"); p.add_argument("--role", default=None); p.add_argument("--ttl", type=float, default=4000.0); p.add_argument("--grace", type=float, default=120.0); p.add_argument("--max-requeue", type=int, default=3, dest="max_requeue"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_reap)
    p = sub.add_parser("supervise"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_supervise)
    p = sub.add_parser("webui-guard"); p.add_argument("--port", type=int, default=8799); p.add_argument("--min-age", type=float, default=30.0, dest="min_age"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_webui_guard)
    p = sub.add_parser("health-sweep"); p.add_argument("--apply", action="store_true"); p.add_argument("--stuck-min", type=int, default=90, dest="stuck_min"); p.set_defaults(fn=cmd_health_sweep)
    p = sub.add_parser("health"); p.add_argument("--stale-min", type=int, default=30, dest="stale_min"); p.add_argument("--stuck-min", type=int, default=90, dest="stuck_min"); p.add_argument("--todo-stale-min", type=int, default=1440, dest="todo_stale_min"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_health)
    p = sub.add_parser("stale"); p.add_argument("--hours", type=int, default=24); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_stale)
    p = sub.add_parser("storage-audit"); p.add_argument("--threshold-mb", type=float, default=5.0, dest="threshold_mb"); p.add_argument("--top", type=int, default=10); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_storage_audit)
    p = sub.add_parser("tasklines"); p.add_argument("--id", default=None); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_tasklines)
    p = sub.add_parser("refs"); p.add_argument("--check", action="store_true"); p.add_argument("--purge-smoke", action="store_true"); p.set_defaults(fn=cmd_refs)
    p = sub.add_parser("prune"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_prune)
    p = sub.add_parser("selfwake"); p.set_defaults(fn=cmd_selfwake)
    p = sub.add_parser("selfwake-guard"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_selfwake_guard)
    p = sub.add_parser("selfwake-exempt"); p.add_argument("--role", default=None); p.add_argument("--off", action="store_true"); p.add_argument("--mode", default=None); p.add_argument("--reason", default=None); p.add_argument("--note", default=None); p.set_defaults(fn=cmd_selfwake_exempt)
    p = sub.add_parser("whoami"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_whoami)
    p = sub.add_parser("caps"); p.add_argument("--role", default=None); p.set_defaults(fn=cmd_caps)
    p = sub.add_parser("roster"); p.add_argument("--grep", default=None); p.set_defaults(fn=cmd_roster)
    p = sub.add_parser("ask-user"); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", required=True); p.add_argument("--detail", default=""); p.add_argument("--priority", default="中"); p.add_argument("--deadline", default=""); p.add_argument("--ref", default=None); p.set_defaults(fn=cmd_ask_user)
    p = sub.add_parser("answer"); p.add_argument("--todo", required=True); p.add_argument("--reply", required=True); p.add_argument("--from", dest="frm", default=None); p.set_defaults(fn=cmd_answer)
    p = sub.add_parser("todos-archive"); p.add_argument("--older-min", type=int, default=0, dest="older_min"); p.add_argument("--apply", action="store_true"); p.set_defaults(fn=cmd_todos_archive)
    p = sub.add_parser("route"); p.add_argument("keywords", nargs="+"); p.set_defaults(fn=cmd_route)
    p = sub.add_parser("retire"); p.add_argument("--role", required=True); p.add_argument("--reason", default=""); p.add_argument("--succeeded-by", dest="succeeded_by", default=None); p.add_argument("--session-ids", dest="session_ids", default=""); p.add_argument("--memory", default=""); p.set_defaults(fn=cmd_retire)
    p = sub.add_parser("revive"); p.add_argument("--role", required=True); p.set_defaults(fn=cmd_revive)
    p = sub.add_parser("retired"); p.set_defaults(fn=cmd_retired)
    p = sub.add_parser("sessions"); p.add_argument("--project", default=None); p.add_argument("--limit", type=int, default=0); p.add_argument("--json", action="store_true"); p.add_argument("--tag", default=None, help="给会话id打标记"); p.add_argument("--untag", default=None, help="撤会话id标记"); p.add_argument("--label", default=None, help="标签(默认'可恢复')"); p.add_argument("--note", default=None, help="标记备注"); p.add_argument("--tagged", action="store_true", help="只列已标记的会话"); p.add_argument("--live", action="store_true", help="只列运行中(有活进程)"); p.add_argument("--dead", action="store_true", help="只列死的(可恢复/休眠)"); p.add_argument("--non-bus", action="store_true", dest="non_bus", help="只列非bus(个人)会话"); p.add_argument("--bus", action="store_true", help="只列bus(agent)会话"); p.add_argument("--by-size", action="store_true", dest="by_size", help="按 resume jsonl 大小降序(辅助判断谁最大/该归档清)"); p.set_defaults(fn=cmd_sessions)
    p = sub.add_parser("resume", help="停机快速恢复面板:按角色列最后绑定会话+状态+恢复命令(presence 锚)"); p.add_argument("--role", default=None, help="只看某角色"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_resume)
    p = sub.add_parser("edge-acquire"); p.add_argument("--who", default=None); p.add_argument("--timeout", type=float, default=120.0); p.add_argument("--ttl", type=float, default=300.0); p.set_defaults(fn=cmd_edge_acquire)
    p = sub.add_parser("edge-release"); p.add_argument("--who", default=None); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_edge_release)
    p = sub.add_parser("fleetlog"); p.add_argument("--add", action="store_true", help="追加一条(默认=列最近)"); p.add_argument("--kind", default=None, help="milestone/incident/maintenance/retire/revive/train/note"); p.add_argument("--title", default=None); p.add_argument("--detail", default=""); p.add_argument("--by", default=None); p.add_argument("--ts", default=None, help="补记历史事件时指定时间(ISO)"); p.add_argument("--limit", type=int, default=20); p.set_defaults(fn=cmd_fleetlog)
    p = sub.add_parser("boot-check"); p.add_argument("--role", default=None); p.add_argument("--sid", default=None, help="核验指定会话(默认=本会话/presence 绑定)"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_boot_check)
    p = sub.add_parser("role-audit"); p.set_defaults(fn=cmd_role_audit)
    p = sub.add_parser("memory-audit"); p.add_argument("--dir", default=None); p.set_defaults(fn=cmd_memory_audit)
    p = sub.add_parser("caps-audit"); p.set_defaults(fn=cmd_caps_audit)
    p = sub.add_parser("fleet-state"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_fleet_state)
    p = sub.add_parser("maint"); p.add_argument("--on", action="store_true"); p.add_argument("--off", action="store_true"); p.add_argument("--note", default=""); p.add_argument("--by", default=None); p.set_defaults(fn=cmd_maint)
    p = sub.add_parser("dispatch"); p.add_argument("--task", required=True); p.add_argument("--from", dest="frm", default=None); p.add_argument("--title", default=""); p.add_argument("--name", default="adhoc"); p.add_argument("--cwd", default=None); p.add_argument("--timeout", type=float, default=3600.0); p.add_argument("--requires-approval", dest="requires_approval", action="store_true", help="高风险标记:dispatch 拒收(无人审直跑面),改走 send+人工领取"); p.set_defaults(fn=cmd_dispatch)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
