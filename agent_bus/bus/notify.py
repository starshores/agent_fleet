# -*- coding: utf-8 -*-
"""bus.notify —— 完成通知:WinRT toast 分级弹窗 + kind:notice 触发协调位事件自醒。

"通知 Master"=给在场的人弹一条 OS 事件 toast(契合全局规则:完成走事件通知不轮询),
让人去碰 Master / 开 WebUI。toast 由 done 端发(完成时刻,tab/daemon 都会跑),0 token、非阻塞。
(PushNotification 是会话内建工具,done 子进程调不到 → 用 WinRT 模板 AppID ClaudeCode.NotifyTest。)
依赖:config(BUS/_now)· io(_write_json/_audit)· roster(HUMAN_ATTENDED)· selfwake(_self_wake_state)。
"""
import subprocess, time

from bus.config import BUS, _now
from bus.io import _write_json, _audit
from bus.roster import HUMAN_ATTENDED
from bus.selfwake import _self_wake_state


def _toast_throttled(min_gap=45):
    """节流:距上次 toast 不足 min_gap 秒就跳过(防 ok 完成刷屏)。状态存 BUS/.last_toast。"""
    f = BUS / ".last_toast"
    now = time.time()
    try:
        last = float(f.read_text(encoding="utf-8").strip())
    except Exception:
        last = 0.0
    if now - last < min_gap:
        return False
    try:
        f.write_text(str(now), encoding="utf-8")
    except Exception:
        pass
    return True


def _fire_toast(head, body):
    """真正弹一条 WinRT toast。绝不抛错、绝不阻塞(Popen 即走,不 wait)。"""
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("'", "’").replace('"', "”"))
    xml = ('<toast><visual><binding template="ToastGeneric">'
           "<text>" + esc(head) + "</text><text>" + esc(body) + "</text>"
           "</binding></visual></toast>")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
          "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
          "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
          "$x.LoadXml('" + xml + "');"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('ClaudeCode.NotifyTest')"
          ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def _maybe_toast(sender, status, title, result, notify, reply_mode=None):
    """完成通知分级(默认 auto):
      never → 不发;always → 发;
      auto  → error 必发(不节流);ok 仅当 sender 是人值守角色且过节流;
      reply_mode=sync(request 同步应答)→ 一律不发(调用方已同步拿到)。"""
    if notify == "never" or reply_mode == "sync":
        return
    if notify == "auto":
        if status == "ok":
            if sender not in HUMAN_ATTENDED:
                return
            if not _toast_throttled():
                return
        # status=error → 必发,不节流
    head = ("✅ 任务完成" if status == "ok" else "❌ 任务失败") + (" · " + title if title else "")
    body = (sender + " ← " + (result or ""))[:140]
    _fire_toast(head, body)


def _emit_completion_notice(sender, notifier_role, ref_id, status, title, result_loc=None):
    """若 sender 是【自醒在岗的协调位】,往其 inbox 投一条 kind:notice 轻量完成通知,触发其后台 wait 自醒。
    cmd_done 与 dispatch_agent 收尾共用(dispatch 直接写 done 不走 cmd_done,故此前不触发自醒——本函数补齐)。
    防环铁律:notice 自身完成不要调本函数(调用方负责对 kind=notice 不调)。
    投递唯一判据=【sender 自醒在岗(armed)】——任何挂了后台 wait 的角色都投,不限 Master/MasterAgent。
    (原 HUMAN_ATTENDED 白名单闸已移除:它会让 Master数分 等新 hello 的自醒协调位静默失联;Master数分 报的 bug。)
    best-effort,绝不影响主流程。返回是否投了 notice。"""
    try:
        if _self_wake_state(sender)[0] in ("stale", "off"):   # recent 仍投:它重挂后即见 notice
            return False
        loc = result_loc or ("%s/done/%s.json" % (sender, ref_id))
        nid = "notice-" + ref_id
        notice = {
            "id": nid, "from": notifier_role, "to": sender, "kind": "notice",
            "title": "✅ 完成:" + (title or ref_id),
            "task": ("[完成通知·读完即弃、勿当任务执行] %s 完成了你派的《%s》(status=%s)。"
                     "完整结果在 %s;读完直接 done 本通知即可。"
                     % (notifier_role, title or ref_id, status, loc)),
            "notice_of": ref_id, "notice_status": status,
            "created": _now(), "status": "pending",
        }
        _write_json(BUS / sender / "inbox" / (nid + ".json"), notice)
        _audit("notify_wake", id=ref_id, to=sender)
        return True
    except Exception:
        return False
