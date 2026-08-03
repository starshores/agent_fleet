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
from bus.proc import _claude_ancestor, _age_seconds, _alive_claude_pids, _claude_by_session


def _touch_presence(role, mode, extra=None):
    """写/更新 <role>/presence.json:声明身份 + 心跳(last_seen)。
    P1(presence 多写者·2026-07-28):①json_lock 跨 RMW——心跳(daemon 线程)与
    _set_current_task(claim/done)并发整写会互相丢字段;②心跳继承 current_task/current_title
    ——原实现整写抹掉、靠"领取与完成之间不发心跳"的时序假设,daemon 模式该假设不成立。"""
    pf = BUS / role / "presence.json"
    pres = None
    claude_pid, cwd, chain = _claude_ancestor()   # 进程探查在锁外(慢:psutil 走祖先链)
    if not claude_pid:
        # parent-walk 失败(本 harness 下 Bash 子 shell 父链偶发够不到 claude.exe)→ 按 session_id
        # 反查 `claude --resume <sid>` 的活进程(2026-07-29 模型侧报:resume 会话 presence 绑死旧 pid 的治本)
        sp, sc = _claude_by_session(os.environ.get("CLAUDE_CODE_SESSION_ID"))
        claude_pid, cwd = sp, (cwd or sc)
    try:
        with json_lock(pf, tries=50):
            prev = {}
            if pf.exists():
                try:
                    prev = json.loads(pf.read_text(encoding="utf-8"))
                except Exception:
                    prev = {}
            inherited_pid = prev.get("claude_pid")
            if not claude_pid and inherited_pid and inherited_pid not in _alive_claude_pids():
                # 死 pid 不永续(2026-07-29 重启实证):电脑重启后旧 pid 永远死,继承会让 who 永判"残留"
                # 且掩盖真进程未绑;置 None 让 who 显"无 pid"→按 CLAUDE.md 补跑 hello 重绑(诚实降级)。
                inherited_pid = None
            pres = {
                "role": role,
                # 探查不到就继承旧值(同 session_id 模式·2026-07-29 培训师报):后台 wait/心跳进程的
                # 祖先链常解析不到 claude(派生壳重挂靠)→ 原来无条件写会把 pid/cwd 冲成 None,
                # wait 期周期心跳(70099ac)放大了冲刷频率。真死进程由 who/selfwake 的存活检查兜底判定;
                # 但【死 claude pid 不继承】(上面置 None),防重启后旧 pid 被心跳永续。
                "claude_pid": claude_pid or inherited_pid,
                "shell_pid": os.getpid(),
                "cwd": cwd or prev.get("cwd"),
                "host": socket.gethostname(),
                "started": prev.get("started") or _now(),
                "last_seen": _now(),
                "mode": mode,
                # 权威 session→role 绑定:交互 tab 的 hello/心跳子进程继承 CLAUDE_CODE_SESSION_ID
                # (= 会话 jsonl id),据此 WebUI 看板精确对齐、不再 regex 猜 role。daemon 无此 env→None。
                "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID") or prev.get("session_id"),
            }
            for k in ("current_task", "current_title"):   # 心跳不丢"当前在做"(claim 设/done 清,只归它俩管)
                if k in prev:
                    pres[k] = prev[k]
            if extra:
                pres.update(extra)
            _write_json(pf, pres)
    except TimeoutError:
        pass   # 抢不到锁(极端)→ 本次心跳丢弃,下次补;绝不裸写覆盖
    if pres:
        _record_session_role(role, pres.get("session_id"))   # 锁外落账本(独立文件,自有锁)


def _record_session_role(role, sid):
    """sid↔role append-only 持久账本(2026-08-03 Master数分口径·根治死会话 role 无从判):
    presence 只存每 role【最新】session_id,rebind 即覆盖=历史绑定丢(实测 54% 死会话无从判归属)。
    hello/心跳时把绑定永久留账 session_role_map.json → 此后每个 hello 过的会话,死了也能查到权威 role。
    首绑即记、同 sid 已记同 role 直接返回(热路径零写);同 sid 换 role(罕见误绑)追加 history 不抹。"""
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
            if cur:
                rec["history"] = (cur.get("history") or []) + [{"role": cur.get("role"), "ts": cur.get("ts")}]
            m[sid] = rec
            _write_json(f, m)
    except Exception:
        pass   # 账本尽力而为,绝不影响 presence 主流程


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
    listening:claude_pid 在跑;daemon:daemon_pid 在跑;idle/hello-only:没在听 → False
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
        pid = pr.get("claude_pid")
        return bool(pid and pid in _alive_claude_pids())
    return False  # idle / 其它:没在听的循环
