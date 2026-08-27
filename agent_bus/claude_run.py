#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""claude_run.py — 跑 claude -p 并把过程【流式】写进 live.log(供 tail -f / WebUI 只读看)。

headless daemon / dispatch 用它替代一次性 capture,这样虽然不能 attach,过程也实时可见。
用 `--output-format stream-json --verbose` 拿事件流,解析成可读行(💬文本 / 🔧工具),
同时抓最终 result 返回给调用方。解析不出就回退原样写 + 纯捕获结果。
worker_daemon.py 和 dispatch_agent.py 共用。
"""
import subprocess, json, time, threading, sys, os

# UTF-8 根治(2026-07-10):被 daemon/dispatch 复用;独立跑或父未修时兜住自身 stdout/stderr。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.environ.setdefault("PYTHONUTF8", "1")

# 起 claude -p 不弹控制台窗口(claude_bin 多是 .cmd,无此标志会弹窗;daemon/dispatch 跑任务时尤其扰人)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def resolve_effort(role, task_effort=None, agents_path=None):
    """解析某 role 本次 claude -p 的 effort(推理档位)。
    优先级(镜像 per-role 模型解析·2026-08-27 用户令):
      task_effort(per-task/dispatch 覆盖) > agents.json[role].effort(role 级声明) > None(继承全局 settings effortLevel)。
    返回 effort 字符串(low/medium/high/xhigh/max)或 None(=不加 --effort,claude 吃全局)。"""
    if task_effort:
        return task_effort
    try:
        if agents_path is None:
            agents_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents.json")
        d = json.load(open(agents_path, encoding="utf-8"))
        ags = d.get("agents", d) if isinstance(d, dict) else {}
        rec = ags.get(role) if isinstance(ags, dict) else None
        if isinstance(rec, dict) and rec.get("effort"):
            return rec["effort"]
    except Exception:
        pass
    return None


def resolve_model(role, agents_path=None):
    """解析某 role 的 model 声明(agents.json[role].model);None=继承全局 settings model。
    (顺带接线:此前 agents.json 的 model 字段声明了但无代码读取=空转;现与 effort 同处解析。)"""
    try:
        if agents_path is None:
            agents_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents.json")
        d = json.load(open(agents_path, encoding="utf-8"))
        ags = d.get("agents", d) if isinstance(d, dict) else {}
        rec = ags.get(role) if isinstance(ags, dict) else None
        if isinstance(rec, dict) and rec.get("model"):
            return rec["model"]
    except Exception:
        pass
    return None


def resolve_continue(continue_from):
    """解析任务的接续上下文指令(daemon 跨任务不进程内背上下文·外置接续·2026-08-27 Master数分口径/MasterAgent机制)。
    continue_from = None | {state_file?, resume_session?, session_id?}
      · state_file(层A·默认·省+稳):约定路径 agent_bus/_workstate/<thread>.json,存前序任务【提炼工作态】
        (summary/next/artifacts);存在则返回 prompt 前缀让本任务先读它接续,不进程内背全上下文。
      · resume_session(层C·贵·仅A不足时):claude -p --resume <session> 全上下文接续。
      · session_id:启动即指定会话 id(便于后续 --resume 引用)。
    返回 (prompt_prefix, resume_session, session_id)。"""
    if not continue_from or not isinstance(continue_from, dict):
        return "", None, None
    prefix = ""
    sf = continue_from.get("state_file")
    if sf:
        prefix = ("【接续工作态·最先做】先读取状态文件恢复上文,再执行本任务:%s\n"
                  "(该文件=前序任务落的提炼工作态:已完成/关键发现/下一步/产物路径;读完顺着它接着做,别从零重来。)\n\n" % sf)
    return prefix, continue_from.get("resume_session"), continue_from.get("session_id")


def run_streaming(claude_bin, cwd, prompt, live_log, timeout=3600, effort=None, model=None,
                  resume_session=None, session_id=None):
    """返回 (status, result_text, meta)。过程实时写 live_log。
    meta = {tokens_in, tokens_out, ..., session_id}(session_id 从 stream-json init 事件抓,供接续 --resume)。
    effort/model:非空则注入 --effort/--model(per-role/per-task);None=继承全局 settings。
    接续上下文(2026-08-27·daemon 跨任务不进程内背上下文·外置接续 层C):
      resume_session:非空 → --resume <session>(全上下文接续·贵·仅提炼状态不够时用);
      session_id:非空且无 resume → --session-id <uuid>(启动即指定会话 id·便于后续 --resume)。"""
    meta = {}
    cmd = [claude_bin, "-p", "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", str(model)]
    if effort:
        cmd += ["--effort", str(effort)]
    if resume_session:
        cmd += ["--resume", str(resume_session)]
    elif session_id:
        cmd += ["--session-id", str(session_id)]
    try:
        lf = open(live_log, "w", encoding="utf-8", errors="replace")
    except Exception:
        lf = None

    def w(s):
        if lf:
            try:
                lf.write(s + "\n"); lf.flush()
            except Exception:
                pass

    w("[%s] ▶ 启动 claude -p (cwd=%s)" % (_now(), cwd))
    final, is_err, got_result = None, False, False
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                             creationflags=_NO_WINDOW)
        killed = {"v": False}

        def killer():
            killed["v"] = True
            # 杀【整棵进程树】(P0·2026-07-28):claude.cmd 是 cmd 宿主壳,p.kill 只杀壳,
            # 底下 node 孤儿继续跑=烧 token,且其握着 stdout 管道写端 → 上面的 for line 循环
            # 永不 EOF=daemon 卡死。taskkill /T /F 连子进程一起收;失败再退回 p.kill 兜底。
            try:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                               capture_output=True, timeout=15, creationflags=_NO_WINDOW)
            except Exception:
                pass
            try:
                p.kill()   # 兜底(taskkill 不在/失败;树杀成功时这里已无进程,静默无害)
            except Exception:
                pass

        timer = threading.Timer(timeout, killer)
        timer.start()
        try:
            p.stdin.write(prompt); p.stdin.close()
        except Exception:
            pass
        for line in p.stdout:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                w(line)  # 非 JSON,原样写
                continue
            if ev.get("session_id") and not meta.get("session_id"):
                meta["session_id"] = ev["session_id"]   # init/system 事件带·供后续接续 --resume
            t = ev.get("type")
            if t == "assistant":
                for b in (ev.get("message", {}).get("content") or []):
                    bt = b.get("type")
                    if bt == "text" and (b.get("text") or "").strip():
                        w("💬 " + b["text"].strip())
                    elif bt == "tool_use":
                        w("🔧 %s %s" % (b.get("name", "?"),
                                       json.dumps(b.get("input", {}), ensure_ascii=False)[:140]))
            elif t == "result":
                got_result = True
                final = ev.get("result")
                is_err = bool(ev.get("is_error"))
                u = ev.get("usage") or {}
                meta.update({          # update 而非重新赋值 —— 保留前面已捕获的 session_id(接续 --resume 用)
                    "tokens_in": u.get("input_tokens", 0) or 0,
                    "tokens_out": u.get("output_tokens", 0) or 0,
                    "cache_read": u.get("cache_read_input_tokens", 0) or 0,
                    "cache_creation": u.get("cache_creation_input_tokens", 0) or 0,
                    "cost_usd": ev.get("total_cost_usd", 0) or 0,
                    "duration_ms": ev.get("duration_ms", 0) or 0,
                    "num_turns": ev.get("num_turns", 0) or 0,
                })
                w("[%s] ■ 完成 is_error=%s tok=%s/%s $%.4f" % (
                    _now(), is_err, meta["tokens_in"], meta["tokens_out"], meta["cost_usd"]))
        p.wait()
        timer.cancel()
        if killed["v"]:
            return "error", "claude -p 超时(>%ss),已 kill" % timeout, meta
        if got_result:
            return ("error" if is_err else "ok"), ((final or "(无输出)")[:4000]), meta
        return ("ok" if p.returncode == 0 else "error"), "(无 result 事件,returncode=%s)" % p.returncode, meta
    except Exception as e:
        return "error", "拉起 claude -p 失败:%s" % e, meta
    finally:
        if lf:
            try:
                lf.close()
            except Exception:
                pass
