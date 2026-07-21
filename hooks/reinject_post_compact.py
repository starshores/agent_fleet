#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SessionStart(matcher=compact) hook —— compact 后【自定位·重认身份+重载纪律】。
背景:compact 会退化【身份+操作纪律】;本 cwd 多 role 自举、只凭 cwd 会认错人。
机制:hooks 机制中 PreCompact/PostCompact 不能注入上下文,
唯一有效杠杆 = SessionStart 且 source="compact",经 stdout `hookSpecificOutput.additionalContext` 注入压后 context。
本脚本:① 反查本 session 真实 role(stdin.session_id → presence.session_id;fallback env)②注入 正确身份 + 本角色操作铁律 + 验 selfwake 提醒 ③反查不到 或 自醒掉线 → WinRT toast 提醒用户。
只读、不写 presence/不改队列。失败静默(exit 0,不阻断会话)。"""
import sys, json, os
from pathlib import Path

# 强制 stdout UTF-8:Claude Code 管道读 hook stdout,Windows 默认 GBK 会让 CJK/符号编码崩→空输出→hook失效。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BUS = Path(os.environ.get("AGENTBUS_DIR") or (Path.home() / ".claude" / "agent_bus"))

# 通用操作铁律(所有 bus role 都适用;深版在 AGENTS.md / SPECIALIZATION.md / 各角色记忆锚)
_COMMON_RULES = (
    "done 回填必带 --role {role};"
    "自醒 wait 只用【后台 run_in_background】重挂、绝不用 shell `&`、上一个没 completed 前别重复挂(俩 wait 抢同一 notice 竞态);"
    "被唤起若 inbox 无新活→静默重挂+最多一行、绝不重 dump selfwake/who/状态(自灌上下文=加速 compact 降智);"
    "notice 读它 notice_of 指向的 done/<id>.json 取结果后 done 掉即弃;"
    "被 wait 领进 processing 的消息必须当轮 done 闭合;"
    "需用户拍板/二选一→走 `ab ask-user --from {role} ...` 投非阻塞待办池、禁用 AskUserQuestion(已被 block_askuser.py 硬拦),投完继续干活别等。"
)
# 角色特有铁律:从 role_profiles.json(canonical 单一源·无镜像)读,不再硬编码 dict
# (可扩展=加角色只改注册表;灭 dict 分叉;顺带修旧名 stale 问题)。
def _role_profile(role):
    """取本 role 的 one_liner + boundary(compact 该留的身份+边界/反面锚/记忆slug)。
    读法:Path(__file__).parent.parent/role_profiles.json、显式 utf-8。
    文件缺失 / role 未注册 / json 解析失败 → 返回 None(调用方回落 generic)。
    绝不抛异常(hook 恒 exit 0,不因注册表崩掉自定位)。"""
    try:
        pf = Path(__file__).resolve().parent.parent / "role_profiles.json"
        data = json.loads(pf.read_text(encoding="utf-8"))
        for r in data.get("roles", []):
            if r.get("role") == role:
                ol = (r.get("one_liner") or "").strip()
                bd = (r.get("boundary") or "").strip()
                if ol or bd:
                    return ol, bd
    except Exception:
        pass
    return None

def _resolve_role(sid):
    """按 session_id 反查绑定 role。stdin.session_id(hook 权威提供)为准;仅其缺失才用 env 兜底
    (别默认拿 env 当候选:hook 环境的 env 可能是别的/父会话,会误匹配)。"""
    cand = [sid] if sid else []
    if not cand:
        ev = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if ev:
            cand = [ev]
    if not cand or not BUS.exists():
        return None
    for r in sorted(p.name for p in BUS.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        pf = BUS / r / "presence.json"
        try:
            if pf.exists():
                psid = json.loads(pf.read_text(encoding="utf-8")).get("session_id")
                if psid and psid in cand:
                    return r
        except Exception:
            pass
    return None

def _toast(title, body):
    """WinRT toast 提醒(hook 不能调 PushNotification 工具,用已注册 AppID 的 toast)。失败静默。"""
    try:
        import subprocess
        ps = (
            '$AppID="ClaudeCode.NotifyTest";'
            '[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;'
            '[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;'
            '$x=New-Object Windows.Data.Xml.Dom.XmlDocument;'
            '$x.LoadXml(\'<toast><visual><binding template="ToastGeneric"><text>%s</text><text>%s</text></binding></visual></toast>\');'
            '$t=[Windows.UI.Notifications.ToastNotification]::new($x);'
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppID).Show($t)'
        ) % (title, body)
        subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                       capture_output=True, timeout=8)
    except Exception:
        pass

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("source") != "compact":   # 只在 compact 后注入(matcher 已过滤,双保险)
        return
    sid = data.get("session_id")
    role = _resolve_role(sid)
    if role:
        prof = _role_profile(role)
        if prof:
            ol, bd = prof
            rule = "(role_profiles.json·单一源)%s。边界:%s" % (ol, bd)
        else:
            rule = "读你的角色恢复锚记忆(在 MEMORY.md 索引里搜你的 role)。"
        ctx = (
            "## ⚡ compact 后自定位(SessionStart·source=compact 自动注入)\n"
            "**你是【%s】。** compact 只压了对话、没动你的 presence 绑定——【不必重 hello】,但必须重认身份+重载下面纪律,别退化。\n\n"
            "**你的操作铁律**:%s\n\n"
            "**本角色特有**:%s\n\n"
            "**立即做**:① 跑 `ab selfwake` 看【%s】是否 ✓在岗;没在岗就【后台 run_in_background】挂 `ab wait --role %s --timeout 0`(0=自动日2h/夜6h) 再 `ab selfwake` 验。② 继续被 compact 打断前的活(工作态见你最近的 done/记忆)。\n"
            "**深恢复**:cwd=C:\\apphome\\agent_fleet 的读 CLAUDE.md 恢复入口;先 `whoami` 已确认身份=%s。"
        ) % (role, _COMMON_RULES.format(role=role), rule, role, role, role)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}, ensure_ascii=False))
    else:
        # 反查不到 role:注入告警 + toast 提醒用户
        ctx = (
            "## ⚠ compact 后自定位失败\n"
            "无法按 session_id 反查到你绑定的 bus role(可能 presence 丢失/未 hello 过)。\n"
            "**别默认自己是 MasterAgent**——先跑 `ab whoami` 确认;仍空则按 CLAUDE.md 恢复入口判定真实身份再干活。"
        )
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}, ensure_ascii=False))
        _toast("Claude Code · compact 后身份反查失败", "有 tab compact 后 whoami 查不到 role,请确认其身份/presence")

if __name__ == "__main__":
    main()
