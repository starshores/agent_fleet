# -*- coding: utf-8 -*-
"""「忙而聋」验收(2026-09-24):__smoke__ 角色造【turn 活跃 + 无 wait + inbox 一条 2h 前未领消息】→
① _self_wake_state 判 recent 且 detail 含 ⚠忙而聋;② _who_data verdict 含 ⚠聋;③ 清空 inbox 后回到"干活期正常态"(不误标);
④ hook _gray_roles 读 config 含 MasterUI/Master数分。不借别人 role;测完还原 presence/turn_state。期待末行 ALL PASS。"""
import os, sys, json, time, importlib
os.environ["PYTHONUTF8"] = "1"
ROOT = r"C:\apphome\agent_fleet"; BUS_SRC = os.path.join(ROOT, "agent_bus"); sys.path.insert(0, BUS_SRC); sys.path.insert(0, os.path.join(ROOT, "hooks"))
from bus.config import BUS
from bus.selfwake import _self_wake_state, _inbox_backlog
from bus.status import _who_data
R = "__smoke__"; D = BUS / R
ok = True
def chk(c, m):
    global ok; print(("PASS " if c else "FAIL ") + m); ok = ok and c
D.mkdir(exist_ok=True); (D / "inbox").mkdir(exist_ok=True)
pf = D / "presence.json"; tf = D / "turn_state.json"
orig_p = pf.read_bytes() if pf.exists() else None; orig_t = tf.read_bytes() if tf.exists() else None
now = time.time(); fmt = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
msg = D / "inbox" / ("1790000000000-%s-deaf.json" % R)
try:
    pf.write_text(json.dumps({"role": R, "mode": "listening", "claude_pid": os.getpid(), "last_seen": fmt(now - 3 * 3600), "wait_timeout": 110}, ensure_ascii=False), encoding="utf-8")
    tf.write_text(json.dumps({"state": "thinking", "label": "思考/执行中", "ts": fmt(now - 20)}, ensure_ascii=False), encoding="utf-8")
    msg.write_text(json.dumps({"id": msg.stem, "from": R, "to": R, "title": "t", "task": "x", "created": fmt(now - 2 * 3600), "status": "pending"}, ensure_ascii=False), encoding="utf-8")
    n, h = _inbox_backlog(R); chk(n == 1 and 1.9 <= h <= 2.1, "_inbox_backlog=(%d, %sh)" % (n, h))
    st, detail = _self_wake_state(R, wait_roles=set())
    chk(st == "recent" and "忙而聋" in detail, "① selfwake recent+⚠忙而聋: %s" % detail[:60])
    # ② who 视图:_list_roles 排除 "_" 前缀目录 → __smoke__ 不入 who;用 zz 前缀测试角色(_is_test_role 仍判测试·who 会列)·测完整目录删
    import shutil
    Z = "zzsmoke_deaf"; ZD = BUS / Z; (ZD / "inbox").mkdir(parents=True, exist_ok=True)
    try:
        # who 判活按 claude 进程集(_alive_claude_pids)·测试 python pid 不算 → 借任一【活 claude pid】作 presence 引用(临时测试角色·测完整目录删·不碰真实 role)
        from bus.proc import _alive_claude_pids
        cp = (sorted(_alive_claude_pids()) or [os.getpid()])[0]
        (ZD / "presence.json").write_text(json.dumps({"role": Z, "mode": "listening", "claude_pid": cp, "last_seen": fmt(now - 3 * 3600), "wait_timeout": 110}, ensure_ascii=False), encoding="utf-8")
        (ZD / "turn_state.json").write_text(json.dumps({"state": "tool", "label": "跑工具:Bash", "ts": fmt(now - 20)}, ensure_ascii=False), encoding="utf-8")
        (ZD / "inbox" / ("1790000000000-%s-deaf.json" % Z)).write_text(json.dumps({"id": "x", "from": Z, "to": Z, "title": "t", "task": "x", "created": fmt(now - 2 * 3600), "status": "pending"}, ensure_ascii=False), encoding="utf-8")
        v = next((r["verdict"] for r in _who_data()["roles"] if r["role"] == Z), "")
        chk("⚠聋" in (v or "") and "干活中" in (v or ""), "② who verdict: %s" % v)
    finally:
        shutil.rmtree(ZD, ignore_errors=True)
    msg.unlink()
    st, detail = _self_wake_state(R, wait_roles=set())
    chk(st == "recent" and "忙而聋" not in detail and "干活期正常态" in detail, "③ inbox 空→干活期正常态不误标")
    hk = importlib.import_module("enforce_selfwake")
    g = hk._gray_roles(); chk(g is None or {"MasterUI", "Master数分", "MasterAgent"} <= set(g), "④ hook 灰度读 config: %s" % (sorted(g) if g else g))
finally:
    try: msg.unlink()
    except Exception: pass
    D.mkdir(exist_ok=True)   # 并发 smoke teardown 可能已删 __smoke__ 目录 → 还原前重建·防 FileNotFoundError
    if orig_p is None: pf.unlink(missing_ok=True)
    else: pf.write_bytes(orig_p)
    if orig_t is None: tf.unlink(missing_ok=True)
    else: tf.write_bytes(orig_t)
print("ALL PASS" if ok else "SOME FAIL")
