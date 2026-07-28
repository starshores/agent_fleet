# -*- coding: utf-8 -*-
"""
external_canvas.py — 焦点-free 操作云表格(canvas)。不 bring_to_front、不抢用户焦点。

核心发现(2026-07-07 验证):CDP 的 click/type/press 是【页面级派发】(发渲染进程),
不是 OS 输入注入——所以【不 bring_to_front】就能在后台标签驱动 sheet,不会把 Edge 弹前台、
不会抢用户正在打字的焦点。之前"抢焦点误输入"的元凶就是 bring_to_front。
唯一真需要 OS 焦点的是 clipboard.read()(Ctrl+C 读)——所以【读】改用截图(视觉),不用剪贴板。

命令:
  # 焦点-free 写单元格(名称框element.click聚焦→公式栏键入,不bring_to_front)
  python external_canvas.py write --sheet SHEET_ID --cell D18 --value "文本" [--doc-token TOKEN] [--who Master] [--no-lock]
  # 焦点-free 区域截图(供视觉读值/判色;不bring_to_front、不clipboard)
  python external_canvas.py shot --sheet SHEET_ID --goto A5 --out C:\path\shot.png [--doc-token ...]
      --goto 先把某格滚入视图(名称框定位),再整页截图。

铁律:绝不 b.close();写入前读回该格核对由调用方/视觉负责。
"""
import os, sys, json, argparse, subprocess
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
CDP = "http://localhost:9222"
BASE = os.environ.get("EXTERNAL_DOCS_BASE", "https://example.com")  # 按部署环境配置(不把具体租户写进基座)
DEFAULT_DOC = os.environ.get("EXTERNAL_DOC_TOKEN", "example-doc-token")  # 默认表格 token 可由部署环境覆盖


def _lock(action, who):
    """edge 焦点互斥锁。acquire 失败【硬退出】(P0·2026-07-28):edge-acquire 恒 exit 0、
    结果在 stdout JSON——之前只捕异常不看结果,抢锁失败照操作 Edge=互斥形同虚设=串台病根。"""
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "agentbus.py"),
                            f"edge-{action}", "--who", who] + (["--timeout", "60"] if action == "acquire" else []),
                           capture_output=True, text=True, timeout=70)
        if action == "acquire":
            ok = False
            try:
                ok = bool(json.loads((r.stdout or "").strip().splitlines()[-1]).get("acquired"))
            except Exception:
                ok = False
            if not ok:
                print(f"[fatal] edge-acquire 抢锁失败(被占/超时/异常)→ 禁止无锁操作 Edge,退出。"
                      f"稍后重试或 `ab edge-status` 看持有者。stdout={(r.stdout or '').strip()[:200]}", file=sys.stderr)
                sys.exit(3)
    except SystemExit:
        raise
    except Exception as e:
        if action == "acquire":
            print(f"[fatal] edge-acquire 调用异常:{e} —— 抢锁失败禁继续(P0),退出。", file=sys.stderr)
            sys.exit(3)
        print(f"[warn] edge-{action} 异常: {e}", file=sys.stderr)


def _find_sheet(ctx, doc_token, sheet_id):
    url = f"{BASE}/sheets/{doc_token}?sheet={sheet_id}"
    for pp in ctx.pages:
        if f"/sheets/{doc_token}" in pp.url:
            if f"sheet={sheet_id}" not in pp.url:
                pp.goto(url, wait_until="domcontentloaded", timeout=30000); pp.wait_for_timeout(2500)
            return pp
    for pp in ctx.pages:
        if "/sheets/" in pp.url:
            pp.goto(url, wait_until="domcontentloaded", timeout=30000); pp.wait_for_timeout(2500)
            return pp
    return None


def _namebox(pg):
    return pg.query_selector(".formulabar__map input.ud__native-input") or pg.query_selector(".formulabar__map input")


def _goto_cell(pg, cell):
    """名称框 element.click 聚焦(不 bring_to_front)→ 键入地址 → Enter。返回是否到位。"""
    box = _namebox(pg)
    if not box:
        return False
    box.click(); pg.wait_for_timeout(180)
    box.press("Control+a"); box.press("Delete"); pg.wait_for_timeout(80)
    box.type(cell); pg.wait_for_timeout(120)
    box.press("Enter"); pg.wait_for_timeout(500)
    cur = pg.evaluate("""()=>{const b=document.querySelector('.formulabar__map input');return b?b.value:''}""")
    return cur.replace("$", "").upper() == cell.replace("$", "").upper()


def write(doc_token, sheet_id, cell, value):
    res = {"sheet": sheet_id, "cell": cell, "ok": False, "readback": None, "error": None}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP); ctx = b.contexts[0]
        pg = _find_sheet(ctx, doc_token, sheet_id)
        if not pg:
            res["error"] = "NO_SHEET"; print(json.dumps(res, ensure_ascii=False)); return 3
        # 不 bring_to_front —— 全程页面级 CDP
        if not _goto_cell(pg, cell):
            res["error"] = f"定位{cell}失败"; print(json.dumps(res, ensure_ascii=False)); return 4
        fb = pg.query_selector(".formulabar__inputarea")
        if not fb:
            res["error"] = "无公式栏编辑区.formulabar__inputarea"; print(json.dumps(res, ensure_ascii=False)); return 5
        fb.click(); pg.wait_for_timeout(200)
        pg.keyboard.press("Control+a"); pg.keyboard.press("Delete"); pg.wait_for_timeout(120)
        pg.keyboard.type(value); pg.wait_for_timeout(200)
        pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
        # 读回:重新定位该格读公式栏
        _goto_cell(pg, cell)
        rb = pg.evaluate("""()=>{const fb=document.querySelector('.formulabar__inputarea');return fb?fb.innerText.trim():''}""")
        res["readback"] = rb
        res["ok"] = (rb == value)
    print(json.dumps(res, ensure_ascii=False)); return 0 if res["ok"] else 6


def shot(doc_token, sheet_id, goto, out):
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP); ctx = b.contexts[0]
        pg = _find_sheet(ctx, doc_token, sheet_id)
        if not pg:
            print(json.dumps({"error": "NO_SHEET"}, ensure_ascii=False)); return 3
        if goto:
            _goto_cell(pg, goto)
            try:
                pg.keyboard.press("Escape")
            except Exception:
                pass
            pg.wait_for_timeout(300)
        pg.screenshot(path=out)   # screenshot 不需 OS 焦点
        print(json.dumps({"ok": True, "out": out, "goto": goto}, ensure_ascii=False)); return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--sheet", required=True); w.add_argument("--cell", required=True); w.add_argument("--value", required=True)
    w.add_argument("--doc-token", default=DEFAULT_DOC); w.add_argument("--who", default="Master"); w.add_argument("--no-lock", action="store_true")
    s = sub.add_parser("shot")
    s.add_argument("--sheet", required=True); s.add_argument("--goto", default=""); s.add_argument("--out", required=True)
    s.add_argument("--doc-token", default=DEFAULT_DOC); s.add_argument("--who", default="Master"); s.add_argument("--no-lock", action="store_true")
    a = ap.parse_args()
    lock = not getattr(a, "no_lock", False)
    if lock: _lock("acquire", a.who)
    try:
        if a.cmd == "write": rc = write(a.doc_token, a.sheet, a.cell, a.value)
        else: rc = shot(a.doc_token, a.sheet, a.goto, a.out)
    finally:
        if lock: _lock("release", a.who)
    sys.exit(rc)


if __name__ == "__main__":
    main()
