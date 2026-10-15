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
        try:
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

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
            role = (q.get("reply_role", ["__all__"])[0]) or "__all__"
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
        elif path == "/api/waitreg":
            # P3 wait 注册表观测:聚合各 role wait.reg.json(请求路径直读小文件·手动刷新/20s 轮询)
            self._send(200, build_waitreg())
        elif path == "/api/tasklines":
            self._send(200, build_tasklines())
        elif path == "/api/groups":
            self._send(200, build_groups())
        elif path == "/api/mechanisms":
            self._send(200, build_mechanisms())
        elif path == "/api/role-profiles":
            self._send(200, build_role_profiles())
        elif path == "/api/role-profiles-biz":
            self._send(200, build_role_profiles_biz())
        elif path == "/api/longrange":
            self._send(200, build_longrange())
        elif path == "/api/prompts":
            # 提示词审核:手动刷新时直接读小文件(无缓存/无 poller);只读
            self._send(200, build_prompts())
        elif path == "/api/paths":
            # 路径注册表:path_registry.json + 健康校验(请求路径直读·手动刷新)
            self._send(200, api.build_paths())
        elif path == "/api/guide":
            # 📖操作说明:渲染 repo 根 BUS_GUIDE.md(单一源·内容 MasterAgent 维护·只读)
            self._send(200, build_guide())
        elif path == "/api/user-absence":
            # 🙋用户确认点/缺席(用户侧自醒对偶·ab user-absence-check)·只读
            self._send(200, build_user_absence())
        elif path == "/api/agent-profile":
            # 📇per-agent数据画像(L3·硬指标·中性非KPI·Master数分红线)·12s TTL缓存·days窗可调
            q = parse_qs(u.query)
            self._send(200, build_agent_profile(days=(q.get("days", ["30"])[0])))
        elif path == "/api/storage":
            # 存储全景:三分格局+项目根分布+违规候选(长 TTL 缓存·full du 数秒·别进 SSE)
            q = parse_qs(u.query)
            fresh = (q.get("fresh", ["0"])[0]) in ("1", "true")
            self._send(200, build_storage(force=fresh))
        elif path == "/api/lifecycle":
            # 角色全生命周期:agents.json + retired_roster.json + fleet_log retire/revive 事件
            self._send(200, build_lifecycle())
        elif path == "/api/fleet-log":
            # 舰队日志:fleet_log.jsonl 倒序(请求路径直读·手动刷新)
            self._send(200, build_fleet_log())
        elif path == "/api/fleet-state":
            # 舰队整体状态(fleet_state_rules.json 判定·顶栏徽标数据源)
            self._send(200, get_fleet_state())
        elif path == "/api/route":
            # 派活建议对口(U3 防误派):bus.roles.route_scores 单一源,只读
            q = parse_qs(u.query).get("q", [""])[0]
            self._send(200, build_route(q))
        elif path == "/api/asr/devices":
            # 🎙语音转写:列音频源(loopback 扬声器/麦克风)+ 可用模型(调 asr_venv list_devices)
            self._send(200, build_asr_devices())
        elif path == "/api/asr/status":
            # 转写运行态 + 会话 + 服务自报状态(轮询用)
            self._send(200, build_asr_status())
        elif path == "/api/asr/lines":
            # 当前会话转录行(since=已收行数,取增量)
            since = parse_qs(u.query).get("since", ["0"])[0]
            self._send(200, build_asr_lines(since=since))
        elif path == "/api/asr/audio":
            # 原声片段回放(f=lines 里的 audio 相对路径;路径校验在 asr_audio 内)
            f = parse_qs(u.query).get("f", [""])[0]
            r = asr_audio(f)
            if r is None:
                self._send(404, {"error": "audio not found"})
            else:
                self._send(200, r[0], ctype=r[1])
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
        elif path == "/api/role-model-prefs":
            self._send(200, update_role_model_prefs(self._body_json()))
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
        elif path == "/api/open-path":
            # 📂路径页 explorer 打开:127.0.0.1 only(Origin 头已在 do_POST 头部检查)
            b = self._body_json()
            self._send(200, api.open_path((b.get("path") or "").strip()))
        elif path == "/api/done":
            # 手动 done 一条任务(舰队总览 done 按钮 · 127.0.0.1 only)
            b = self._body_json()
            task_id = (b.get("task_id") or "").strip()
            role = (b.get("role") or "").strip()
            result = (b.get("result") or "手动done").strip()
            status = (b.get("status") or "ok").strip()
            try:
                self._send(200, done_task(task_id, role, result, status))
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)})
        elif path == "/api/asr/start":
            # 🎙启动语音转写子进程(127.0.0.1 only·Origin 已在头部校验)
            b = self._body_json()
            self._send(200, start_asr(source=(b.get("source") or "loopback"),
                                      name=(b.get("name") or ""), model=(b.get("model") or ""),
                                      silence_ms=b.get("silence_ms", 500)))
        elif path == "/api/asr/stop":
            self._send(200, stop_asr())
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
