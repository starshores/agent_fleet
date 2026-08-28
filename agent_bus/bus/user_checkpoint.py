# -*- coding: utf-8 -*-
"""bus.user_checkpoint —— 用户侧自醒对偶 + 缺席检测(user-absence detection·2026-08-28)。

洞察(用户):agent 有【自醒】(心跳)能被自动发现缺席;【用户侧无对偶】→ 用户久不回时
没人能自动发现,pending 确认点脱离协作上下文/无 age 追踪 = 烂着没人知道。

本模块补两件(对偶 selfwake-guard):
- user_last_seen(全局在场信号·任一 session 用户活动即盖章)→ 判 away vs present。
- user-absence-check guard(扫 pending 确认 checkpoint·present/away × age × urgency → 梯度升级·
  低紧急可 auto-expire 但必留痕、高风险绝不自动过期)。

确认点(checkpoint)= todos.json 里 source 以"确认·"起头的项(复用 ask-user·对齐培训师纪律
"确认点 inline 进协作记录")。不另建存储。

口径(Master数分 v1·可调进 user_checkpoint_config.json·非硬编码):
- ① 阈值 urgency × 日夜二维;② ★防误报=present/away 两正交信号拆开;③ age=now−created;
- ④ 高风险绝不自动过期、auto-expire 必留痕不伪装已答;⑤ 升级梯度全程非阻塞。

kind 维(Master数分 v2·2026-08-28·MasterUI 抛 P1c):checkpoint 分【待决策 decision】(需拍板/取舍·必显式答)
vs【待知悉结论 fyi】(活已完成·交付报告·只需轻量 ack)。取值:显式 --kind 优先 → 标题标记派生 →
★默认 decision(安全默认·拿不准当待决策·别误判成可自动关的 fyi=漏用户该做的决策=违④)。
kind × urgency 正交(kind 管『要不要拍板』·urgency 管『多急』);面板分两栏计数不夸大开放决策数。
"""
import json, os, time
from bus.config import BUS, _now
from bus.io import _write_json, _audit, json_lock

USER_SEEN = BUS / "user_last_seen.json"
CKPT_CFG = BUS / "user_checkpoint_config.json"

# ── v1 基线口径(Master数分 2026-08-28)·文件缺时用此·可调不改码 ──────────────────
_DEFAULT_CFG = {
    "_comment": "用户确认点缺席检测口径(Master数分 v1 基线·可调)。时段 24h 制;阈值单位=分钟。",
    "day_start": "09:00", "day_end": "22:00",     # 日=09-22 / 夜=22-09(参照自醒 wait 日2h夜6h 分档)
    "away_idle_min": 20,                            # 全局无用户活动超此分钟 = away(离席);内=present(在场)
    "attention_min": 10,                            # present 但没理这条超此 age = 轻 nudge 面板醒目(不打断)
    "escalate_after_min": {                         # away+unaddressed 达此 age → 升级(按 urgency)
        "high": 15, "normal": 45, "low": 120,
    },
    "night_defer": True,                            # 夜间压制 toast/push(除不可逆高风险)·队列次日白天首刻
    "auto_expire": {                                # ④ auto-expire 阈值(必留痕·分 kind×urgency·Master数分口径 2026-08-28)
        "low_after_min": 1440,                      # decision+low 超 24h 无答 → auto-expire(标注·不伪装已答)
        "normal_after_min": None,                   # decision+normal 不自动过期(持续挂+升级)
        "high_after_min": None,                     # decision+high 绝不自动过期(数据真实性红线·必须显式答)
        "fyi_after_min": 720,                       # ★fyi(知悉结论)超此→auto-archive 归档(Master数分:略短于 decision-low·超期无害·留痕;主路径=批量ack·此为兜底非激进)
    },
}


def _load_cfg():
    """默认口径 + 配置文件深合并(嵌套 dict 逐键并·非整块替换)。
    ★深合并要紧:配置文件只需写要改的键;整块替换会让旧文件(缺新键如 fyi_after_min)覆盖掉新默认。"""
    import copy
    cfg = copy.deepcopy(_DEFAULT_CFG)
    try:
        if CKPT_CFG.exists():
            d = json.loads(CKPT_CFG.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                        cfg[k].update(v)          # 嵌套 dict:逐键并(保未提及的默认键)
                    else:
                        cfg[k] = v
    except Exception:
        pass
    return cfg


def _parse_iso(ts):
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def _hhmm_to_min(s):
    try:
        h, m = s.split(":"); return int(h) * 60 + int(m)
    except Exception:
        return 0


def _is_night(cfg, now=None):
    """当前是否夜间(按 day_start/day_end)。now=time.time()。"""
    lt = time.localtime(now if now is not None else time.time())
    cur = lt.tm_hour * 60 + lt.tm_min
    ds, de = _hhmm_to_min(cfg["day_start"]), _hhmm_to_min(cfg["day_end"])
    day = (ds <= cur < de) if ds < de else (cur >= ds or cur < de)
    return not day


# ── user_last_seen:全局在场信号(信号①)────────────────────────────────────
def touch_user_seen(source="prompt", session=None):
    """盖章"用户此刻活跃"(任一 session)。源:UserPromptSubmit hook(用户发消息)/ cmd_answer(答确认)。"""
    rec = {"ts": _now(), "epoch": time.time(), "source": source, "session": session or ""}
    try:
        with json_lock(USER_SEEN):
            _write_json(USER_SEEN, rec)
    except Exception:
        try: _write_json(USER_SEEN, rec)
        except Exception: pass
    return rec


def user_last_seen_epoch():
    try:
        d = json.loads(USER_SEEN.read_text(encoding="utf-8"))
        return float(d.get("epoch") or _parse_iso(d.get("ts", "")) or 0)
    except Exception:
        return 0.0


def cmd_user_seen(args):
    """盖章用户活跃(供 UserPromptSubmit hook / 协调位转达用户消息时调)。"""
    rec = touch_user_seen(source=getattr(args, "source", None) or "cli",
                          session=getattr(args, "session", None))
    _audit("user_seen", source=rec["source"])
    print("✓ user_last_seen 已盖章 @ %s (source=%s)" % (rec["ts"], rec["source"]))


# ── checkpoint 视图:todos.json 的"确认·"项 ────────────────────────────────
_PENDING = ("待办", "进行中")
_URGENCY_BY_PRIO = {"高": "high", "中": "normal", "低": "low"}


def _urgency_of(item):
    """urgency:显式 item.urgency 优先;否则由 priority 派生(高=high/中=normal/低=low)。"""
    u = (item.get("urgency") or "").strip().lower()
    if u in ("high", "normal", "low"):
        return u
    return _URGENCY_BY_PRIO.get(item.get("priority"), "normal")


# ── kind:待决策(decision)vs 待知悉结论(fyi)·Master数分口径 2026-08-28【2】────────────
# 派生标记(缺显式 --kind 时用):decision 标记优先命中 → decision(安全默认偏向待决策)。
# ★含"待定/待确认/未决"→ decision:Master数分反例『[完成·待确认]…+2项待定』标题带"完成"像 fyi 但含未决点=decision,
# 纯启发式误判 fyi 自动关=丢待定=数据真实性事故;显式识别未决标记 → 稳判 decision(不靠默认侥幸)。
_KIND_DECISION_MARKERS = ("需业务定", "拍板", "怎么办", "需定", "待信息", "待定", "待确认", "未决",
                          "?", "?", "二选", "选择", "确认是否")
_KIND_FYI_MARKERS = ("[已完成]", "[完成]", "已完成", "结论", "报告", "report", "知悉", "fyi", "交付")


def _kind_of(item):
    """kind:显式 item.kind 优先(decision|fyi)→ 否则标题/detail 标记派生 → ★默认 decision。
    ③安全默认(数据真实性):拿不准当【待决策】·别默认成可自动 ack/关的 fyi——把真决策误判成 fyi
    自动关=漏掉用户该做的决策=违④。故 decision 标记优先于 fyi 标记,二者皆无 → decision。"""
    k = (item.get("kind") or "").strip().lower()
    if k in ("decision", "fyi"):
        return k
    # 只扫【标题】(Master数分口径【2】"由标题标记"·非 detail)——扫 detail 会过度促成 fyi、违安全默认精神。
    text = item.get("title") or ""
    tl = text.lower()
    for m in _KIND_DECISION_MARKERS:            # decision 标记优先(安全默认偏待决策)
        if m in text or m.lower() in tl:
            return "decision"
    for m in _KIND_FYI_MARKERS:
        if m in text or m.lower() in tl:
            return "fyi"
    return "decision"                            # ③ 安全默认=待决策


def _expire_after(cfg, kind, urg):
    """该 checkpoint 的 auto-expire 阈值(分钟)·None=永不自动过期。kind×urgency 口径(Master数分【3】):
    - fyi(知悉结论):超期无害 → fyi_after_min(与 urgency 正交)。
    - decision(待决策):只 low 可超期(low_after_min);normal/high=None(★high 绝不·④红线)。"""
    ae = cfg.get("auto_expire") or {}
    if kind == "fyi":
        return ae.get("fyi_after_min")
    return ae.get(urg + "_after_min")            # decision:按 urgency(low 有值·normal/high=None)


def _scan_checkpoints():
    """读 todos.json,取所有 pending 的确认点(source 以'确认·'起头)。"""
    tf = BUS / "todos.json"
    try:
        todos = json.loads(tf.read_text(encoding="utf-8")) if tf.exists() else []
        if not isinstance(todos, list):
            todos = []
    except Exception:
        todos = []
    out = []
    for t in todos:
        if str(t.get("source", "")).startswith("确认·") and t.get("status") in _PENDING:
            out.append(t)
    return out


def _classify(cfg, item, now, present):
    """给一条 checkpoint 定状态(★核心·Master数分口径 ②③⑤ + kind 正交)。返回 (state, age_min, urgency, kind)。
    state ∈ fresh / nudge(present+未理+超attention) / escalate(away+未理+超阈) / expireable(超期·kind×urg)。
    kind 管『要不要拍板』·urgency 管『多急』·两轴正交(Master数分【4】)——escalate 仍按 urgency(fyi 也可 high 尽快知悉)。"""
    urg = _urgency_of(item)
    kind = _kind_of(item)
    created = _parse_iso(item.get("created", ""))
    age = (now - created) / 60.0 if created else 0.0     # ③ age=now−created
    esc_th = (cfg.get("escalate_after_min") or {}).get(urg, 45)
    exp_th = _expire_after(cfg, kind, urg)               # ④ kind×urgency:fyi→fyi_after_min·decision→仅low
    if exp_th and age >= exp_th:
        return "expireable", age, urg, kind
    # ② present/away × 未理(该 checkpoint 仍 pending=未 confirmed)
    if present:
        if age >= cfg.get("attention_min", 10):
            return "nudge", age, urg, kind    # 在场但没理这条 → 面板醒目·不打断
        return "fresh", age, urg, kind
    else:  # away
        if age >= esc_th:
            return "escalate", age, urg, kind  # 离席+未理+超阈 → 真『用户很久没回』
        return "fresh", age, urg, kind


def cmd_user_absence_check(args):
    """用户缺席检测 guard(对偶 selfwake-guard)。扫 pending 确认 checkpoint,交叉 present/away × age × urgency,
    给出每条状态 + 该升级的项;--json 供 WebUI;--apply 执行 auto-expire(留痕)+ toast/push 升级(夜间压制)。"""
    from bus.fleetlog import maint_active
    cfg = _load_cfg()
    now = time.time()
    night = _is_night(cfg, now)
    seen = user_last_seen_epoch()
    idle_min = (now - seen) / 60.0 if seen else 1e9
    present = idle_min < cfg.get("away_idle_min", 20)     # 信号①:全局在场 vs 离席
    cps = _scan_checkpoints()

    rows, to_escalate, to_expire = [], [], []
    for it in cps:
        state, age, urg, kind = _classify(cfg, it, now, present)
        row = {"id": it.get("id"), "title": (it.get("title") or "")[:60], "from": it.get("confirm_from"),
               "ref": it.get("ref"), "urgency": urg, "kind": kind, "age_min": round(age, 1), "state": state,
               "created": it.get("created")}
        rows.append(row)
        if state == "escalate":
            to_escalate.append(row)
        elif state == "expireable":
            to_expire.append((it, row))

    # ⑤ 呈现 truthfulness(Master数分【5】):分两栏计数——『待你决策』vs『待你知悉』,不笼统一个『N待确认』(夸大开放决策数)
    n_decision = sum(1 for r in rows if r["kind"] == "decision")
    n_fyi = sum(1 for r in rows if r["kind"] == "fyi")
    result = {
        "as_of": _now(), "night": night,
        "user_present": present, "user_idle_min": round(idle_min, 1) if seen else None,
        "pending": len(cps), "pending_decision": n_decision, "pending_fyi": n_fyi, "checkpoints": rows,
        "to_escalate": to_escalate, "expireable": [r for _, r in to_expire],
    }

    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False)); return

    if maint_active():
        print("🔧 维修模式:用户缺席检测休眠。"); return

    # ── --apply:执行 auto-expire(④ 留痕不伪装已答)+ 升级(⑤ 非阻塞·夜间压制)──
    apply = getattr(args, "apply", False)
    acted = []
    if to_expire and apply:
        tf = BUS / "todos.json"
        with json_lock(tf):
            try: todos = json.loads(tf.read_text(encoding="utf-8"))
            except Exception: todos = []
            byid = {t.get("id"): t for t in todos}
            ae = cfg.get("auto_expire") or {}
            for it, row in to_expire:
                t = byid.get(it.get("id"))
                if not t: continue
                # ★数据真实性红线:标 expired + 显式留痕,绝不伪装成"已答/完成"。留痕文案分 kind:
                # fyi=知悉类超期归档(无害);decision=低紧急超期(用户从未回答·仍非已确认)。
                t["status"] = "expired"; t["updated"] = _now()
                if row["kind"] == "fyi":
                    t["expire_note"] = "已知悉结论·超期自动归档(age>%s min)·超期无害·非用户确认" % ae.get("fyi_after_min")
                    t["detail"] = ((t.get("detail", "") or "") + " 〔⏰auto-archived·知悉类超期归档·非拍板/非已确认〕").strip()
                else:
                    t["expire_note"] = "用户从未回答·低紧急超期自动过期(age>%s min)·非用户确认" % ae.get("low_after_min")
                    t["detail"] = ((t.get("detail", "") or "") + " 〔⏰auto-expired·用户从未回答·非已确认〕").strip()
                acted.append(("expired", row["id"]))
            _write_json(tf, todos)
        for _, cid in acted:
            _audit("checkpoint_auto_expire", id=cid, note="low超期·留痕非伪装")

    # 升级:escalate 项(away+未理+超阈)→ WinRT toast 汇总(夜间压制 toast/push·除非不可逆高风险 urgency=high)
    esc_toast = [r for r in to_escalate if not (night and cfg.get("night_defer", True) and r["urgency"] != "high")]
    if esc_toast and apply:
        titles = "、".join("《%s》(%s·%dmin)" % (r["title"], r["urgency"], int(r["age_min"])) for r in esc_toast[:5])
        _toast("⏰ 用户久未回确认点(%d)" % len(esc_toast), "用户离席%dmin·待确认:%s" % (int(idle_min), titles))
        for r in esc_toast:
            _audit("checkpoint_escalate", id=r["id"], urgency=r["urgency"], age_min=r["age_min"])

    # 打印摘要
    tag = "🌙夜" if night else "☀️日"
    who = ("在场present(idle %dmin)" % idle_min) if present else ("离席away(idle %dmin)" % idle_min if seen else "无last-seen记录")
    print("用户缺席检测 [%s·%s] · pending确认 %d(待决策%d/待知悉%d)" % (tag, who, len(cps), n_decision, n_fyi))
    for r in rows:
        icon = {"escalate": "🔴升级", "nudge": "🟡nudge", "expireable": "⏰可过期", "fresh": "·"}.get(r["state"], "·")
        kicon = "🗳️决策" if r["kind"] == "decision" else "📄知悉"
        print("  %s %s %s《%s》·age%dmin·from %s" % (icon, kicon, r["urgency"], r["title"], int(r["age_min"]), r["from"]))
    if apply and (acted or esc_toast):
        print("  → 执行:auto-expired %d(留痕)· toast升级 %d" % (len(acted), len(esc_toast)))
    elif not apply and (to_escalate or to_expire):
        print("  (dry:%d 待升级 / %d 可过期;--apply 执行)" % (len(to_escalate), len(to_expire)))


def _toast(title, body):
    """WinRT toast(非阻塞·复用全局模板 AppID)。失败静默(升级是尽力而为·不阻塞检测)。"""
    import subprocess
    ps = (
        '$x=New-Object Windows.Data.Xml.Dom.XmlDocument;'
        '$x.LoadXml(\'<toast><visual><binding template="ToastGeneric"><text>%s</text><text>%s</text></binding></visual></toast>\');'
        '[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;'
        '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("ClaudeCode.NotifyTest").Show('
        '[Windows.UI.Notifications.ToastNotification]::new($x))'
    ) % (title.replace("'", ""), body.replace("'", "")[:180])
    try:
        subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                       capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass
