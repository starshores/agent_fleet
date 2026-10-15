# -*- coding: utf-8 -*-
"""bus.presence —— presence.json 心跳/身份绑定 · current_task · pid_tags · owner 存活判定。

依赖:config(BUS/_now)· io(_write_json)· proc(_claude_ancestor/_age_seconds/_alive_claude_pids)。
"""
import json, os, socket

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, _now
from bus.io import _write_json, json_lock
from bus.proc import _host_ancestor, _host_session_id, _host_pid_alive, _age_seconds


def _touch_presence(role, mode, extra=None, *, expected_host_session_id=None):
    """写/更新 <role>/presence.json:声明身份 + 心跳(last_seen)。
    P1(presence 多写者·2026-07-28):①json_lock 跨 RMW——心跳(daemon 线程)与
    _set_current_task(claim/done)并发整写会互相丢字段;②心跳继承 current_task/current_title
    ——原实现整写抹掉、靠"领取与完成之间不发心跳"的时序假设,daemon 模式该假设不成立。"""
    pf = BUS / role / "presence.json"
    pres = None
    host_kind, host_pid, cwd, chain = _host_ancestor()  # 只沿当前进程父链绑定，不扫描其它会话
    host_sid = _host_session_id(host_kind)
    if expected_host_session_id is not None:
        if (not isinstance(expected_host_session_id, str) or not expected_host_session_id.strip()
                or host_kind != "codex" or not host_pid or host_sid != expected_host_session_id):
            raise RuntimeError("Codex resume host does not match expected session")
    try:
        with json_lock(pf, tries=50):
            prev = {}
            if pf.exists():
                try:
                    prev = json.loads(pf.read_text(encoding="utf-8"))
                except Exception:
                    prev = {}
            if os.environ.get("AGENTBUS_CODEX_BRIDGE_WAIT") == "1":
                # The messenger's internal wait is detached from the Codex
                # process. It may refresh heartbeat/mode, but must never erase
                # the exact host binding when parent walking returns nothing.
                bridge_sid = _host_session_id("codex")
                if (not bridge_sid or prev.get("host_kind") != "codex"
                        or prev.get("host_session_id") != bridge_sid
                        or not _host_pid_alive(prev.get("host_pid"), "codex")):
                    raise RuntimeError("Codex bridge wait has no live exact host binding")
                pres = dict(prev)
                pres.update(last_seen=_now(), mode=mode, shell_pid=os.getpid())
                if extra:
                    pres.update({k: v for k, v in extra.items()
                                 if k not in {"role", "host_kind", "host_pid", "host_session_id",
                                              "claude_pid", "codex_pid", "session_id", "codex_session_id"}})
                _write_json(pf, pres)
                return
            if expected_host_session_id is not None:
                # Resume's earlier lookup is advisory: hello may have rebound
                # this role while we waited for the existing presence lock.
                if (not isinstance(prev, dict)
                        or prev.get("host_session_id") != expected_host_session_id
                        or prev.get("host_kind") not in (None, "codex")
                        or (pf.parent / "parked.json").exists()):
                    raise RuntimeError("Codex resume binding changed before presence update")
            prev_kind = prev.get("host_kind") or ("claude" if prev.get("claude_pid") else None)
            prev_pid = prev.get("host_pid") or prev.get("claude_pid")
            if not host_pid and prev_pid and not _host_pid_alive(prev_pid, prev_kind):
                prev_kind = prev_pid = None
            host_replaced = bool(host_pid and (host_kind != prev_kind or host_pid != prev_pid))
            bound_kind = host_kind or prev_kind
            bound_pid = host_pid or prev_pid
            if host_replaced:
                # A host replacement must not inherit the previous runtime's session identity.
                prev = dict(prev)
                prev.pop("session_id", None)
                prev.pop("codex_session_id", None)
            if host_sid is None and bound_kind == prev_kind and not host_replaced:
                host_sid = prev.get("host_session_id")
            claude_sid = (_host_session_id("claude") if bound_kind == "claude"
                          else (prev.get("session_id") if bound_kind == "claude" else None))
            codex_sid = (_host_session_id("codex") if bound_kind == "codex"
                         else (prev.get("codex_session_id") if bound_kind == "codex" else None))
            pres = {
                "role": role,
                "host_kind": bound_kind,
                "host_pid": bound_pid,
                "host_session_id": host_sid,
                # Keep the legacy field strictly Claude-only; Codex must never be mislabeled as Claude.
                "claude_pid": bound_pid if bound_kind == "claude" else None,
                "codex_pid": bound_pid if bound_kind == "codex" else None,
                "shell_pid": os.getpid(),
                "cwd": cwd or prev.get("cwd"),
                "host": socket.gethostname(),
                "started": _now() if host_replaced else (prev.get("started") or _now()),
                "last_seen": _now(),
                "mode": mode,
                # Claude transcript id stays separate from the Codex host session id.
                "session_id": claude_sid,
                "codex_session_id": codex_sid,
            }
            for k in ("current_task", "current_title"):   # 心跳不丢"当前在做"(claim 设/done 清,只归它俩管)
                if k in prev:
                    pres[k] = prev[k]
            if extra:
                pres.update(extra)
            _write_json(pf, pres)
    except TimeoutError:
        pass   # 抢不到锁(极端)→ 本次心跳丢弃,下次补;绝不裸写覆盖
    if pres and pres.get("session_id"):
        _record_session_role(role, pres.get("session_id"))   # Claude transcript map only
    if (pres and host_kind == "codex" and host_sid
            and pres.get("host_kind") == "codex"
            and pres.get("host_session_id") == host_sid):
        from bus.codex_startup import record_codex_session_role
        record_codex_session_role(role, host_sid)


def _record_session_role(role, sid, src=None):
    """sid↔role append-only 持久账本(2026-08-03 Master数分口径·根治死会话 role 无从判):
    presence 只存每 role【最新】session_id,rebind 即覆盖=历史绑定丢(实测 54% 死会话无从判归属)。
    hello/心跳时把绑定永久留账 session_role_map.json → 此后每个 hello 过的会话,死了也能查到权威 role。
    首绑即记、同 sid 已记同 role 直接返回(热路径零写);同 sid 换 role(罕见误绑)追加 history 不抹。
    公共别名 record_session_role:daemon/dispatch 的 claude -p 子会话无 CLAUDE_CODE_SESSION_ID
    也不 hello,run_streaming 拿到 meta.session_id 后由调用方落账(2026-09-17 归属缺口修·
    实证 30 天窗 20 个无归属 session 里 7 个=8-27 daemon 试验批)。"""
    if not (role and sid):
        return
    f = BUS / "session_role_map.json"
    try:
        try:
            m = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except Exception:
            m = {}
        if (m.get(sid) or {}).get("role") == role:
            return   # 热路径:已记同绑定,不进锁不写盘
        from bus.config import _now as _now_fn
        with json_lock(f, tries=20):
            try:
                m = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            except Exception:
                m = {}
            cur = m.get(sid)
            if cur and cur.get("role") == role:
                return
            rec = {"role": role, "ts": _now_fn()}
            if src:
                rec["src"] = src   # 归属来源标记(如 dispatch-adhoc=ephemeral 代号·画像并 _adhoc 桶·Master数分 Q1 口径)
            if cur:
                rec["history"] = (cur.get("history") or []) + [{"role": cur.get("role"), "ts": cur.get("ts")}]
            m[sid] = rec
            _write_json(f, m)
    except Exception:
        pass   # 账本尽力而为,绝不影响 presence 主流程


record_session_role = _record_session_role   # 公共面:daemon/dispatch 落账用(见函数 docstring)


def _set_current_task(role, tid, title):
    """把'当前在做的任务'merge 进 presence(A3:让 who/WebUI 显示 worker/tab 在忙什么,
    而非看着像空闲)。claim 时设、done 时清(tid=None)。锁跨 RMW(P1),保留 presence 其它字段。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return
    try:
        with json_lock(pf, tries=50):
            try:
                pr = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                return
            if tid:
                pr["current_task"] = tid
                pr["current_title"] = (title or "")[:80]
            else:
                pr.pop("current_task", None)
                pr.pop("current_title", None)
            _write_json(pf, pr)
    except Exception:
        pass


def _pidtags_file():
    return BUS / "pid_tags.json"


def _load_pidtags():
    f = _pidtags_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _owner_live(role):
    """该 role 当前监听者的【进程】是否还在(给 reaper 判孤儿)。
    **只看进程在不在,不看心跳新鲜度**——这是踩过的坑:daemon 跑 claude -p 期间
    阻塞在 run_task、不发心跳,心跳会变陈但进程活着、任务正在做;若按"心跳陈了=死"去
    reap,会把【正在执行的任务】误判成孤儿退回 inbox → 重复执行(实测差点触发第二次外部写)。
    所以:进程真没了(pid 不存在)才算 owner 死;活着但卡住交给 reaper 的 ttl 硬上限去兜。
    listening:绑定的 Claude/Codex host 在跑;daemon:daemon_pid 在跑;idle/hello-only:没在听 → False
    (idle tab 不会推进 processing,其滞留任务该退回 inbox 等真 worker)。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return False
    try:
        pr = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return False
    mode = pr.get("mode")
    if psutil is None:
        # 没 psutil 只能退而求其次用心跳新鲜度(尽力,不如进程判准)
        age = _age_seconds(pr.get("last_seen", ""))
        wt = pr.get("wait_timeout") or 110
        return mode in ("listening", "daemon") and age is not None and age <= wt + 60
    if mode == "daemon":
        dpid = pr.get("daemon_pid")
        return bool(dpid and psutil.pid_exists(dpid))
    if mode == "listening":
        kind = pr.get("host_kind") or ("claude" if pr.get("claude_pid") else None)
        pid = pr.get("host_pid") or pr.get("claude_pid")
        return _host_pid_alive(pid, kind)
    return False  # idle / 其它:没在听的循环
