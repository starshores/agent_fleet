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
from bus.identity import (  # noqa: E402
    cmd_register, cmd_tag, cmd_hello, cmd_whoami, cmd_selfwake, cmd_selfwake_guard, cmd_selfwake_exempt,
)



# BUS 2.0 命令分组(对齐 bus/ 一体化模块;供 `ab`/`ab commands`/无参时分组呈现·治 43 命令扁平难发现)
_CMD_GROUPS = [
    ("消息任务流 core", "send wait serve request done peek replies answer"),
    ("身份·自醒 presence/selfwake", "hello whoami selfwake selfwake-guard selfwake-exempt"),
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
    p = sub.add_parser("selfwake-exempt"); p.add_argument("--role", default=None); p.add_argument("--off", action="store_true"); p.add_argument("--mode", default=None); p.add_argument("--reason", default=None); p.add_argument("--note", default=None); p.set_defaults(fn=cmd_selfwake_exempt)
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
