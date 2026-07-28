#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.routes —— WebUI HTTP 路由层(BUS 2.0 P4 · Phase D · 2026-07-20)。

抽自 server.py 的 class Handler(do_GET/do_POST + 路由分发)。契约铁律(P4):纯搬运,
路由/端点响应/口径不变。Handler 裸名调用 api 层数据产出(build_*/get_*/compute_*/send_task/
todo_*/_change_cv/_bump 等)+ INDEX_HTML,故本模块以 facade 方式把 api 全部名字并入命名空间
+ from page import INDEX_HTML。server.py 的 main() 以 from routes import Handler 装配。
"""
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from common import ABDIR, ab, BUS  # noqa: F401
import api
for _k, _v in vars(api).items():  # facade:Handler 裸名调 api 全部名字(含 _ 前缀+缓存)
    if not _k.startswith("__"):
        globals()[_k] = _v
from page import INDEX_HTML  # noqa: F401


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
        elif path == "/api/role-profiles":
            self._send(200, build_role_profiles())
        elif path == "/api/longrange":
            self._send(200, build_longrange())
        elif path == "/api/prompts":
            # 提示词审核:手动刷新时直接读小文件(无缓存/无 poller);只读
            self._send(200, build_prompts())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if origin and origin not in ("http://127.0.0.1:8799", "http://localhost:8799"):
            self._send(403, {"error": "forbidden"})
            return
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
        elif path == "/api/open-file":
            # 📂开文件:白名单硬约束在 open_file 内(只允许 build_prompts openable 的真实文件·127.0.0.1 only)
            b = self._body_json()
            self._send(200, open_file((b.get("path") or "").strip()))
        else:
            self._send(404, {"error": "not found"})

    def _sse(self):
        # 直接引用 api 模块属性，绕过 facade int 定格副本(facade 拷贝时 _change_ver=0 定格，api._bump() 只改 api 侧)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            with api._change_cv:
                last = api._change_ver
            while True:
                with api._change_cv:
                    got = api._change_cv.wait_for(lambda: api._change_ver != last, timeout=15)
                    if got:
                        last = api._change_ver
                if got:
                    self.wfile.write(b"data: change\n\n")
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
