#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多 agent 管理 WebUI — agent bus 的可视化控制台。

本机内部工具,只绑 127.0.0.1。复用 agentbus.py 的数据模型与函数,不另写一套逻辑。

起服务:  python C:/apphome/agent_fleet/ui/server.py
          可选:  --port 8799  --host 127.0.0.1

功能:
  A 舰队总览(同 who):verdict / claude_pid / 心跳age / cwd + 在跑但未认领进程
  B 队列(同 list):每 role inbox/processing/done 计数,可点开看任务 JSON
  C 派活:表单 → 复用 agentbus send --from Master
  D 回填查看:读 <role>/done(默认 Master),展示结果,可清除
  E 自动刷新:watchdog 文件事件 → SSE 推送;客户端可调轮询兜底

隐私模型(用户 2026-07-02 定):【只有显式标记(pid_tags.json private=true)的才藏,其余一律如实显示】。
  - private=true = 用户主动 opt-in 隐藏(如截图/投屏给外部那刻):只显 pid+label🔒,不探 cwd。
  - 未标记 / 已标注非 private:如实显 cwd/proj/标题(自看控制台绑 127.0.0.1、唯一观众=所有者本人,
    无外部方→默认不脱敏;掩码只丢信息,如看不出哪个个人会话吃上下文)。旧「自动掩码个人会话」已去除。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ---- 共享基础层(BUS 2.0 P4 · Phase A):agentbus 引导 + 基础 helper 抽到 ui/common.py ----
# ABDIR/ab/BUS 由 common 单一源引导;此处 re-export 保对外兼容(子进程 import server; server.* 路径,
# 及本模块内以裸名调用)。common 在 ui/ 目录,子进程已把 ui/ 塞进 sys.path 故可达。
from common import ABDIR, ab, BUS  # noqa: E402,F401

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# =============================================================
#  数据采集 —— 全部复用 agentbus 的 helper,不复制其逻辑
# =============================================================

# 基础 helper 已抽到 ui/common.py(P4·Phase A),re-export 保裸名调用 + 子进程 server.* 兼容。
from common import (  # noqa: E402,F401
    _roles, _count, _read_tasks, _safe_cwd, _turn_state, _vclass_of,
)


# =============================================================
#  数据/API 层 —— 全部 build_*/get_*/compute_* + 缓存/refresher/notify/send/todos 抽到 ui/api.py(P4·Phase C)。
#  server.py 变 facade:import api 后把 api 全部名字并入本模块命名空间,保:
#   ① Handler/main 裸名调用不变 ② 子进程 import server; server.compute_fleet() 兼容 ③ SSE 用的 _change_cv 等下划线名/缓存对象也在。
# =============================================================
import api  # noqa: E402
for _k, _v in vars(api).items():  # facade 完整 re-export(含 _ 前缀名与缓存对象)
    if not _k.startswith("__"):
        globals()[_k] = _v


# =============================================================
#  HTTP
# =============================================================
# INDEX_HTML(前端页面 ~2159 行)抽到 ui/page.py(P4·Phase B);re-export 保 server.INDEX_HTML 兼容 + do_GET 裸名引用。
from page import INDEX_HTML  # noqa: E402,F401


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # 静默,避免刷屏

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/" or path == "/index.html":
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/api/state":
            q = parse_qs(u.query)
            role = (q.get("reply_role", ["Master"])[0]) or "Master"
            fresh = (q.get("fresh", ["0"])[0]) in ("1", "true")
            self._send(200, build_state(role, fresh=fresh))
        elif path == "/api/events":
            self._sse()
        elif path == "/api/health":
            # 只读后台缓存(子进程刷新);冷启未就绪回 loading 占位,绝不现扫
            d = get_health()
            self._send(200, d if d is not None else {"loading": True})
        elif path == "/api/tray":
            # 运转指示计数(bus_tray.py 系统托盘 + WebUI 共用·单一源=fleet 缓存口径)
            self._send(200, bus_tray_counts())
        elif path == "/api/reap":
            # 孤儿巡检:只读 dry-run,复用 agentbus reap --dry-run
            self._send(200, reap_dryrun())
        elif path == "/api/todos":
            self._send(200, {"ok": True, "todos": _sorted_todos()})
        elif path == "/api/live":
            q = parse_qs(u.query)
            self._send(200, read_live(role=(q.get("role", [None])[0]),
                                      dispatch_id=(q.get("id", [None])[0])))
        elif path == "/api/bi":
            self._send(200, build_bi())
        elif path == "/api/caps":
            # 能力池/caps:capabilities.json + agents.json 实时只读(不改 caps)
            self._send(200, build_caps())
        elif path == "/api/usage":
            q = parse_qs(u.query)
            fresh = (q.get("fresh", ["0"])[0]) in ("1", "true")
            self._send(200, get_usage(force=fresh))
        elif path == "/api/dispatch_metrics":
            q = parse_qs(u.query)
            fresh = (q.get("fresh", ["0"])[0]) in ("1", "true")
            self._send(200, get_dispatch_metrics(force=fresh))
        elif path == "/api/sessions":
            self._send(200, read_sessions())
        elif path == "/api/ctx":
            # 只读后台缓存(bounded jsonl 尾读);冷启未就绪回 loading 占位,绝不现读
            d = get_ctx()
            self._send(200, d if d is not None else {"loading": True})
        elif path == "/api/ctxpanel":
            # 纯上下文预算看板:内存 ctx_cache(+who_cache 仅校验 role 标签),请求路径零扫描/零读盘
            self._send(200, build_ctxpanel())
        elif path == "/api/tasklines":
            self._send(200, build_tasklines())
        elif path == "/api/groups":
            self._send(200, build_groups())
        elif path == "/api/mechanisms":
            self._send(200, build_mechanisms())
        elif path == "/api/longrange":
            self._send(200, build_longrange())
        elif path == "/api/prompts":
            # 提示词审核:手动刷新时直接读小文件(无缓存/无 poller);只读
            self._send(200, build_prompts())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/send":
            b = self._body_json()
            to = (b.get("to") or "").strip()
            task = (b.get("task") or "").strip()
            if not to or not task:
                self._send(400, {"ok": False, "error": "to/task 必填"})
                return
            notify = (b.get("notify") or "auto").strip()
            if notify not in ("auto", "always", "never"):
                notify = "auto"
            try:
                tid = send_task(to, b.get("title", ""), task,
                                requires_approval=bool(b.get("requires_approval")),
                                notify=notify)
                self._send(200, {"ok": True, "id": tid})
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)})
        elif path == "/api/supervise":
            # 手动触发 daemon 自愈:复用 agentbus supervise(可 dry-run)
            b = self._body_json()
            self._send(200, run_supervise(dry_run=bool(b.get("dry_run"))))
        elif path == "/api/clear_replies":
            b = self._body_json()
            role = (b.get("role") or "Master").strip()
            n = clear_replies(role, b.get("file"))
            self._send(200, {"ok": True, "cleared": n})
        elif path == "/api/todos/add":
            self._send(200, todo_add(self._body_json()))
        elif path == "/api/todos/update":
            self._send(200, todo_update(self._body_json()))
        elif path == "/api/todos/delete":
            self._send(200, todo_delete(self._body_json()))
        elif path == "/api/todos/dispatch":
            self._send(200, todo_dispatch(self._body_json()))
        elif path == "/api/todos/answer":
            self._send(200, todo_answer(self._body_json()))
        elif path == "/api/lr-decide":
            b = self._body_json()
            self._send(200, resolve_lr_batch((b.get("task_id") or "").strip(), b.get("approvals") or {}))
        else:
            self._send(404, {"error": "not found"})

    def _sse(self):
        global _change_ver
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            with _change_cv:
                last = _change_ver
            while True:
                with _change_cv:
                    # 最长 15s 等变更;到点发心跳保活
                    got = _change_cv.wait_for(lambda: _change_ver != last, timeout=15)
                    if got:
                        last = _change_ver
                if got:
                    self.wfile.write(b"data: change\n\n")
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def start_supervisor(interval=90):
    """后台 supervisor 线程:周期跑 `agentbus supervise`,自愈 fleet.json 里已死的 daemon。
    复用常驻 WebUI 进程当宿主(它本身已登录自启 + WmiPrvSE 脱壳常驻),不另起需要再看护的进程。
    用 Event.wait 计时(非 sleep 轮询忙等),进程退出即停。"""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "supervise"],
                               capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
            except Exception:
                pass

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[supervise] fleet 自愈线程已启动,间隔 %ds(读 fleet.json,重启已死 daemon)" % interval)
    return t


def start_selfwake_guard(interval=180):
    """常驻自醒探测网(Master 令·B):每 ~interval 秒后台跑 `ab selfwake-guard` ——
    它扫掉自醒(stale)的 agent → dedup 投「重挂」提醒到其 inbox + WinRT toast 报警。
    由 WebUI 8799 always-on 驱动,不依赖任何 agent 醒着(把「18h 没人发现掉自醒」变「3 分钟内报警」)。
    探测逻辑全在 agentbus(cmd_selfwake_guard),此处只周期调它。
    · daemon=True 随 server 退出;· 首轮先 sleep(避开重启后 agent 尚未重挂的假警);
    · 失败静默 try/except,绝不掀翻 8799;· 子进程带 CREATE_NO_WINDOW 免弹窗(见记忆 popen-claude-no-window)。"""
    def loop():
        while True:
            time.sleep(interval)
            try:
                subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "selfwake-guard"],
                               capture_output=True, timeout=60, creationflags=_NO_WINDOW)
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--no-supervise", action="store_true", help="关闭 fleet daemon 自愈线程")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost"):
        print("拒绝:内部工具只允许绑 127.0.0.1 /", args.host)
        sys.exit(2)

    start_watcher()
    start_fleet_refresher(20)         # 唯一舰队刷新者:20s 子进程扫描刷新 _fleet_cache(请求路径零扫描)
    start_health_refresher(25)        # 唯一健康刷新者:25s 子进程 health --json 刷新 _health_cache(请求路径零扫描)
    start_ctx_refresher(12)           # 唯一上下文占用刷新者:12s bounded jsonl 尾读刷新 _ctx_cache(1M 撞墙预警,请求路径零现读)
    start_who_refresher(20)           # 唯一 who 刷新者:20s 子进程 who --json 刷新 _who_cache(供上下文模块校验 role 在岗,请求路径零扫描)
    start_status_pusher(15)           # daemon→agent 定时通信:15s 刷新 fleet_status.json(复用上面缓存的扫描,不再自扫)
    if not args.no_supervise:
        start_supervisor()
    start_selfwake_guard(180)         # 常驻自醒探测网(Master令·B):180s 跑 ab selfwake-guard,掉自醒→inbox提醒+toast(8799 always-on 驱动,不依赖 agent 醒着)
    # 预热缓存(后台线程,不阻塞启动):
    #  · build_bi() 跑一遍 → 填充 _proc_cache(psutil.Process 对象 + cpu_percent 基线),
    #    使【首个真实 /api/bi】~220ms 而非冷启 ~4.5s(_proc_cache 无 TTL,长期受益,首屏 BI 关键)。
    #  · compute_fleet_cached() → 首个 /api/state 若在 3s TTL 内命中热缓存(快速重连时受益)。
    #  · 舰队缓存由 start_fleet_refresher 首轮立即填充(~4s),无需在此预热。
    def _prewarm():
        try:
            build_bi()
        except Exception:
            pass
    threading.Thread(target=_prewarm, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    url = "http://%s:%d/" % (args.host, args.port)
    print("=" * 56)
    print(" Agent Bus 控制台已启动")
    print("   ", url)
    print("    BUS =", BUS)
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止。")
        srv.shutdown()


if __name__ == "__main__":
    main()
