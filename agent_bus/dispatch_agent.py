#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dispatch_agent.py — 一次性 ad-hoc agent:执行单条临时任务、有总线沟通能力、干完即逝。

与持久 role daemon(worker_daemon.py)的区别:
  - 不监听队列、不注册 role、不起 loop/serve —— 只做派来的这一件临时任务即退出。
  - 有总线沟通能力(prompt 里教它用 agentbus request/send 跟别的 agent 协作 + 回填)。
  - 失败可重新 dispatch(无状态、可丢弃)。

由 `agentbus dispatch` 以 detached 进程派发;读 BUS/_dispatch/<id>.json,跑 claude -p,
把结果回填到派发方 <from>/done/<id>.json,然后清掉派发记录、退出。
"""
import sys, os, json, subprocess, argparse
from pathlib import Path

# UTF-8 根治(2026-07-10):派发路径原本零 reconfigure → CJK garble。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.environ.setdefault("PYTHONUTF8", "1")   # 传给本进程起的子 python

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentbus as ab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    args = ap.parse_args()

    rec_path = ab.BUS / "_dispatch" / (args.id + ".json")
    try:
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
    except Exception:
        return  # 没记录就退

    frm = rec.get("from", "unknown")
    name = rec.get("name") or "adhoc"
    title = rec.get("title", "")
    task = rec.get("task", "")
    cwd = rec.get("cwd") or ab.cfg("default_worker_cwd", os.getcwd())
    timeout = rec.get("timeout", 3600)
    claude = os.environ.get("CLAUDE_BIN") or ab.cfg("claude_bin", str(Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd"))
    agentbus_py = str(ab.BUS / "agentbus.py")

    prompt = (
        "你是【一次性 ad-hoc agent】(代号:%s)。只完成下面这一件临时任务即结束——"
        "**不监听任何队列、不取别的任务、不起 loop/serve/wait、不把自己当持久 worker**。\n"
        "你有【总线沟通能力】:\n"
        "  - 需要别的 agent 协助 → 运行:python \"%s\" request --to <角色> --from %s --task \"...\"(发子任务并阻塞等应答)\n"
        "  - 要报进度/留言 → python \"%s\" send --to <角色> --from %s --title \"...\" --task \"...\"\n"
        "  - 若要操作 Edge/云文档 → 先 python \"%s\" edge-acquire --who %s 抢锁,操作完 edge-release --who %s(抢不到稍候重试,防串台)。\n"
        "完成后用一段简洁中文回复最终结果/产物路径(你的最终回复会被原样回填给派发方 %s)。\n\n"
        "【临时任务标题】%s\n【内容】\n%s" % (
            name, agentbus_py, name, agentbus_py, name, agentbus_py, name, name, frm, title, task)
    )

    # 流式跑 claude -p,过程实时写 live.log(tail -f 可看),并拿最终结果
    import claude_run
    live = ab.BUS / "_dispatch" / (args.id + ".live.log")
    # per-task effort 覆盖(2026-08-27):rec.effort(dispatch --effort) > name 的 role 声明 > 全局继承
    eff = claude_run.resolve_effort(name, rec.get("effort"))
    mdl = rec.get("model") or claude_run.resolve_model(name)
    # 接续上下文(2026-08-27):rec.continue_from → 状态文件前缀(层A)/ --resume(层C)
    _pref, _resume, _sid = claude_run.resolve_continue(rec.get("continue_from"))
    if _pref:
        prompt = _pref + prompt
    status, result, meta = claude_run.run_streaming(claude, cwd, prompt, str(live), timeout=timeout,
                                                    effort=eff, model=mdl, resume_session=_resume, session_id=_sid)
    ab.record_metric(name, args.id, title, status, meta, kind="dispatch")   # BI 资源消耗埋点
    # 归属账本:ad-hoc 子会话不 hello → sid→name 落账。Master数分 Q1 口径(eeb4e5):代号∈在册 role→归
    # 那个 role;ephemeral 代号(resume-test 等)→标 src=dispatch-adhoc·画像并 _adhoc 桶不单列。
    try:
        from bus.presence import record_session_role
        roster = json.loads((ab.BUS / "agents.json").read_text(encoding="utf-8"))
        record_session_role(name, (meta or {}).get("session_id"),
                            src=None if name in roster else "dispatch-adhoc")
    except Exception:
        pass

    # 回填给派发方(直接写 <from>/done/<id>.json,无需 inbox 任务)
    reply = {"id": args.id, "from": name, "to": frm, "title": title,
             "status": status, "result": result[:4000], "finished": ab._now(), "kind": "adhoc",
             "session_id": meta.get("session_id")}   # 接续用:编排方据此下轮 --resume-session 全上下文接续
    try:
        ab._role_dir(frm)
        ab._write_json(ab.BUS / frm / "done" / (args.id + ".json"), reply)
        ab._audit("dispatch_done", id=args.id, to=frm, status=status, name=name)
    except Exception:
        pass
    # 触发派发方自醒:dispatch 直接写 done 不走 cmd_done,故复用 _emit_completion_notice
    # 给【自醒在岗的协调位】投 notice → 其后台 wait 醒来读 <frm>/done/<id>.json 取本结果。
    ab._emit_completion_notice(frm, name, args.id, status, title)
    # 清掉派发记录(一次性,干完即逝)
    try:
        rec_path.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()
