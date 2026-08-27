# -*- coding: utf-8 -*-
"""bus.exec —— 派遣一次性 ad-hoc agent（dispatch）。

dispatch 立刻 detached 起一个一次性执行体（claude -p 经 dispatch_agent.py），跑完把结果回填 <from>/done/<id>（replies 收）。
与 send（投队列等 role 领）不同：不监听队列、干完即逝、失败再派。

依赖：config(BUS/cfg/_now)· io(_gen_id/_write_json/_audit)· subprocess/sys。
dispatch_agent.py = 本包父目录 agent_bus/ 下的兄弟脚本（Path(__file__).parent.parent）。
"""
import json, os, subprocess, sys
from pathlib import Path

from bus.config import BUS, cfg, _now
from bus.io import _gen_id, _write_json, _audit, _fail


def cmd_dispatch(args):
    """派一个【一次性 ad-hoc agent】去做单条临时任务:有总线沟通能力、不监听队列、干完即逝、可重新派。
    与 send(投进某 role 的队列等它领)不同 —— dispatch 立刻 detached 起一个一次性执行体(claude -p),
    跑完把结果回填到派发方 <from>/done/<id>(用 `replies --role <from>` 收)。失败就再 dispatch 一个。"""
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    # P1 审批闸(2026-07-28):dispatch 起的一次性 agent 以 --dangerously-skip-permissions 直跑、
    # 无人审——高风险任务不得走这条快跑面(send/wait 侧有 requires_approval 闸,dispatch 原是空洞)。
    if getattr(args, "requires_approval", False):
        _fail("requires_approval 高风险任务不走 dispatch(一次性 agent 无人审直跑):"
              "请改 `send --to <role> --requires-approval`,由人工值守 tab "
              "`wait --include-approval` 领取执行。")
    tid = _gen_id("adhoc")
    cwd = args.cwd or cfg("default_worker_cwd")
    rec = {
        "id": tid, "from": frm, "name": args.name or "adhoc",
        "title": args.title or "", "task": args.task, "cwd": cwd,
        "timeout": args.timeout, "created": _now(), "kind": "adhoc",
        # per-task effort/model 覆盖(2026-08-27):非空则单次拉满(如硬诊断 --effort max),平时吃 role/全局
        "effort": getattr(args, "effort", None), "model": getattr(args, "model", None),
    }
    dd = BUS / "_dispatch"
    dd.mkdir(parents=True, exist_ok=True)
    _write_json(dd / (tid + ".json"), rec)
    _audit("dispatch", id=tid, frm=frm, name=rec["name"], title=rec["title"])
    da = str(Path(__file__).parent.parent / "dispatch_agent.py")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen([sys.executable, da, "--id", tid], creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(BUS))
    except Exception as e:
        _fail("dispatch 起一次性 agent 失败: %s" % e)
    print(tid)
