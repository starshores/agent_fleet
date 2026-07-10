#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生命周期 hook —— 盖每个 bus 成员会话的实时 turn 状态到 <role>/turn_state.json,供运维面板显示
「是否在思考/执行中 vs 空闲」。

真实依据(2026-07-10 claude-code-guide 核实官方 hooks-guide.md / hooks.md / sessions.md):
  UserPromptSubmit = turn 开始;PreToolUse = 将跑工具(带 tool_name);PostToolUse = 工具完回推理;
  Stop = turn 结束(model 答完)。**纯推理(thinking)无独立 hook** → UserPromptSubmit..Stop 之间
  即"在 turn 内(思考/执行中)"= 可观测的真实代理。payload 保证字段:session_id / hook_event_name /
  cwd / transcript_path;工具事件另带 tool_name。官方明确别解析 transcript jsonl(内部格式会变)。

设计:
  · 写【独立小文件】<role>/turn_state.json,**不碰 presence.json**(零风险动身份,避免与 _touch_presence 竞态丢字段)。
  · fire-and-forget:恒 exit 0、无 stdout → 放行,绝不 deny/阻塞工具或 turn(与 block_askuser 的 deny 不同)。
    Stop hook 只盖状态不 block → 无「连续 block 被覆盖」问题。
  · 仅 bus 成员(session_id 命中某 presence)才写;非成员(无关项目会话)静默跳过——与 bus 收窄一致。
  · UTF-8 强制(Win GBK 遇 CJK 崩)。任何异常都吞掉、照常放行(fail-open)。
"""
import sys, json, os, time
from pathlib import Path

BUS = Path(os.environ.get("AGENTBUS_DIR", str(Path.home() / ".claude" / "agent_bus")))

# hook 事件 → (state, 中文标签模板)。thinking 含"turn 开始/工具间隙推理";tool=正跑某工具;idle=turn 结束/等输入。
# 依据(claude-code-guide 2 次核实官方 hooks-guide.md/hooks.md):
#   UserPromptSubmit=turn 开始;PreToolUse=将跑工具(带 tool_name);PostToolUse=工具完回推理(async 免延迟);
#   Stop=turn 正常结束(⚠ 用户中断/取消【不】触发任何 hook → 靠运维面板 ts-陈旧兜底补);
#   Notification+matcher idle_prompt=Claude 答完等下一条输入 → idle。
_EVENT_STATE = {
    "UserPromptSubmit": ("thinking", "思考/执行中"),
    "PreToolUse":       ("tool",     "跑工具"),
    "PostToolUse":      ("thinking", "思考/执行中"),
    "Stop":             ("idle",     "空闲"),
    "Notification":     ("idle",     "空闲·等输入"),
}


def _role_of(sid):
    """session_id → role(扫 presence,同 block_askuser/_resolve_role 口径)。查不到→None=非 bus 成员。"""
    if not sid:
        return None
    try:
        for pf in BUS.glob("*/presence.json"):
            try:
                d = json.loads(pf.read_text(encoding="utf-8"))
                if d.get("session_id") == sid:
                    return d.get("role") or pf.parent.name
            except Exception:
                continue
    except Exception:
        pass
    return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return  # 读不到 → fail-open
    ev = data.get("hook_event_name") or ""
    if ev not in _EVENT_STATE:
        return
    if ev == "Notification":
        # 只有"空闲等输入/答完"类通知才算 idle;权限弹窗等其它通知不改 turn 状态(即便 matcher 已过滤,再兜一层)
        nt = data.get("notification_type") or ""
        if nt and nt not in ("idle_prompt", "agent_completed"):
            return
    role = _role_of(data.get("session_id"))
    if not role:
        return  # 非 bus 成员 → 不写(与 bus 收窄一致,也省得给无关项目留状态文件)
    state, label = _EVENT_STATE[ev]
    tool = (data.get("tool_name") or "") if ev in ("PreToolUse", "PostToolUse") else ""
    if state == "tool" and tool:
        label = "跑工具:" + tool
    rec = {"state": state, "label": label, "tool": tool, "event": ev,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())}
    try:
        p = BUS / role / "turn_state.json"
        tmp = p.parent / ("turn_state.%d.tmp" % os.getpid())   # 唯一 tmp 名,防并行工具 hook 竞态
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    # 恒不输出、恒正常退出 → 放行,绝不阻塞工具/turn
