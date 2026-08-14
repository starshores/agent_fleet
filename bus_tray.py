# -*- coding: utf-8 -*-
"""
bus_tray.py —— agent bus 运转状态 · Windows 系统托盘指示器
- 托盘图标数字【优先级】(用户 2026-08-14):掉线数>0 → 画【掉线数】(异常态醒目·比在跑更该一眼看到);
    否则画【在跑数 M】(用户 2026-08-04 口径:一眼判断是否在干活,0=没活)。颜色反映 bus 健康:
    🔴 红 = 有掉线 agent(优先) / 🟢 绿 = 有 agent 在跑任务 / 🔵 蓝 = 在岗但全 idle / 🟠 橙 = 有静默异常 / 🔴 红 = 全部离线 / ⚪ 灰 = 探测失败
- 悬停 tooltip 看健康:⚠掉线X(有则提最前) · 在岗N · 在跑M · 静默K · 休眠P · 更新时间
- 双击 = 打开本机控制台 WebUI;右键 = 刷新 / 打开控制台 / 退出
纯本机运行(只跑本机 agentbus.py who --json + 打开 127.0.0.1 控制台),不联网、不外发任何数据。
数据口径由 Master数分2(bus 监控)定义;数据源 = agentbus.py who --json。
"""
import os
import sys
import json
import glob
import time
import subprocess
import threading
import tempfile
import webbrowser
import urllib.request

import win32api
import win32con
import win32gui
import win32event
import winerror
from PIL import Image, ImageDraw, ImageFont

# ---- 配置(可用环境变量覆盖)----
AGENTBUS = os.environ.get("AGENTBUS_PY", r"C:\apphome\agent_fleet\agent_bus\agentbus.py")
PYEXE = os.environ.get("AGENTBUS_PYEXE", sys.executable)
WEBUI = os.environ.get("AGENTBUS_WEBUI", "http://127.0.0.1:8799")
POLL_SEC = int(os.environ.get("BUS_TRAY_POLL", "5"))
BUS_DIR = os.path.dirname(AGENTBUS)
RUN_TTL = int(os.environ.get("BUS_TRAY_RUN_TTL", "3600"))  # processing claim 超此秒=卡死僵尸,不计在跑
FONT_PATH = r"C:\Windows\Fonts\arialbd.ttf"
CREATE_NO_WINDOW = 0x08000000  # 不弹控制台

WM_TRAY = win32con.WM_USER + 20
ID_REFRESH = 1001
ID_OPEN = 1002
ID_QUIT = 1003

# 颜色(RGB)
COL = {
    "run":    (62, 207, 142),   # 绿:有在跑任务
    "idle":   (91, 157, 255),   # 蓝:在岗全 idle
    "warn":   (245, 166, 35),   # 橙:有静默异常
    "down":   (255, 93, 93),    # 红:全部离线
    "err":    (125, 138, 160),  # 灰:探测失败
}

_state = {"hwnd": None, "hicon": None, "tip": "bus 托盘启动中…", "n": 0, "color": "err",
          "ico_toggle": 0}


def _parse_claimed(s):
    """bus 的 claimed 是本机本地时间 ISO(无时区)→ 本地时间戳"""
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def _probe_webui():
    """优先源:GET WebUI /api/tray —— 与运维面板【同一 fleet 缓存口径】(单一源·实时)。
    用户 2026-07-14 要求托盘实时/口径对齐运维面板;WebUI 在跑时走这条,数字与面板永远一致。
    返回 (N, M, K, P, stale) 或 None(WebUI 未起/超时→退回本机 who 探测)。"""
    try:
        with urllib.request.urlopen(WEBUI + "/api/tray", timeout=4) as resp:
            d = json.loads(resp.read().decode("utf-8", "replace"))
        if not d.get("ok") or d.get("loading"):
            return None
        return (int(d.get("present", 0)), int(d.get("running", 0)),
                int(d.get("silent", 0)), int(d.get("parked", 0)), int(d.get("stale", 0)),
                int(d.get("offline", 0)))
    except Exception:
        return None


def _probe_local():
    """兜底源:本机 agentbus who --json + 扫 processing claim(WebUI 未起时用)。
    返回 (在岗N, 在跑M, 静默K, 休眠P, 卡死claim) 或 None(探测失败)。
    在跑 M = processing/ 里 non-notice 消息、claim 新鲜(<RUN_TTL)且 owner 在岗 → 真·在执行;
    卡死claim = processing/ 里超 RUN_TTL 的 non-notice 僵尸 claim(claim 了没 done,不算在跑,是待清 rot)。
    不用 who.current_task 作在跑源:该字段会滞留在已卡死的旧 claim 上 → 虚报在跑。"""
    try:
        out = subprocess.run(
            [PYEXE, AGENTBUS, "who", "--json"],
            capture_output=True, timeout=25, creationflags=CREATE_NO_WINDOW,
        )
        data = json.loads(out.stdout.decode("utf-8", "replace"))  # 显式 UTF-8,防 GBK 坑
    except Exception:
        return None
    roles = data.get("roles", [])

    def real(r):
        return not r.get("pseudo") and not r.get("retired")

    present = [r for r in roles if real(r) and r.get("present") and r.get("alive")]
    alive_names = set(r["role"] for r in present)
    n = len(present)
    k = sum(1 for r in present if "静默" in (r.get("verdict") or ""))
    p = sum(1 for r in roles if real(r) and r.get("parked"))
    # 掉线口径(对齐 WebUI bus_tray_counts·Master数分2 定 2026-08-14):dead(注册过但进程死=present且not alive)
    #  ∪ self_wake stale(⚠掉线);排 parked+pseudo+retired(real() 已排 pseudo/retired)。who --json 无 vclass,
    #  用 present&not alive 等价 dead。WebUI 在跑时走 _probe_webui 读权威 offline,本兜底仅 WebUI down 时用。
    offline = sum(1 for r in roles if real(r) and not r.get("parked")
                  and ((r.get("present") and not r.get("alive")) or r.get("self_wake") == "stale"))

    now = time.time()
    running = set()
    stale = 0
    for d in glob.glob(os.path.join(BUS_DIR, "*")):
        if not os.path.isdir(d):
            continue
        role = os.path.basename(d)
        if role.startswith("_") or role in ("shared",):
            continue
        for f in glob.glob(os.path.join(d, "processing", "*.json")):
            if "notice-" in os.path.basename(f):
                continue
            ts = None
            try:
                with open(f, encoding="utf-8") as fh:
                    obj = json.load(fh)
                if obj.get("from") == "bus守卫" or obj.get("kind") == "notice":
                    continue          # 系统消息(自醒守卫提醒/完成通知)≠ 在跑任务,不计
                ts = _parse_claimed(obj.get("claimed", ""))
            except Exception:
                ts = None
            if ts is None:
                try:
                    ts = os.path.getmtime(f)
                except Exception:
                    ts = now
            if now - ts < RUN_TTL:
                if role in alive_names:
                    running.add(role)
            else:
                stale += 1
    return (n, len(running), k, p, stale, offline)


def _probe():
    """单一源优先:先试 WebUI /api/tray(与运维面板同口径·实时),不可达再退回本机 who 探测。"""
    r = _probe_webui()
    if r is not None:
        return r
    return _probe_local()


def _pick_color(n, m, k, stale, offline=0):
    if offline > 0:
        return "down"   # 有掉线 agent = 异常态醒目(告警红·优先于一切·用户 2026-08-14)
    if n == 0:
        return "down"
    if m > 0:
        return "run"
    if k > 0 or stale > 0:
        return "warn"
    return "idle"


def _make_icon(num, color):
    """PIL 画一个带数字的圆盘 → 存 .ico → LoadImage 成 HICON。返回 hicon。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = COL.get(color, COL["err"])
    d.ellipse([1, 1, size - 2, size - 2], fill=bg + (255,))
    txt = ("99+" if num >= 100 else str(num)) if isinstance(num, int) else str(num)
    fsize = 42 if len(txt) == 1 else (34 if len(txt) == 2 else 26)
    try:
        font = ImageFont.truetype(FONT_PATH, fsize)
    except Exception:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), txt, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), txt,
           fill=(255, 255, 255, 255), font=font)
    # 双缓冲文件名,避免 LoadImage 与写文件撞锁
    _state["ico_toggle"] ^= 1
    path = os.path.join(tempfile.gettempdir(), "bus_tray_%d.ico" % _state["ico_toggle"])
    img.save(path, format="ICO", sizes=[(32, 32), (16, 16)])
    hicon = win32gui.LoadImage(0, path, win32con.IMAGE_ICON, 0, 0,
                               win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
    return hicon


def _update_tray(n, tip, color):
    hwnd = _state["hwnd"]
    if not hwnd:
        return
    new_icon = _make_icon(n, color)
    old_icon = _state["hicon"]
    nid = (hwnd, 0, win32gui.NIF_ICON | win32gui.NIF_TIP | win32gui.NIF_MESSAGE,
           WM_TRAY, new_icon, tip[:127])
    try:
        win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
    except Exception:
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
    _state["hicon"] = new_icon
    if old_icon:
        try:
            win32gui.DestroyIcon(old_icon)
        except Exception:
            pass


def _fmt_tray(n, m, k, p, stale, offline):
    """算托盘图标数字 + tooltip。图标数字【优先级】(用户 2026-08-14):
    掉线数>0 → 图标显示【掉线数】(异常态醒目·比在跑更该一眼看到);否则显示【在跑数 m】(正常态·维持现状口径)。
    tooltip 恒列全轴,掉线>0 时把「⚠掉线X」提到最前醒目位。返回 (图标数字, tooltip 串)。"""
    num = offline if offline > 0 else m
    state = "运转中" if n else "全部离线"
    off = ("⚠掉线%d · " % offline) if offline else ""
    tip = "bus %s · %s在岗%d · 在跑%d · 静默%d · 休眠%d%s · %s" % (
        state, off, n, m, k, p, (" · 卡claim%d" % stale) if stale else "",
        time.strftime("%H:%M:%S"))
    return num, tip


def _poll_loop():
    while True:
        r = _probe()
        if r is None:
            tip = "bus 探测失败(agentbus who 无响应)"
            _update_tray("?", tip, "err")
        else:
            n, m, k, p, stale, offline = r
            num, tip = _fmt_tray(n, m, k, p, stale, offline)
            _update_tray(num, tip, _pick_color(n, m, k, stale, offline))
        time.sleep(POLL_SEC)


# ---- 窗口消息处理 ----
def _on_tray(hwnd, msg, wparam, lparam):
    if lparam == win32con.WM_LBUTTONDBLCLK:
        webbrowser.open(WEBUI)
    elif lparam == win32con.WM_RBUTTONUP:
        _show_menu(hwnd)
    return True


def _show_menu(hwnd):
    menu = win32gui.CreatePopupMenu()
    win32gui.AppendMenu(menu, win32con.MF_STRING, ID_OPEN, "打开控制台 (WebUI)")
    win32gui.AppendMenu(menu, win32con.MF_STRING, ID_REFRESH, "立即刷新")
    win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
    win32gui.AppendMenu(menu, win32con.MF_STRING, ID_QUIT, "退出")
    pos = win32gui.GetCursorPos()
    win32gui.SetForegroundWindow(hwnd)  # Win 怪癖:否则菜单不消失
    win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN | win32con.TPM_BOTTOMALIGN,
                            pos[0], pos[1], 0, hwnd, None)
    win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)


def _on_command(hwnd, msg, wparam, lparam):
    cid = win32api.LOWORD(wparam)
    if cid == ID_OPEN:
        webbrowser.open(WEBUI)
    elif cid == ID_REFRESH:
        threading.Thread(target=lambda: _refresh_once(), daemon=True).start()
    elif cid == ID_QUIT:
        win32gui.DestroyWindow(hwnd)
    return True


def _refresh_once():
    r = _probe()
    if r is None:
        _update_tray("?", "bus 探测失败", "err")
    else:
        n, m, k, p, stale, offline = r
        num, tip = _fmt_tray(n, m, k, p, stale, offline)
        _update_tray(num, tip, _pick_color(n, m, k, stale, offline))


def _on_destroy(hwnd, msg, wparam, lparam):
    win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
    win32gui.PostQuitMessage(0)
    return True


def main():
    # 单实例守卫:已在跑就直接退,避免叠出重复托盘图标
    # 关键:句柄必须存进 _state 长期持有,否则被 GC → CloseHandle → mutex 释放 → 守卫失效
    _state["mutex"] = win32event.CreateMutex(None, False, "Global\\BusTraySingleton")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return
    hinst = win32api.GetModuleHandle(None)
    wc = win32gui.WNDCLASS()
    wc.hInstance = hinst
    wc.lpszClassName = "BusTrayWndClass"
    wc.lpfnWndProc = {
        WM_TRAY: _on_tray,
        win32con.WM_COMMAND: _on_command,
        win32con.WM_DESTROY: _on_destroy,
    }
    atom = win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(atom, "BusTray", win32con.WS_OVERLAPPED,
                                 0, 0, 0, 0, 0, 0, hinst, None)
    win32gui.UpdateWindow(hwnd)
    _state["hwnd"] = hwnd

    # 先加一个占位图标
    hicon = _make_icon("…", "err")
    _state["hicon"] = hicon
    nid = (hwnd, 0, win32gui.NIF_ICON | win32gui.NIF_TIP | win32gui.NIF_MESSAGE,
           WM_TRAY, hicon, "bus 托盘启动中…")
    win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    threading.Thread(target=_poll_loop, daemon=True).start()
    win32gui.PumpMessages()


if __name__ == "__main__":
    main()
