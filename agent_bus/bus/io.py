# -*- coding: utf-8 -*-
"""bus.io —— 原子落地 / 共享 JSON 锁 / id 生成 / 审计日志 / BI 资源指标(叶子层,仅依赖 bus.config)。"""
import json, os, time
from contextlib import contextmanager
from bus.config import BUS, METRICS, _now


def _gen_id(role):
    # 形如 <毫秒ts>-<role>-<6hex>。role 嵌 id 曾与 done 的 startswith 前缀匹配叠出错配歧义面
    # (P2 审):P1 已改 _find_task 精确 id 匹配,歧义面消;保留 role 段=人读排障友好,不再改。
    return "%d-%s-%s" % (time.time_ns() // 1_000_000, role, os.urandom(3).hex())


def _write_json(path, obj):
    # tmp 名带 pid+随机:两进程同写同目标时,固定 .tmp 名会让 B 的半截文件被 A replace 进正式位
    # (P0·doc-blind 2026-07-28);随机化后各写各的 tmp,replace 仍原子=后写者胜、绝无半截。
    tmp = path.parent / (path.name + ".%d-%s.tmp" % (os.getpid(), os.urandom(2).hex()))
    tmp.parent.mkdir(parents=True, exist_ok=True)   # 父目录保障:全新 role 首个 wait 写 trace 曾因目录未建而崩(P1 实测抓获)
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    # 原子落地,避免 watcher 读到半截。os.replace 在 Windows 下若目标被 watcher/AV/另一 waiter
    # 瞬时占用会抛 PermissionError(WinError 5)——短 backoff 重试,绝不让瞬时锁把调用方崩掉
    # (尤其自醒 wait 进程:Master 报过 _claim 改写时撞 WinError 5 → wait exit 1 → 协调位掉线漏消息)。
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == 4:
                try: tmp.unlink()           # 持久失败:清掉孤儿 .tmp 再抛
                except Exception: pass
                raise
            time.sleep(0.05 * (attempt + 1))


def _oldest(folder):
    files = sorted([p for p in folder.glob("*.json")])
    return files[0] if files else None


def _fail(msg, code=1):
    """错误通道统一(P2·2026-07-28):{"error": msg} 落 stdout(机器可读不变)+ 非零退出码。
    原状=各命令 print error 后 exit 0,脚本/daemon 调用方无法用退出码判败,只能 parse stdout。
    约定:命令面错误一律走本函数;exit 0 = 成功且 stdout 无 error 键。"""
    import sys
    print(json.dumps({"error": msg}, ensure_ascii=False))
    sys.exit(code)


def _steal_stale_lock(lock):
    """原子夺取孤儿锁(P1·锁夺取 ABA 修):两进程同判 stale 各自 unlink 时,后 unlink 的
    可能删掉的是【对方刚建的新锁】→ 双持锁。改为 rename 改名占有——os.replace 对同一源
    恒一胜:胜者(rename 成功)删除改名件后去重抢,败者(源已没)直接去重抢,绝无双清。
    返回 True=本进程夺取成功(可立刻重试 O_EXCL),False=别人抢先(同样应重试,别再 unlink)。"""
    tomb = lock.with_name(lock.name + ".steal-%d-%s" % (os.getpid(), os.urandom(2).hex()))
    try:
        os.replace(str(lock), str(tomb))
    except OSError:
        return False          # 锁已被别人夺走/已释放——都不该再动
    try:
        tomb.unlink()
    except Exception:
        pass
    return True


@contextmanager
def json_lock(path, tries=100, sleep=0.03, stale_s=60):
    """共享 JSON 文件的 RMW 独占锁(<path>.lock·O_EXCL 原子创建=恒一胜)。

    用法:with json_lock(BUS/"todos.json"): load→改→_write_json。
    背景(P0·doc-blind 2026-07-28):todos.json / agents.json 多方并发读-改-写会互相覆盖丢数据;
    os.replace 争源本机实证会双胜(pool.py gate①),锁必须 O_EXCL。
    stale 回收:RMW 是毫秒级,锁文件 mtime 超 stale_s 秒=持有者已崩 → 强清重抢(pool 同款思路,
    叶子层不依赖 psutil,用 mtime ttl 判据足够)。抢不到(tries 耗尽)抛 TimeoutError——
    调用方宁可失败重来,不可裸写。"""
    lock = path.parent / (path.name + ".lock")
    fd = None
    for _ in range(tries):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps({"pid": os.getpid(), "acquired_at": _now()}).encode("utf-8"))
            except Exception:
                pass
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_s:   # 孤儿锁(持有者崩了)→ 原子夺取重抢
                    _steal_stale_lock(lock)   # rename 占有恒一胜(ABA 防:绝不 unlink 到别人新锁)
                    continue
            except OSError:
                continue          # 锁刚被释放/瞬时占用 → 立刻重抢
            time.sleep(sleep)
        except OSError:
            time.sleep(sleep)
    if fd is None:
        raise TimeoutError("json_lock 抢锁超时:%s(持有者见锁文件内容)" % lock)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            lock.unlink()
        except Exception:
            pass


def _audit(event, **fields):
    """append-only 审计日志:send/claim/done 各记一行到 BUS/events.jsonl。
    全链路可追溯(replies 被 clear 后仍留痕);best-effort,绝不影响主流程。
    P1(2026-07-28):改 O_APPEND 单次 os.write(与 record_metric 同款)——buffered open("a")
    并发多进程可能拆行互相穿插,审计行一坏整条不可解析。"""
    try:
        rec = {"ts": _now(), "event": event}
        rec.update(fields)
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(BUS / "events.jsonl"), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        pass


# ---------------- BI 资源消耗指标(token / 成本 / 耗时)----------------
# 每个任务跑完(daemon / dispatch)往 BUS/metrics.jsonl 追一行,WebUI BI 模块按角色/全队聚合。
def record_metric(role, task_id, title, status, meta, kind="daemon"):
    """任务完成后记一条资源消耗。meta 来自 claude_run.run_streaming 的第三个返回值。
    best-effort,绝不影响主流程(埋点失败不能让任务回填失败)。"""
    try:
        meta = meta or {}
        rec = {
            "t": time.time(),                       # epoch 秒(给 BI 做时间窗聚合)
            "ts": _now(),                           # 可读本地时间
            "role": role,
            "task_id": task_id,
            "title": (title or "")[:80],            # 标题截断;私有 tab 不走 daemon 任务,不会入账
            "status": status,
            "kind": kind,                           # daemon / dispatch
            "tokens_in": int(meta.get("tokens_in", 0) or 0),
            "tokens_out": int(meta.get("tokens_out", 0) or 0),
            "cache_read": int(meta.get("cache_read", 0) or 0),
            "cache_creation": int(meta.get("cache_creation", 0) or 0),
            "cost_usd": float(meta.get("cost_usd", 0) or 0),
            "duration_ms": int(meta.get("duration_ms", 0) or 0),
            "num_turns": int(meta.get("num_turns", 0) or 0),
        }
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        # O_APPEND 原子追加单行,避免并发 daemon 收尾时互相截断
        fd = os.open(str(METRICS), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def read_metrics(limit=8000):
    """读 metrics.jsonl(尾部 limit 行),返回 dict 列表。文件不在/坏行跳过。"""
    if not METRICS.exists():
        return []
    try:
        lines = METRICS.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-limit:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out
