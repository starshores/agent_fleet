# -*- coding: utf-8 -*-
"""bus.mailbox —— 任务原子领取原语(inbox→processing)+ 自身角色反查 + 可领取筛选。

低层原语:core(send/wait/done)与 pool(工作组)都复用它 → 下沉到此,打破 core↔pool 互相依赖环。
依赖:config(BUS/_now)· io(_write_json/_audit)· presence(_set_current_task)· proc(_claude_ancestor)。
"""
import os, json, time

from bus.config import BUS, _now
from bus.io import _write_json, _audit, _steal_stale_lock
from bus.presence import _set_current_task
from bus.proc import _host_ancestor, _host_session_id
from bus.codex_recovery import (claim_blocked, protect_claim, is_codex_role,
                                fence_tasks, verify_claim_identity)


def _claim(role, taskpath, locked=False, *, codex_session_id=None):
    """原子领取:把任务从 inbox 移到 processing,返回 task dict。
    并发安全 = O_EXCL 独占锁(<task>.lock 谁建成谁独占;P0 统一·2026-07-28):
    本机实测 os.replace 争同源【会双胜】(pool.py gate① 压测 200-300 轮出 2-8 胜者),
    多监听者(tab wait + daemon)并存时靠 replace 判胜=同任务双执行风险,故与组池统一 O_EXCL。
    抢输/文件没了返回 None;坏文件返回 {"error":...}。
    locked=True:调用方(pool._claim_group_task)已持有同名 <task>.lock → 跳过抢锁直接搬运
    (否则自伤:组池持锁调进来会被自己的锁打成落空)。
    stale 回收:锁主建锁后崩溃留孤儿 .lock → mtime 超 60s 强清重抢(claim 是毫秒级动作)。"""
    lock = taskpath.with_name(taskpath.name + ".lock")
    fd = None
    if not locked:
        for attempt in range(2):
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if attempt == 0 and time.time() - lock.stat().st_mtime > 60:
                        _steal_stale_lock(lock)   # 孤儿锁 → rename 原子夺取(ABA 防·P1)后重抢一次
                        continue
                except OSError:
                    pass
                return None                # 真被别的监听者独占(锁新鲜)→ 干净落空
            except OSError:
                return None
        if fd is None:
            return None
        try:
            os.write(fd, json.dumps({"who": role, "pid": os.getpid(), "acquired_at": _now()},
                                    ensure_ascii=False).encode("utf-8"))   # 记 pid/时间供排障
        except Exception:
            pass
        os.close(fd)
    proc = BUS / role / "processing" / taskpath.name
    recovery = None
    try:
        sid = (codex_session_id if codex_session_id is not None else
               os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"))
        codex_claim = codex_session_id is not None or bool(sid) or is_codex_role(role)
        if codex_claim:
            sid = sid or _host_session_id("codex")
            verify_claim_identity(role, sid)
        if not taskpath.is_file():
            return None
        try:
            candidate = json.loads(taskpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if codex_claim:
                raise ValueError("Invalid Codex task; delivery refused")
            candidate = None  # Preserve legacy bad-file handling after movement.
        if claim_blocked(role, taskpath.stem, candidate):
            return None
        if codex_claim and (not isinstance(candidate, dict) or candidate.get("id") != taskpath.stem):
            raise ValueError("Codex task id must match its queue filename")
        if codex_claim and proc.exists():
            fence_tasks(role, [candidate], "duplicate_processing_task")
            return None
        if codex_claim:
            recovery = protect_claim(role, taskpath.stem, required=True, session_id=sid)
        for attempt in range(4):
            try:
                if codex_claim:
                    verify_claim_identity(role, sid)
                os.replace(str(taskpath), str(proc))  # 此刻仅持锁者到达,replace 无并发争用=纯搬运
                break
            except FileNotFoundError:
                return None  # 已被(旧版本监听者/reap)移走——不重试
            except OSError:
                # Windows 下源文件被 watcher/AV 瞬时占用会抛 PermissionError(OSError 子类)。
                # 源还在就短重试(Master 实测反馈);源没了=被移走,放弃。
                if not taskpath.exists():
                    return None
                if attempt == 3:
                    return None  # 重试仍失败,本轮放弃(任务留 inbox,下轮再领)
                time.sleep(0.04 * (attempt + 1))
    finally:
        try:
            lock.unlink()   # task 已移走/放弃,释放锁(残留也无害:_oldest_claimable 只认 *.json)
        except Exception:
            pass
    try:
        obj = json.loads(proc.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": "bad task file: %s" % e}
    obj["status"] = "processing"
    if recovery is not None:
        obj["codex_recovery"] = recovery
    obj["claimed"] = _now()
    obj["claim_pid"] = os.getpid()                    # ④型排障/孤儿判定(2026-09-24 Master数分 15b5e5):claim 者 pid·死了=悬单
    try:
        obj["claim_ppid"] = os.getppid()              # 父壳(bash 包装)pid·harness 内存回收杀的就是它
    except Exception:
        pass
    try:
        _write_json(proc, obj)   # 更新 status/claimed 供 who/WebUI 看
    except Exception as e:
        if recovery is not None:
            # The durable fence already exists; never deliver a task whose
            # Codex claim metadata could not be persisted.
            raise RuntimeError("Codex claim metadata could not be persisted") from e
        # 任务已原子移入 processing(上面 os.replace 成功),状态改写只是尽力而为——
        # 写失败(瞬时锁等持久化失败)绝不能崩 wait,否则协调位自醒掉线漏消息(Master 报的惨案)。
        _audit("claim_write_warn", id=obj.get("id", taskpath.stem), role=role, err=str(e)[:80])
    _audit("claim", id=obj.get("id", taskpath.stem), role=role, frm=obj.get("from"))
    if recovery is not None:
        verify_claim_identity(role, sid)
    _set_current_task(role, obj.get("id", taskpath.stem), obj.get("title"))  # A3:presence 记"当前在做",who/WebUI 可见
    return obj


def _claim_and_print(role, taskpath):
    print(json.dumps(_claim(role, taskpath), ensure_ascii=False))


def _resolve_self_role():
    """Resolve this session's role; Codex must use its unique session id.

    Multiple Codex threads share an App Server PID, so PID matching can select
    another role's presence. Keep PID fallback only for legacy Claude sessions.
    """
    kind, pid, _cwd, _chain = _host_ancestor()
    sid = _host_session_id(kind)
    if not kind and (os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")):
        kind = "codex"
        sid = _host_session_id("codex")
    if kind == "codex":
        if not sid or not BUS.exists():
            return None
        matches = []
        for d in BUS.iterdir():
            pf = d / "presence.json"
            if not d.is_dir() or not pf.exists():
                continue
            try:
                pr = json.loads(pf.read_text(encoding="utf-8"))
                if (pr.get("host_kind") == "codex"
                        and sid == pr.get("host_session_id")):
                    matches.append(d.name)
            except Exception:
                pass
        return matches[0] if len(matches) == 1 else None
    if not pid or not BUS.exists():
        return None
    for d in BUS.iterdir():
        pf = d / "presence.json"
        if d.is_dir() and pf.exists():
            try:
                pr = json.loads(pf.read_text(encoding="utf-8"))
                if (pr.get("host_pid") or pr.get("claude_pid")) == pid:
                    return d.name
            except Exception:
                pass
    return None


def _oldest_claimable(inbox, include_approval=False):
    """inbox 里最老的【可自动领取】任务。requires_approval=true 的高风险任务默认跳过
    (统一的'先审后做'闸:tab serve / daemon / wait 都走这里,所以闸对 tab 和 daemon 一致生效),
    留在 inbox 等人工 `wait --include-approval` 在有人值守的 tab 里领取执行。"""
    for p in sorted(inbox.glob("*.json")):
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
            if claim_blocked(inbox.parent.name, p.stem, o):
                continue
            if not include_approval and o.get("requires_approval"):
                continue  # 高风险:不自动领,跳过
        except Exception:
            if claim_blocked(inbox.parent.name, p.stem):
                continue
        return p
    return None
