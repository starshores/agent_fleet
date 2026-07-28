# -*- coding: utf-8 -*-
"""bus.watch —— watchdog 事件监听公共样板(P1·2026-07-28 抽公共)。

背景:core._wait_core / core._wait_for_reply / edge._wait_file_gone / pool.fanout-collect
四处手写 Observer+Handler 复制体——fanout-collect 曾因复制时丢 `import threading` 直接
NameError 崩(P0 e7f1244),样板复制=每处都可能再丢一块。收敛为单一原语。

用法:
    ev, stop, ok = arm_watch([dir1, dir2], suffix=".json")
    try:
        if ok:
            ev.wait(timeout=...)     # 命中(目录下 suffix 文件事件)即 set;调用方自管 clear/续等
        else:
            ...                      # Observer 起不来(watchdog 坏/资源限)→ 调用方降级(如轮询阻塞)
    finally:
        stop()                       # 幂等清场

参数:on=事件类型子集(created/moved/deleted);moved_field=moved 事件看哪端
(dest_path=移入本目录=新任务;src_path=移走=文件消失,edge 等锁释放用)。
本模块绝不抛:watchdog 缺失/启动失败都以 ok=False 返回。叶子层,仅依赖标准库。
"""
import threading
from pathlib import Path


def arm_watch(dirs, suffix=".json", on=("created", "moved"), moved_field="dest_path"):
    """监听 dirs 下 suffix 文件事件 → set Event。返回 (ev, stop, ok)。"""
    ev = threading.Event()
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except Exception:
        return ev, (lambda: None), False

    class H(FileSystemEventHandler):
        def on_any_event(self, e):
            et = getattr(e, "event_type", "")
            if et not in on:
                return
            field = moved_field if et == "moved" else "src_path"
            if str(getattr(e, field, "")).endswith(suffix):
                ev.set()

    obs = Observer()
    scheduled = False
    for d in dirs:
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            obs.schedule(H(), str(d), recursive=False)
            scheduled = True
        except Exception:
            pass
    ok = False
    if scheduled:
        try:
            obs.start()
            ok = True
        except Exception:
            ok = False

    def stop():
        if ok:
            try:
                obs.stop()
                obs.join()
            except Exception:
                pass

    return ev, stop, ok
