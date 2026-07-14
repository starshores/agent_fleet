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
from bus.io import _write_json
from bus.proc import _claude_ancestor, _age_seconds, _alive_claude_pids


def _touch_presence(role, mode, extra=None):
    """写/更新 <role>/presence.json:声明身份 + 心跳(last_seen)。"""
    pf = BUS / role / "presence.json"
    prev = {}
    if pf.exists():
        try:
            prev = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    claude_pid, cwd, chain = _claude_ancestor()
    pres = {
        "role": role,
        "claude_pid": claude_pid,
        "shell_pid": os.getpid(),
        "cwd": cwd,
        "host": socket.gethostname(),
        "started": prev.get("started") or _now(),
        "last_seen": _now(),
        "mode": mode,
        # 权威 session→role 绑定:交互 tab 的 hello/心跳子进程继承 CLAUDE_CODE_SESSION_ID
        # (= 会话 jsonl id),据此 WebUI 看板精确对齐、不再 regex 猜 role。daemon 无此 env→None。
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID") or prev.get("session_id"),
    }
    if extra:
        pres.update(extra)
    _write_json(pf, pres)


def _set_current_task(role, tid, title):
    """把'当前在做的任务'merge 进 presence(A3:让 who/WebUI 显示 worker/tab 在忙什么,
    而非看着像空闲)。claim 时设、done 时清(tid=None)。merge 写,保留 presence 其它字段。
    注:_touch_presence 心跳不保留该字段——但领取与完成之间不发心跳,故 current_task 在执行期间稳定可见。"""
    pf = BUS / role / "presence.json"
    if not pf.exists():
        return
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
    try:
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
