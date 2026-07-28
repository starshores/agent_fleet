# -*- coding: utf-8 -*-
"""
external_msg.py — 经 Edge CDP 给云文档群发消息(支持真 @ mention)。

为什么单独成工具:云文档的 @ 是特殊机制——纯文本 "@名字" 不会触发提醒。
真 @ 必须:键入 '@' → 浮层 .adit-mentionList 弹出 → 点中 .mention-suggestion_name=目标 → 生成 chip。
且发送必须点 .send__button,不能按 Enter(有 chip 时 Enter 被编辑器当成换行,消息卡在输入框)。

用法:
  python external_msg.py send --group "示例群" --at "示例用户" --text "正文..." [--who Master] [--no-lock] [--dry]
  --at 多个用逗号分隔,会按序作为真 @ chip 放在正文最前;--text 接在其后。
  --dry 只把内容塞进输入框 + 截图 + 读回,不点发送(供预览)。
  --no-lock 调用方已持 edge 锁时跳过自己抢/放。

退出码:0=成功(--dry 时=已就绪未发);非 0=失败。stdout 末行打印 JSON 结果。
铁律:绝不 b.close() 用户的 Edge,只 disconnect(context manager 退出即可)。
"""
import os, sys, json, argparse, subprocess
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
CDP = "http://localhost:9222"
TMP = os.path.expandvars(r"%TEMP%")
# 按部署环境配置消息页地址(不把具体租户写进基座)
MSG_URL = os.environ.get("EXTERNAL_MSG_URL", "https://example.com/next/messenger/")


from external_common import edge_lock as _lock   # P2 收敛:四份逐字复制体归一(焦点策略成文也在 external_common)


def _compose_text(pg):
    return pg.evaluate("""()=>{const e=document.querySelectorAll('[contenteditable=true]');const el=e[e.length-1];return el?el.innerText:'';}""")


def _compose_html(pg):
    return pg.evaluate("""()=>{const e=document.querySelectorAll('[contenteditable=true]');const el=e[e.length-1];return el?el.innerHTML:'';}""")


def send(group_kw, ats, text, dry=False):
    res = {"group": group_kw, "ats": ats, "sent": False, "chips_ok": [], "error": None}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        pg = None
        for pp in ctx.pages:
            if "/next/messenger" in pp.url:
                pg = pp
                break
        if not pg:
            for pp in ctx.pages:
                if "/next/" in pp.url:
                    pg = pp
                    pg.goto(MSG_URL,
                            wait_until="domcontentloaded", timeout=30000)
                    pg.wait_for_timeout(2500)
                    break
        if not pg:
            res["error"] = "NO_MESSENGER (Edge 里没开云文档消息页)"
            print(json.dumps(res, ensure_ascii=False)); return 3
        pg.bring_to_front(); pg.wait_for_timeout(400)
        # 进群
        try:
            pg.get_by_text(group_kw, exact=False).first.click(timeout=3000)
            pg.wait_for_timeout(1000)
        except Exception as e:
            res["error"] = f"进群失败({group_kw}): {e}"
            print(json.dumps(res, ensure_ascii=False)); return 4
        eds = pg.query_selector_all('[contenteditable="true"]')
        if not eds:
            res["error"] = "无 compose 输入框"
            print(json.dumps(res, ensure_ascii=False)); return 5
        eds[-1].click(); pg.wait_for_timeout(200)
        # 清残文
        for _ in range(3):
            pg.keyboard.press("Control+a"); pg.keyboard.press("Backspace"); pg.wait_for_timeout(120)
        # 逐个真 @
        for name in ats:
            pg.keyboard.type("@"); pg.wait_for_timeout(1100)
            clicked = pg.evaluate(
                """(name)=>{for(const el of document.querySelectorAll('.mention-suggestion_name')){if((el.innerText||'').trim()===name){const it=el.closest('.mention-suggestion')||el;it.click();return true;}}return false;}""",
                name)
            pg.wait_for_timeout(500)
            html = _compose_html(pg)
            ok = bool(clicked) and ("data-fake-text" in html or "mention" in html.lower()) and (name in _compose_text(pg))
            res["chips_ok"].append({"name": name, "ok": ok})
            if not ok:
                pg.keyboard.press("Escape")
                res["error"] = f"@{name} 未生成 chip(可能不在会话内/名字不符)"
                pg.screenshot(path=os.path.join(TMP, "external_msg_fail.png"))
                print(json.dumps(res, ensure_ascii=False)); return 6
        # 正文
        if text:
            pg.keyboard.type(text); pg.wait_for_timeout(400)
        before = _compose_text(pg)
        # 读回校验:所有 @名 + 正文片段都在
        markers = list(ats) + ([text[:8]] if text else [])
        if not all(m in before for m in markers):
            res["error"] = f"读回校验失败: {before[:80]!r}"
            pg.screenshot(path=os.path.join(TMP, "external_msg_fail.png"))
            print(json.dumps(res, ensure_ascii=False)); return 7
        pg.screenshot(path=os.path.join(TMP, "external_msg_before.png"))
        if dry:
            res["sent"] = False; res["dry"] = True; res["preview"] = before[:120]
            print(json.dumps(res, ensure_ascii=False)); return 0
        # 点发送按钮(不能 Enter)
        btn = pg.query_selector('.send__button:not(.send__button--disable)') or pg.query_selector('.send__button')
        if not btn:
            res["error"] = "找不到发送按钮 .send__button"
            print(json.dumps(res, ensure_ascii=False)); return 8
        btn.click(); pg.wait_for_timeout(1500)
        after = _compose_text(pg)
        # 正文/首个@名 已不在输入框 = 已发出
        probe = (text[:8] if text else ats[0])
        res["sent"] = probe not in after
        pg.screenshot(path=os.path.join(TMP, "external_msg_after.png"))
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["sent"] else 9


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--group", required=True, help="群名关键词(get_by_text 匹配)")
    s.add_argument("--at", default="", help="要真@的人,逗号分隔,放正文最前")
    s.add_argument("--text", default="", help="正文")
    s.add_argument("--who", default="Master", help="edge 锁持有者标识")
    s.add_argument("--no-lock", action="store_true", help="调用方已持锁则跳过自抢/放")
    s.add_argument("--dry", action="store_true", help="只就绪+截图,不发送")
    a = ap.parse_args()
    if a.cmd == "send":
        ats = [x.strip() for x in a.at.split(",") if x.strip()]
        if not a.no_lock:
            _lock("acquire", a.who)
        try:
            rc = send(a.group, ats, a.text, dry=a.dry)
        finally:
            if not a.no_lock:
                _lock("release", a.who)
        sys.exit(rc)


if __name__ == "__main__":
    main()
