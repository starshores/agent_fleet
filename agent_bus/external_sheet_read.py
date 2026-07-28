# -*- coding: utf-8 -*-
"""
external_sheet_read.py — 经 Edge CDP 只读拉回云表格某 sheet 的区域内容(可复用 CLI)。

配套 external_read.py(读聊天)/ external_msg.py(发消息)。本工具只读表格:
定位 workbook+sheet -> 名称框选区 -> Ctrl+C -> 读 text/html 剪贴板 -> 按 <tr>/<td> 解析。
用 HTML 剪贴板(非纯文本 \n 切)以正确容纳带换行的多行单元格,并顺带取底色(判灰底)。
绝不写/改任何格,绝不 b.close() 用户的 Edge(只 disconnect)。

用法:
  python external_sheet_read.py read --sheet SHEET_ID --range A4:Q65 [--json] [--who Master] [--no-lock]
  --sheet     目标 sheet id(URL 的 ?sheet= 值)。
  --range     区域,如 A4:Q65(含表头行更便于对齐);默认 A1:Z200。
  --doc-token workbook token,默认由 EXTERNAL_DOC_TOKEN 配置。
  --json      结构化输出 {header, rows:[[cell...]], bg:[[color...]]};默认打印 TSV 表格。
  --with-bg   额外打印/带出每格底色(判灰底/黄标)。
  --who       edge 锁持有者标识(默认 Master)。
  --no-lock   调用方已持锁则跳过自抢/放。

退出码:0=成功;3=没开云表格页;4=选区/复制失败;5=剪贴板空/解析不出表。
注意:行号坑——只框数据区(如 A4 起),别把 R1-R3 多行注释标题带纳入(其内嵌换行会干扰);
      本工具走 <td> 边界解析,数据区多行单元格安全。
"""
import os, sys, json, argparse, subprocess, re
from html.parser import HTMLParser
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


class _TableParser(HTMLParser):
    """解析云文档剪贴板 text/html:抽取 <tr>/<td> 文本 + 背景色。<br> 转 \n。"""
    def __init__(self):
        super().__init__()
        self.rows = []          # [[text,...],...]
        self.bgs = []           # [[color,...],...]
        self._row = None
        self._bgrow = None
        self._cell = None
        self._in_td = False
    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []; self._bgrow = []
        elif tag in ("td", "th") and self._row is not None:
            self._in_td = True; self._cell = []
            d = dict(attrs)
            bg = ""
            style = d.get("style", "") or ""
            m = re.search(r"background(?:-color)?\s*:\s*([^;]+)", style, re.I)
            if m:
                bg = m.group(1).strip()
            elif d.get("bgcolor"):
                bg = d["bgcolor"].strip()
            self._bgrow.append(bg)
            # colspan 展开:补空格保持列对齐
            self._colspan = int(d.get("colspan", "1") or "1")
        elif tag == "br" and self._in_td:
            self._cell.append("\n")
    def handle_data(self, data):
        if self._in_td:
            self._cell.append(data)
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_td:
            txt = "".join(self._cell).strip()
            self._row.append(txt)
            for _ in range(max(0, self._colspan - 1)):
                self._row.append(""); self._bgrow.append("")
            self._in_td = False; self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row); self.bgs.append(self._bgrow)
            self._row = None; self._bgrow = None


_CLIP_HTML_JS = r"""
async () => {
  try {
    const items = await navigator.clipboard.read();
    for (const it of items) {
      if (it.types.includes('text/html')) {
        const b = await it.getType('text/html');
        return await b.text();
      }
    }
  } catch (e) { return '__ERR__' + (e && e.message ? e.message : e); }
  return null;
}
"""


def _find_sheet_page(ctx, doc_token, sheet_id):
    url = f"{BASE}/sheets/{doc_token}?sheet={sheet_id}"
    # 已开着该 workbook 的页 -> 导航到目标 sheet;否则找任一 sheets 页导过去
    for pp in ctx.pages:
        if f"/sheets/{doc_token}" in pp.url:
            if f"sheet={sheet_id}" not in pp.url:
                pp.goto(url, wait_until="domcontentloaded", timeout=30000)
                pp.wait_for_timeout(2500)
            return pp
    for pp in ctx.pages:
        if "/sheets/" in pp.url:
            pp.goto(url, wait_until="domcontentloaded", timeout=30000)
            pp.wait_for_timeout(2500)
            return pp
    return None


def _focus_grid(pg):
    """点一下网格 canvas 激活焦点(冷页面 grid 无焦点时 Ctrl+C 抓不到选区)。
    faster-single-canvas 是主网格;点其内部一点(会选中某格,随后名称框重选目标区覆盖)。"""
    try:
        rect = pg.evaluate("""()=>{const c=document.querySelector('canvas.faster-single-canvas')||
            [...document.querySelectorAll('canvas')].sort((a,b)=>b.getBoundingClientRect().width*b.getBoundingClientRect().height-a.getBoundingClientRect().width*a.getBoundingClientRect().height)[0];
            if(!c)return null;const b=c.getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height};}""")
        if rect and rect["w"] > 100:
            pg.mouse.click(rect["x"] + min(300, rect["w"] / 3), rect["y"] + min(230, rect["h"] / 3))
            pg.wait_for_timeout(350)
    except Exception:
        pass


def _select_range(pg, rng):
    """先激活网格焦点 -> 名称框输入区域 -> Enter 选中。返回 True/False。"""
    _focus_grid(pg)
    box = pg.query_selector(".formulabar__map input.ud__native-input") or \
          pg.query_selector(".formulabar__map input")
    if not box:
        return False
    box.click(); pg.wait_for_timeout(150)
    box.fill(""); pg.wait_for_timeout(80)
    box.type(rng); pg.wait_for_timeout(120)
    pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
    return True


def read(doc_token, sheet_id, rng, as_json, with_bg):
    res = {"sheet": sheet_id, "range": rng, "nrows": 0, "header": [], "rows": [], "bg": [], "error": None}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        try:
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        except Exception:
            pass
        pg = _find_sheet_page(ctx, doc_token, sheet_id)
        if not pg:
            res["error"] = "NO_SHEET (Edge 里没开云表格页)"
            print(json.dumps(res, ensure_ascii=False)); return 3
        pg.bring_to_front(); pg.wait_for_timeout(400)
        if not _select_range(pg, rng):
            res["error"] = "选区失败(找不到名称框)"
            print(json.dumps(res, ensure_ascii=False)); return 4
        pg.keyboard.press("Control+c"); pg.wait_for_timeout(700)
        html = pg.evaluate(_CLIP_HTML_JS)
        if not html or (isinstance(html, str) and html.startswith("__ERR__")):
            res["error"] = f"读剪贴板HTML失败: {html}"
            print(json.dumps(res, ensure_ascii=False)); return 5
        pr = _TableParser(); pr.feed(html)
        if not pr.rows:
            res["error"] = "剪贴板无表格(解析0行)"
            print(json.dumps(res, ensure_ascii=False)); return 5
        res["header"] = pr.rows[0]
        res["rows"] = pr.rows[1:]
        res["bg"] = pr.bgs
        res["nrows"] = len(pr.rows) - 1
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"# sheet={sheet_id} range={rng}  数据行={res['nrows']}")
        print("\t".join(res["header"]))
        for i, row in enumerate(res["rows"]):
            line = "\t".join(c.replace("\n", " ⏎ ") for c in row)
            if with_bg:
                bg = res["bg"][i + 1] if i + 1 < len(res["bg"]) else []
                line += "\t|bg:" + ",".join(bg)
            print(line)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read")
    r.add_argument("--sheet", required=True, help="sheet id(URL ?sheet= 值)")
    r.add_argument("--range", default="A1:Z200", help="区域,如 A4:Q65")
    r.add_argument("--doc-token", default=DEFAULT_DOC, help="workbook token")
    r.add_argument("--json", action="store_true")
    r.add_argument("--with-bg", action="store_true", help="带出每格底色")
    r.add_argument("--who", default="Master")
    r.add_argument("--no-lock", action="store_true")
    a = ap.parse_args()
    if a.cmd == "read":
        if not a.no_lock:
            _lock("acquire", a.who)
        try:
            rc = read(a.doc_token, a.sheet, a.range, a.json, a.with_bg)
        finally:
            if not a.no_lock:
                _lock("release", a.who)
        sys.exit(rc)


if __name__ == "__main__":
    main()
