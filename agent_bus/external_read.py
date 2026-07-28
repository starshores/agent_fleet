# -*- coding: utf-8 -*-
"""
external_read.py — 经 Edge CDP 只读采集云文档单聊/群的最近消息(可复用 CLI)。

配套 external_msg.py(发消息)。本工具只读:定位会话 -> 提取最近 N 条 -> 打印。
绝不键入/发送/回复,绝不 b.close() 用户的 Edge(只 disconnect)。

用法:
  python external_read.py read --contact 示例用户 [--last 30] [--json] [--who Master] [--no-lock]
  --contact  会话名(单聊人名或群名);在左侧会话列表 a11y_feed_card_item 里按名字匹配,不用搜索框。
  --last     取最近 N 条(默认 30),最新在前。
  --json     结构化输出 [{"ts","sender","text","id","self"}];默认打印人读表格。
  --self-name 己方显示名(默认"示例用户"),对方(message-not-self)显示为 --contact 名。
  --who      edge 锁持有者标识(默认 Master)。
  --no-lock  调用方已持 edge 锁时跳过自己抢/放。
  --scroll-rounds 会话卡不在首屏时,列表上滚轮数(默认 0=只看首屏)。

退出码:0=成功;3=没开云文档消息页;4=会话未找到;5=无消息 item。stdout 末行/主体为结果。
选择器来源:实测(message-item / message-self|not-self / message-timestamp / a11y_feed_card_item)。
"""
import os, sys, json, argparse, subprocess
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
CDP = "http://localhost:9222"
MESSENGER_URL = os.environ.get("EXTERNAL_MSG_URL", "https://example.com/next/messenger/")  # 按部署环境配置消息页地址
_RELOAD = False  # --reload:读前 reload messenger 强制拉最新(defeat 背景标签 WS 节流)

# 提取最近消息:DOM 正序 -> 取尾 N 反转(最新在前)。self 由 className 判定。
_EXTRACT_JS = r"""
(n) => {
  const rows = [];
  document.querySelectorAll('[class*=message-item]').forEach((el) => {
    const cls = (el.className || '').toString();
    const isSelf = /message-self/.test(cls);
    const t = el.querySelector('.message-timestamp');
    rows.push({
      id: el.id || '',
      self: isSelf,
      ts: t ? (t.textContent || '').trim() : '',
      text: (el.innerText || '').trim()
    });
  });
  return rows.slice(-n).reverse();
}
"""


from external_common import edge_lock as _lock   # P2 收敛:四份逐字复制体归一(焦点策略成文也在 external_common)


def _find_messenger(ctx):
    # 只认【已存在】的 messenger 标签;绝不 new_page()、绝不劫持/导航其它标签。
    # (2026-07-09 用户明示"又冒出新页面"→彻底禁止本工具建页;messenger 标签没了就报错让用户重开,
    #  不擅自新建,避免在用户前台窗口冒标签。)
    for pp in ctx.pages:
        if "/next/messenger" in pp.url:
            return pp
    return None


def _open_contact(pg, contact, scroll_rounds):
    """在会话列表按名字点开单聊/群。命中返回 True。"""
    def _try_click():
        return pg.evaluate(
            """(name)=>{const cards=[...document.querySelectorAll('div.a11y_feed_card_item')];
                 // 第一遍:标题行(首个非空行)精确==name → 优先命中单聊,避开"群里name是最后发言人"的预览误匹配
                 for(const c of cards){
                   const lines=(c.innerText||'').split('\\n').map(s=>s.trim()).filter(Boolean);
                   if(lines.length && lines[0]===name){c.click();return true;}}
                 // 第二遍:回退到包含匹配(群名/别名等)
                 for(const c of cards){
                   if((c.innerText||'').includes(name)){c.click();return true;}}
                 return false;}""",
            contact)
    if _try_click():
        return True
    # 不在首屏:在列表容器内上/下滚后重扫
    for _ in range(max(0, scroll_rounds)):
        pg.evaluate("""()=>{const c=document.querySelector('div.a11y_feed_card_item');
            if(c){const s=c.closest('[class*=scroll],[class*=list]')||c.parentElement;
            if(s) s.scrollTop += 600;}}""")
        pg.wait_for_timeout(400)
        if _try_click():
            return True
    return False


def read(contact, last, as_json, self_name, scroll_rounds):
    res = {"contact": contact, "count": 0, "messages": [], "error": None}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        pg = _find_messenger(ctx)
        if not pg:
            res["error"] = "NO_MESSENGER (Edge 里没开云文档消息页)"
            print(json.dumps(res, ensure_ascii=False)); return 3
        # 只读走 page.evaluate(DOM),不需要标签在前台——【不 bring_to_front】以免抢用户焦点。
        # 【--reload】背景标签的 WebSocket 会被浏览器节流→新消息不进 DOM(实测漏读);
        # reload 强制重连 WS 拉最新(reload 是页面级、不抢焦点)。采集循环务必开 reload。
        if _RELOAD:
            try:
                pg.reload(wait_until="domcontentloaded", timeout=40000)
                pg.wait_for_timeout(4500)
                for _ in range(8):
                    if pg.evaluate("()=>document.querySelectorAll('div.a11y_feed_card_item').length") > 0:
                        break
                    pg.wait_for_timeout(1500)
            except Exception:
                pass
        else:
            pg.wait_for_timeout(200)
        if not _open_contact(pg, contact, scroll_rounds):
            res["error"] = f"会话未找到(会话列表首屏/滚动内无匹配「{contact}」)"
            print(json.dumps(res, ensure_ascii=False)); return 4
        pg.wait_for_timeout(1000)
        rows = pg.evaluate(_EXTRACT_JS, last)
        if not rows:
            res["error"] = "无消息 item(会话可能未加载完/为空)"
            print(json.dumps(res, ensure_ascii=False)); return 5
        for r in rows:
            r["sender"] = self_name if r["self"] else contact
        res["count"] = len(rows)
        res["messages"] = rows
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"# {contact} 最近 {res['count']} 条(最新在前)")
        for r in res["messages"]:
            print(f"[{r['ts'] or '--:--'}] {r['sender']}: {r['text']}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read")
    r.add_argument("--contact", required=True, help="会话名(单聊人名/群名)")
    r.add_argument("--last", type=int, default=30, help="取最近 N 条")
    r.add_argument("--json", action="store_true", help="结构化 JSON 输出")
    r.add_argument("--self-name", default="示例用户", help="己方显示名")
    r.add_argument("--who", default="Master", help="edge 锁持有者标识")
    r.add_argument("--no-lock", action="store_true", help="调用方已持锁则跳过自抢/放")
    r.add_argument("--scroll-rounds", type=int, default=0, help="会话卡不在首屏时列表滚动轮数")
    r.add_argument("--reload", action="store_true", help="读前reload messenger强制拉最新(背景标签WS会被节流,采集循环务必开)")
    a = ap.parse_args()
    if a.cmd == "read":
        global _RELOAD
        _RELOAD = a.reload
        if not a.no_lock:
            _lock("acquire", a.who)
        try:
            rc = read(a.contact, a.last, a.json, a.self_name, a.scroll_rounds)
        finally:
            if not a.no_lock:
                _lock("release", a.who)
        sys.exit(rc)


if __name__ == "__main__":
    main()
