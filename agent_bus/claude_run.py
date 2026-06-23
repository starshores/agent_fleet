#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""claude_run.py — 跑 claude -p 并把过程【流式】写进 live.log(供 tail -f / WebUI 只读看)。

headless daemon / dispatch 用它替代一次性 capture,这样虽然不能 attach,过程也实时可见。
用 `--output-format stream-json --verbose` 拿事件流,解析成可读行(💬文本 / 🔧工具),
同时抓最终 result 返回给调用方。解析不出就回退原样写 + 纯捕获结果。
worker_daemon.py 和 dispatch_agent.py 共用。
"""
import subprocess, json, time, threading


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def run_streaming(claude_bin, cwd, prompt, live_log, timeout=3600):
    """返回 (status, result_text, meta)。过程实时写 live_log。
    meta = {tokens_in, tokens_out, cache_read, cache_creation, cost_usd, duration_ms, num_turns}
    (从 stream-json 的 result 事件抓,供 BI 资源消耗统计;抓不到则为空 dict。)"""
    meta = {}
    cmd = [claude_bin, "-p", "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions"]
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
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        killed = {"v": False}

        def killer():
            killed["v"] = True
            try:
                p.kill()
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
                meta = {
                    "tokens_in": u.get("input_tokens", 0) or 0,
                    "tokens_out": u.get("output_tokens", 0) or 0,
                    "cache_read": u.get("cache_read_input_tokens", 0) or 0,
                    "cache_creation": u.get("cache_creation_input_tokens", 0) or 0,
                    "cost_usd": ev.get("total_cost_usd", 0) or 0,
                    "duration_ms": ev.get("duration_ms", 0) or 0,
                    "num_turns": ev.get("num_turns", 0) or 0,
                }
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
