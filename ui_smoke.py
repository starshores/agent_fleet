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
    "/api/sessions", "/api/ctx", "/api/ctxpanel", "/api/prompts",
    "/api/archive", "/api/paths", "/api/storage", "/api/lifecycle",
    "/api/fleet-log", "/api/fleet-state",
]
# 页面骨架锚(page.py 关键面板;拆分/morphdom 改造后必须仍在)
PAGE_ANCHORS = ["fleetBody", "todoBox", "sessBox", "liveBox", "fleetStateBar", "morphdom"]


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
    except Exception as e:
        add("page-200", False, repr(e))
        for a in PAGE_ANCHORS:
            add("anchor-%s" % a, False, "页面拉取失败")

    # ── GET 端点面:200 + JSON 可解析(loading 占位也算 200+JSON,口径宽进防 flap)──
    for ep in GET_ENDPOINTS:
        name = "GET " + ep
        try:
            st, body = _get(base, ep)
            j = json.loads(body.decode("utf-8"))
            add(name, st == 200 and isinstance(j, (dict, list)), "%s bytes=%d" % (st, len(body)))
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
