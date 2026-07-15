#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse hook —— 拦截 AskUserQuestion(阻塞式交互卡片),自动转投【非阻塞待办池】。

起因(用户 2026-07-06 explicit):AskUserQuestion 是阻塞式选项卡片,问了若用户没及时点,
发问 agent 的会话就被【占住干等】。用户一般没时间实时看 cc 询问 → 决策一律改走非阻塞待办池
(todos.json / WebUI),用户按自己节奏批量看、回填。契合全局 CLAUDE.md「禁用模态对话框/用事件
通知不阻塞」+ 记忆 ask-user-standard。仅靠约定失效过(有 agent 仍用卡片),故机制层硬拦。

行为:匹配到 AskUserQuestion → ①解析 questions 自动投进 todos.json(每问一条,选项写进 detail)
→ ②deny 该工具 + 在理由里告诉 agent「已代你投入待办池 N 条,继续你的活别等、下轮再看回填」。
fail-open:读不到输入 / 非目标工具 → 无输出 = 正常放行,绝不误伤别的工具。
UTF-8 强制(Win GBK 遇 CJK 会崩,全局铁律)。
"""
import sys, json, os, time
from pathlib import Path

BUS = Path(os.environ.get("AGENTBUS_DIR", str(Path.home() / ".claude" / "agent_bus")))


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _resolve_role(sid):
    """按 session_id 反查发问者 role(同 reinject_post_compact 的 presence 扫描)。查不到→None。"""
    if not sid:
        return None
    try:
        for pf in BUS.glob("*/presence.json"):
            try:
                d = json.loads(pf.read_text(encoding="utf-8"))
                if d.get("session_id") == sid:
                    return d.get("role")
            except Exception:
                continue
    except Exception:
        pass
    return None


def _invest(questions, frm):
    """把 AskUserQuestion 的每个问题投进 todos.json(结构对齐 cmd_ask_user)。返回投入条数。"""
    f = BUS / "todos.json"
    try:
        items = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    n = 0
    for q in questions:
        title = (q.get("question") or q.get("header") or "").strip()
        if not title:
            continue
        opts = q.get("options") or []
        detail = ""
        if opts:
            detail = "选项:" + " / ".join(
                "%s=%s" % (o.get("label", ""), (o.get("description", "") or "").strip()) for o in opts)
        tid = "t-%d-%s" % (int(time.time() * 1000), os.urandom(2).hex())
        items.append({
            "id": tid,
            "title": title,
            "detail": detail,
            "source": "确认·" + (frm or "unknown") + "(AskUserQuestion自动转·非阻塞)",
            "priority": "中",
            "status": "待办",
            "deadline": "",
            "created": _now(), "updated": _now(),
            "task_id": None, "dispatched_to": None,
            "confirm_from": frm or "unknown", "ref": None,
        })
        n += 1
    if n:
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, f)
    return n


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return  # 读不到 → fail-open 放行
    if data.get("tool_name") != "AskUserQuestion":
        return  # 非目标工具 → 无输出 = 正常放行

    frm = _resolve_role(data.get("session_id"))
    if not frm:
        # 门禁(2026-07-10 用户):本 session 在 bus 无 presence 绑定 = 非 bus 成员(如无关的
        # 编码项目会话)。绝不拦它、绝不投 bus —— fail-open 放行,让它的 AskUserQuestion 卡片
        # 正常可用。只有 bus 注册成员(hello 过、presence 里有本 session_id)才转投待办池。
        return
    try:
        questions = (data.get("tool_input", {}) or {}).get("questions", []) or []
    except Exception:
        questions = []
    try:
        n = _invest(questions, frm)
    except Exception:
        n = 0

    if n:
        reason = (
            "AskUserQuestion 是【阻塞式交互卡片】——问了若用户没及时点,你的会话就被占住干等"
            "(用户 2026-07-06 explicit 令优化掉:一般没时间实时看 cc 询问)。已【自动代你把 %d 个问题"
            "投入非阻塞待办池 todos.json】(WebUI 待办池可见),无需你重复投。现在请【继续你手头的活、"
            "别等用户】——用户按自己节奏答复后回填,你下轮再看结果。以后要用户拍板一律直接跑 "
            "`ab ask-user --from <你的role> "
            "--title \"<问题>\" --detail \"<背景+选项>\"`(非阻塞),别再用 AskUserQuestion。" % n
        )
    else:
        reason = (
            "AskUserQuestion 已被基建禁用(阻塞会话·用户 2026-07-06 令优化掉)。请改用非阻塞待办池:"
            "`ab ask-user --from <你的role> "
            "--title \"<问题>\" --detail \"<背景+选项如 A=… / B=…>\"`,投完【继续干活别等】,"
            "用户按节奏在 WebUI 待办池回填。"
        )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
