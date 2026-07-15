# -*- coding: utf-8 -*-
"""bus.mailbox —— 任务原子领取原语(inbox→processing)+ 自身角色反查 + 可领取筛选。

低层原语:core(send/wait/done)与 pool(工作组)都复用它 → 下沉到此,打破 core↔pool 互相依赖环。
依赖:config(BUS/_now)· io(_write_json/_audit)· presence(_set_current_task)· proc(_claude_ancestor)。
"""
import os, json, time

from bus.config import BUS, _now
from bus.io import _write_json, _audit
from bus.presence import _set_current_task
from bus.proc import _claude_ancestor


def _claim(role, taskpath):
    """原子领取:把任务从 inbox 移到 processing,返回 task dict。
    多监听者(tab /loop + daemon)并存时只有一个 rename 成功,其余返回 None(没抢到),
    所以绝不会重复执行同一任务。坏文件返回 {"error":...}。"""
    proc = BUS / role / "processing" / taskpath.name
    for attempt in range(4):
        try:
            os.replace(str(taskpath), str(proc))  # 原子;抢输的那个抛 FileNotFoundError
            break
        except FileNotFoundError:
            return None  # 已被别的监听者领走 / 文件没了——不重试
        except OSError:
            # Windows 下源文件被 watcher/AV 瞬时占用会抛 PermissionError(OSError 子类)→ 领取竞态。
            # 源还在就短重试(Master 实测反馈);源没了=被领走,放弃。
            if not taskpath.exists():
                return None
            if attempt == 3:
                return None  # 重试仍失败,本轮放弃(任务留 inbox,下轮再领)
            time.sleep(0.04 * (attempt + 1))
    try:
        obj = json.loads(proc.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": "bad task file: %s" % e}
    obj["status"] = "processing"
    obj["claimed"] = _now()
    try:
        _write_json(proc, obj)   # 更新 status/claimed 供 who/WebUI 看
    except Exception as e:
        # 任务已原子移入 processing(上面 os.replace 成功),状态改写只是尽力而为——
        # 写失败(瞬时锁等持久化失败)绝不能崩 wait,否则协调位自醒掉线漏消息(Master 报的惨案)。
        _audit("claim_write_warn", id=obj.get("id", taskpath.stem), role=role, err=str(e)[:80])
    _audit("claim", id=obj.get("id", taskpath.stem), role=role, frm=obj.get("from"))
    _set_current_task(role, obj.get("id", taskpath.stem), obj.get("title"))  # A3:presence 记"当前在做",who/WebUI 可见
    return obj


def _claim_and_print(role, taskpath):
    print(json.dumps(_claim(role, taskpath), ensure_ascii=False))


def _resolve_self_role():
    """靠本 tab 的 claude 进程 pid 反查它声明过的 role(presence.json 里 claude_pid 匹配)。"""
    pid, _, _ = _claude_ancestor()
    if not pid or not BUS.exists():
        return None
    for d in BUS.iterdir():
        pf = d / "presence.json"
        if d.is_dir() and pf.exists():
            try:
                pr = json.loads(pf.read_text(encoding="utf-8"))
                if pr.get("claude_pid") == pid:
                    return d.name
            except Exception:
                pass
    return None


def _oldest_claimable(inbox, include_approval=False):
    """inbox 里最老的【可自动领取】任务。requires_approval=true 的高风险任务默认跳过
    (统一的'先审后做'闸:tab serve / daemon / wait 都走这里,所以闸对 tab 和 daemon 一致生效),
    留在 inbox 等人工 `wait --include-approval` 在有人值守的 tab 里领取执行。"""
    for p in sorted(inbox.glob("*.json")):
        if include_approval:
            return p
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
            if o.get("requires_approval"):
                continue  # 高风险:不自动领,跳过
        except Exception:
            pass
        return p
    return None
