#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit hook —— 【插话纠偏锚】:用户直接向会话发消息那一刻,若该会话绑定的 bus 角色
手上还有未闭合的 processing 任务,就注入一行锚提醒——防止 agent 被插话带歪、静默弃单。

背景(用户 2026-07-28):前台多 agent 协同模式下,用户会直接向正在执行任务的会话插话;
现状只有事后兜底(route-timeout reap / SPECIALIZATION 纪律 / compact 注入),用户消息落地
那一刻【无机制】。本 hook 补这一刻:确定性注入,不靠 agent 自觉。

机制(官方 hooks.md):UserPromptSubmit 的 stdout(exit 0)会作为 context 注入本轮,
用户与模型都看得到。注入内容 = 手上 processing 任务清单 + 三选一行为锚:
  速答后回任务 / 要紧事先说明再切 / 新话题判 lane 不是自己的就 route。

设计红线:
  · fail-open:任何异常静默 exit 0,绝不阻塞用户消息(与 block_askuser 的 deny 不同)。
  · 只对 bus 成员(session_id 命中 presence)生效;无关会话零输出零打扰。
  · processing 为空 → 静默(agent 空闲时插话没有"被带歪"问题,不注入=零噪音)。
  · UTF-8 强制(Win GBK 遇 CJK 崩 = hook 静默失效,全局铁律)。
"""
import sys, json, os, time
from pathlib import Path

BUS = Path(os.environ.get("AGENTBUS_DIR", str(Path.home() / ".claude" / "agent_bus")))
_MAX_SHOW = 3          # 锚里最多列几条任务(多了自己也是噪音)
_SELF_KINDS = ("notice",)  # notice 类=读完即弃的回执,不算"手上的活",不触发锚


def _role_of(sid):
    """session_id → role(扫 presence;同 turn_state/_resolve_role 口径)。查不到→None=非 bus 成员。"""
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


def _age_min(ts):
    """claimed 的 ISO 时间 → 距今分钟数;解析失败返 None。"""
    try:
        t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        return max(0, int((time.time() - t) / 60))
    except Exception:
        return None


def _processing_tasks(role):
    """读 <role>/processing/ 里未闭合任务 → [(title, from, age_min)];notice 回执不算。"""
    out = []
    try:
        pdir = BUS / role / "processing"
        if not pdir.is_dir():
            return out
        for f in sorted(pdir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if (d.get("kind") or "") in _SELF_KINDS:
                continue
            title = (d.get("title") or d.get("task") or f.stem)[:60]
            out.append((title, d.get("from") or "?", _age_min(d.get("claimed") or "")))
    except Exception:
        pass
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return
    if (data.get("hook_event_name") or "UserPromptSubmit") != "UserPromptSubmit":
        return
    # 盖 user_last_seen(用户缺席检测信号①·2026-08-28):任一 session 用户发消息=用户在场。
    # 直接写(不 import bus·hook 轻量鲁棒);格式对齐 user_checkpoint.touch_user_seen。写 agent_fleet 物理家(junction 同解)。
    try:
        import time as _t
        _bus = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent_bus")
        with open(os.path.join(_bus, "user_last_seen.json"), "w", encoding="utf-8") as _f:
            json.dump({"ts": _t.strftime("%Y-%m-%dT%H:%M:%S"), "epoch": _t.time(),
                       "source": "prompt", "session": data.get("session_id", "")}, _f, ensure_ascii=False)
    except Exception:
        pass
    role = _role_of(data.get("session_id"))
    if not role:
        return
    tasks = _processing_tasks(role)
    if not tasks:
        return
    lines = []
    for title, frm, age in tasks[:_MAX_SHOW]:
        lines.append("「%s」(from %s%s)" % (title, frm, "·claimed %d分钟" % age if age is not None else ""))
    more = "(另 %d 条)" % (len(tasks) - _MAX_SHOW) if len(tasks) > _MAX_SHOW else ""
    print(
        "⚓ 插话纠偏锚(自动注入·你手上有 %d 条未闭合 processing 任务):%s%s。"
        "处理本条用户消息时:①能速答就速答,答完立刻回手头任务闭环(done --role %s);"
        "②用户要求更紧急→先一句话说明手头任务状态再切,别静默弃单;"
        "③新话题先判 lane,不是你的职责就 route 给对的角色,别自揽。"
        % (len(tasks), ";".join(lines), more, role)
    )


if __name__ == "__main__":
    main()
