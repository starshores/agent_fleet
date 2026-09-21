#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.common —— WebUI 共享基础层(BUS 2.0 P4 · Phase A · 2026-07-20)。

抽自 server.py:agentbus 引导(ABDIR/ab/BUS · 单一源)+ 无状态基础 helper,
供 server.py / (后续) ui.api / ui.routes / ui.page 复用。
server.py re-export 这些名字保【对外兼容】——含子进程 `import server; server.compute_fleet()` 路径
(该子进程把 ui/ 目录塞进 sys.path 后 import server,server 再 from common import,故 common 亦可达)。

契约铁律(P4):纯内部结构重组,端点响应 + 面板显示 + 口径全不变。本文件只搬运,不改逻辑。
"""
import json
import os
import sys
from pathlib import Path

# ---- 复用 agentbus.py:它是总线的真相源 ----
# ABDIR 必须在 import agentbus 之前就绪,故不能用 ab.cfg;改为 env AGENTBUS_DIR 或 ~/.claude/agent_bus
# (与 agentbus.BUS 默认一致),不再硬编码用户名路径,换机/换用户即可用。
ABDIR = Path(os.path.expandvars(os.path.expanduser(os.environ.get("AGENTBUS_DIR") or str(Path.home() / ".claude" / "agent_bus"))))
if str(ABDIR) not in sys.path:
    sys.path.insert(0, str(ABDIR))
import agentbus as ab  # noqa: E402

BUS = ab.BUS


# =============================================================
#  基础 helper —— 全部复用 agentbus 的 helper,不复制其逻辑
# =============================================================

def _roles():
    """真正的 role 目录 —— 直接复用 agentbus._list_roles()(真相源)。
    它已排除 __pycache__ 及以 . 或 _ 开头的非 role 目录,UI 不再自己判定。"""
    return ab._list_roles()


def _count(role, sub):
    d = BUS / role / sub
    return len(list(d.glob("*.json"))) if d.exists() else 0


def _read_tasks(role, subs=("inbox", "processing", "held", "failed")):
    """读某 role 的待办/待批/毒任务(完整 JSON,供前端点开看)。
    带出 requires_approval 字段:inbox 里 true 的是高风险任务,自动监听者不领,需人工 wait --include-approval。"""
    out = []
    for sub in subs:
        d = BUS / role / sub
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                o = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                o = {"id": p.stem, "title": "(无法解析)", "task": ""}
            out.append({
                "sub": sub,
                "id": o.get("id", p.stem),
                "from": o.get("from", "?"),
                "to": o.get("to", role),
                "title": o.get("title", ""),
                "task": o.get("task", ""),
                "status": o.get("status", ""),
                "created": o.get("created", ""),
                "claimed": o.get("claimed", ""),
                "requires_approval": bool(o.get("requires_approval")),
                "requeue_count": o.get("requeue_count", 0),
            })
    return out


def _safe_cwd(pid):
    """探进程 cwd（供如实显示未标记/非private 进程）。探不到回 None，绝不抛。"""
    try:
        return ab.psutil.Process(pid).cwd()
    except Exception:
        return None


def _turn_state(role):
    """读 <role>/turn_state.json(turn_state.py hook 盖的实时 turn 状态)。返回 {state,label,tool,age} 或 None。
    真实性:数据只来自 hook 事件(UserPromptSubmit→thinking / Pre·PostToolUse→tool·thinking / Stop→idle),
    非推断;age=now−ts(秒)供前端显示时长 + 判陈旧(如 thinking 太久=可能漏了 Stop)。"""
    p = BUS / role / "turn_state.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {"state": d.get("state"), "label": d.get("label"),
            "tool": d.get("tool"), "event": d.get("event"),
            "age": ab._age_seconds(d.get("ts", ""))}


def _vclass_of(rec):
    """从 _who_data 的 rec 派生【显示类】vclass(badge 颜色)—— 纯 UI 派生,非 verdict 判定。
    复刻旧 compute_fleet 的 verdict→vclass 映射,保 tray/count 口径不漂(使用方 role-verdict 收敛铁律)。
    tray 计数据此:live/idle/warn=在岗·parked=休眠·dead/none=不计在岗。"""
    if rec.get("pseudo") or rec.get("retired"):
        return "none"          # 伪角色/退役:不判在线掉线,不计在岗
    if rec.get("parked"):
        return "parked"
    v = rec.get("verdict") or ""
    if "干活中" in v:
        return "live"          # 🔧干活中(turn活跃·长任务心跳晚=正常·MasterAgent令2026-09-21)=忙碌/正常类非异常
    if "损坏" in v:
        return "dead"          # presence 损坏
    if v.startswith("未声明"):
        return "none"
    if v.startswith("✓"):
        return "live"          # ✓worker在听 / ✓daemon在听
    if v.startswith("✗"):
        return "dead"          # ✗进程已退(残留) / ✗daemon已退
    if "静默" in v:
        return "warn"          # ⚠worker静默(超心跳)
    if v.startswith("已声明"):
        return "idle"          # 已声明(hello),未起循环
    if "(无pid)" in v or v == "daemon?":
        return "warn"
    return "idle"              # daemon(心跳)/心跳前(mode=) 等 psutil-off 兜底
