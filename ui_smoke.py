#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui_smoke —— WebUI 金标准冒烟(UI 2.0 U0 · 2026-07-29,策略同 bus_smoke)。

用途:UI 2.0 每阶段(morphdom 全铺 / 拆 tab / page.py 拆分…)改完跑一次,确认没把
【端点面 / 页面骨架 / 关键行为】改坏。基线只增不减:端点消失 / 原本过的用例挂 = 回归。

前提:常驻 WebUI 在 127.0.0.1:8799(没起先跑 ui/start_ui.ps1,幂等)。
用法:python ui_smoke.py [--json] [--update-baseline] [--port 8799]
"""
import argparse
import json
import os
import re
import socket
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "ui_smoke_baseline.json")

# GET 端点面(routes.py do_GET 全量;/api/events=SSE 单测)。改路由要同步这里+基线。
GET_ENDPOINTS = [
    "/api/state", "/api/health", "/api/tray", "/api/reap", "/api/todos",
    "/api/live", "/api/tasklines", "/api/groups", "/api/mechanisms",
    "/api/role-profiles", "/api/role-profiles-biz", "/api/longrange",
    "/api/bi", "/api/caps", "/api/usage", "/api/dispatch_metrics",
    "/api/sessions", "/api/ctx", "/api/ctxpanel", "/api/waitreg", "/api/prompts",
    "/api/archive", "/api/paths", "/api/storage", "/api/lifecycle",
    "/api/fleet-log", "/api/fleet-state", "/api/route",
]
# 页面骨架锚(page.py 关键面板容器;拆分/morphdom 改造后必须仍在。一面板一锚,U1/U4 回归网·Master数分缺口2)
PAGE_ANCHORS = [
    "fleetBody", "todoBox", "sessBox", "liveBox", "fleetStateBar", "morphdom",   # 核心
    "biKpis", "biRunning", "biSysBody", "biModels",                              # BI
    "usageKpis", "uProj",                                                        # 历史用量(canvas 周边)
    "grpBox", "mechBox", "lrBox", "ctxBody", "wrBody", "promptsBox", "pathsBox",  # 组池/机制/长程/上下文/wait注册表/提示词/路径
    "archRetired", "archResumeBody", "bizRolesBox",                              # 归档/业务角色
    "sendTask", "tviewActive", "tviewHistory",                                   # 派活表单/任务视图
    "dmKpis", "dmRoles", "dmKind", "dmTop",                                      # 派工效率(U2 复活的死码面板)
    "routeChips",                                                                # 建议对口 chip(U3 防误派)
]

# 关键数据端点"必含键"(Master数分缺口1:只验 JSON-parse 会放过 {} 空壳;键=当前真实响应的稳定子集)。
# /api/health 与 /api/ctx 有【冷启 loading 占位契约】(routes.py 注释):{"loading":true} 也算合约内。
MUST_KEYS = {
    "/api/state": ["bus", "fleet", "todos", "now"],
    "/api/bi": ["per_role", "today_usage", "running"],
    "/api/fleet-state": ["state", "icon", "counts", "guard"],
    "/api/health": ["healthy", "selfwake", "queues"],
    "/api/tray": ["ok", "running", "stale"],
    "/api/archive": ["ok", "retired", "resume"],
    "/api/usage": ["totals", "daily", "by_project"],
    "/api/ctx": ["ok", "rows"],
    "/api/waitreg": ["ok", "rows"],
    "/api/sessions": ["ok", "sessions", "live_count", "dead_count"],
}
LOADING_OK = {"/api/health", "/api/ctx"}
# 慢端点单独放宽 HTTP 超时:reap 无缓存·每次 shell agentbus reap --dry-run 做 psutil 全进程扫描
# (裸跑 ~13s,并发压力下更久),其子进程超时=40s → smoke 默认 30s 会先超时误报回归。给它 >40s。
SLOW_EP = {"/api/reap": 50}


def _get(base, path, timeout=30):
    req = urllib.request.Request(base + path, headers={"Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _post(base, path, body, timeout=30):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Origin": "http://127.0.0.1:8799"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def run_tests(base, port):
    results = []

    def add(name, ok, note=""):
        results.append({"name": name, "ok": bool(ok), "note": note})

    # ── 页面骨架 ──
    try:
        st, body = _get(base, "/")
        html = body.decode("utf-8", "replace")
        add("page-200", st == 200, "GET / = %s" % st)
        for a in PAGE_ANCHORS:
            add("anchor-%s" % a, a in html, "页面含 %s" % a)
        # ── tab 白名单闭合校验 ──
        # showTab() 门卫 if(!TAB_NAMES[t])t='fleet' → 任何 data-t 按钮若漏登记 TAB_NAMES
        # 会被静默打回舰队页(端点通/section在/锚点在,但用户点了打不开)。
        # 静态断言:每个 tabbar 的 data-t 按钮都在 TAB_NAMES 有键(P3 waitreg 漏登记复发闸)。
        try:
            m = re.search(r"const TAB_NAMES=\{(.*?)\};", html)
            tab_keys = set(re.findall(r"(?:^|,)\s*'?([A-Za-z_]\w*)'?\s*:", m.group(1))) if m else set()
            btns = set(re.findall(r'class="tab"[^>]*data-t="([^"]+)"', html))
            missing = sorted(b for b in btns if b not in tab_keys)
            add("tabs-in-TAB_NAMES", bool(btns) and not missing,
                ("漏登记: %s" % missing) if missing else "%d 个 tab 按钮全在 TAB_NAMES" % len(btns))
        except Exception as e:
            add("tabs-in-TAB_NAMES", False, repr(e))
    except Exception as e:
        add("page-200", False, repr(e))
        for a in PAGE_ANCHORS:
            add("anchor-%s" % a, False, "页面拉取失败")

    # ── GET 端点面:200 + JSON 可解析;关键数据端点再验必含键(防空壳化回归)──
    for ep in GET_ENDPOINTS:
        name = "GET " + ep
        try:
            st, body = _get(base, ep, timeout=SLOW_EP.get(ep, 30))
            j = json.loads(body.decode("utf-8"))
            add(name, st == 200 and isinstance(j, (dict, list)), "%s bytes=%d" % (st, len(body)))
            if ep in MUST_KEYS and isinstance(j, dict):
                if ep in LOADING_OK and j.get("loading") and len(j) <= 2:
                    add("keys " + ep, True, "冷启 loading 占位(合约内)")
                else:
                    missing = [k for k in MUST_KEYS[ep] if k not in j]
                    add("keys " + ep, not missing, ("缺键: %s" % missing) if missing else "必含键齐")
        except Exception as e:
            add(name, False, repr(e))

    # ── 行为用例(只测无副作用路径)──
    try:
        st, body = _get(base, "/api/no-such-endpoint")
        add("GET-unknown-404", st == 404, str(st))
    except urllib.error.HTTPError as e:
        add("GET-unknown-404", e.code == 404, str(e.code))
    except Exception as e:
        add("GET-unknown-404", False, repr(e))
    st, body = _post(base, "/api/send", {})                      # 缺 to/task → 400 拒
    add("POST-send-empty-400", st == 400, str(st))
    st, body = _post(base, "/api/supervise", {"dry_run": True})  # dry-run 无副作用
    try:
        j = json.loads(body.decode("utf-8"))
        add("POST-supervise-dryrun", st == 200 and isinstance(j, (dict, list)), str(st))
    except Exception as e:
        add("POST-supervise-dryrun", False, repr(e))
    st, body = _post(base, "/api/no-such-endpoint", {})
    add("POST-unknown-404", st == 404, str(st))
    # 其余 POST 的空入参校验面(Master数分缺口3:只挑【实现里空入参必拒·零副作用】的;
    # todos/add 空体会真建"(无标题)"待办、clear_replies 会真清 → 有副作用,不进 smoke)
    for name, path in (("POST-answer-empty", "/api/todos/answer"),
                       ("POST-dispatch-empty", "/api/todos/dispatch"),
                       ("POST-openpath-empty", "/api/open-path"),
                       ("POST-openfile-empty", "/api/open-file"),
                       ("POST-lrdecide-empty", "/api/lr-decide")):
        try:
            st, body = _post(base, path, {})
            j = json.loads(body.decode("utf-8"))
            add(name, st == 200 and j.get("ok") is False, "%s ok=%s" % (st, j.get("ok")))
        except Exception as e:
            add(name, False, repr(e))

    # ── route 行为(U3):空 q 必拒;q=角色名必命中(role 名恒在评分 hay 里=确定性,不赌 desc 措辞)──
    try:
        st, body = _get(base, "/api/route")
        j = json.loads(body.decode("utf-8"))
        add("GET-route-empty", st == 200 and j.get("ok") is False, str(j.get("error", "")))
    except Exception as e:
        add("GET-route-empty", False, repr(e))
    try:
        st, body = _get(base, "/api/route?q=MasterAgent")
        j = json.loads(body.decode("utf-8"))
        hit = any(r.get("role") == "MasterAgent" for r in (j.get("recs") or []))
        add("GET-route-query", st == 200 and j.get("ok") is True and hit, "recs=%d" % len(j.get("recs") or []))
    except Exception as e:
        add("GET-route-query", False, repr(e))

    # ── 会话归属四态(Master数分口径 2026-08-03):role_src 必在枚举内;死会话绝不 bound(不伪造可靠度)──
    try:
        st, body = _get(base, "/api/sessions")
        j = json.loads(body.decode("utf-8"))
        rows = j.get("sessions") or []
        legal = all(s.get("role_src") in ("bound", "ledger", "archive", "inferred", "unknown") for s in rows)
        no_fake = all(not (s.get("state") == "死" and s.get("role_src") == "bound") for s in rows)
        honest = all(bool(s.get("role")) == (s.get("role_src") != "unknown") for s in rows)
        add("sessions-role-4state", st == 200 and legal and no_fake and honest,
            "n=%d legal=%s no_fake=%s honest=%s" % (len(rows), legal, no_fake, honest))
    except Exception as e:
        add("sessions-role-4state", False, repr(e))

    # ── SSE 连通:读到前导注释帧即通(短超时防挂死)──
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.settimeout(5)
        s.sendall(("GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % port).encode())
        buf = b""
        while b"connected" not in buf and len(buf) < 4096:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
        s.close()
        add("SSE-connect", b"connected" in buf, "%d bytes" % len(buf))
    except Exception as e:
        add("SSE-connect", False, repr(e))

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args()
    base = "http://127.0.0.1:%d" % args.port

    try:
        _get(base, "/api/fleet-state", timeout=5)
    except Exception:
        print("✗ WebUI 未在跑(%s)。先起:powershell -File C:\\apphome\\agent_fleet\\ui\\start_ui.ps1" % base)
        sys.exit(2)

    results = run_tests(base, args.port)
    report = {
        "endpoints": GET_ENDPOINTS,
        "anchors": PAGE_ANCHORS,
        "tests": results,
        "test_count": len(results),
        "pass": sum(1 for r in results if r["ok"]),
    }

    regressions = []
    if args.update_baseline:
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    elif os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            base_rep = json.load(f)
        gone_ep = set(base_rep.get("endpoints", [])) - set(GET_ENDPOINTS)
        gone_an = set(base_rep.get("anchors", [])) - set(PAGE_ANCHORS)
        if gone_ep:
            regressions.append("端点消失: %s" % sorted(gone_ep))
        if gone_an:
            regressions.append("页面锚点消失: %s" % sorted(gone_an))
        base_pass = {t["name"] for t in base_rep.get("tests", []) if t["ok"]}
        now_fail = {t["name"] for t in results if not t["ok"]}
        broke = sorted(base_pass & now_fail)
        if broke:
            regressions.append("原本过、现在挂: %s" % broke)
        report["baseline_compare"] = {"regressions": regressions}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("端点面: %d GET + 行为/SSE 用例" % len(GET_ENDPOINTS))
        print("冒烟: %d/%d 过" % (report["pass"], report["test_count"]))
        for r in results:
            if not r["ok"]:
                print("  ✗ %-24s %s" % (r["name"], r["note"]))
        if args.update_baseline:
            print("✓ 基线已写 %s" % BASELINE)
        elif regressions:
            print("\n⚠ 回归:")
            for rg in regressions:
                print("  " + rg)
        elif os.path.exists(BASELINE):
            print("✓ 无回归(对比基线)")
    sys.exit(1 if (report["pass"] < report["test_count"] or regressions) else 0)


if __name__ == "__main__":
    main()
