# -*- coding: utf-8 -*-
"""④型悬单验收(Master数分 15b5e5 a + d21617 判据热修·2026-09-24):判据=listening 角色 processing 消息 claimed 后【无 turn 活动】≥orphan_no_turn_min。
__smoke__ 造 presence(listening)+turn_state,processing 三条:
  orph  = claimed 2h 前·turn_state.ts 早于 claimed  → 悬单
  busy  = claimed 2h 前·但 turn_state.ts 晚于 claimed(agent 领后一直在干) → 不判(Master数分③:正常 claim 后 wait 退出·agent turn 活跃·不得退回)
  fresh = claimed 1min 前(阈值内)                  → 不判
① _orphan_claims 只出 orph;② selfwake/who 标 ⚠悬单1;③ reap --dry-run 只退 orph;④ _requeue_orphans 退回 inbox 字段正确;
⑤ daemon 模式角色不判;⑥ 真跑 ab wait --timeout 3 起手退悬单并领到。测完还原 presence/turn_state。期待末行 ALL PASS。"""
import os, sys, json, time, subprocess
os.environ["PYTHONUTF8"] = "1"
ROOT = r"C:\apphome\agent_fleet"; BUS_SRC = os.path.join(ROOT, "agent_bus"); sys.path.insert(0, BUS_SRC)
from bus.config import BUS
from bus.selfwake import _orphan_claims, _requeue_orphans, _self_wake_state
R = "__smoke__"; D = BUS / R; PY = sys.executable; AB = [PY, os.path.join(BUS_SRC, "agentbus.py")]
ok = True
def chk(c, m):
    global ok; print(("PASS " if c else "FAIL ") + m); ok = ok and c
def run(*a, timeout=60):
    return subprocess.run(AB + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
now = time.time(); fmt = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
for sub in ("inbox", "processing"):
    (D / sub).mkdir(parents=True, exist_ok=True)
    for f in (D / sub).glob("1790000000001-*"): f.unlink()
pf = D / "presence.json"; tf = D / "turn_state.json"
orig_p = pf.read_bytes() if pf.exists() else None; orig_t = tf.read_bytes() if tf.exists() else None
def mk(name, claimed_ago):
    (D / "processing" / name).write_text(json.dumps({"id": name[:-5], "from": R, "to": R, "title": "t", "task": "x", "created": fmt(now - claimed_ago - 5),
                                                     "status": "processing", "claimed": fmt(now - claimed_ago), "claim_pid": 1}, ensure_ascii=False), encoding="utf-8")
try:
    pf.write_text(json.dumps({"role": R, "mode": "listening", "claude_pid": os.getpid(), "last_seen": fmt(now), "wait_timeout": 110}), encoding="utf-8")
    # 场景 A:turn_state 在 3h 前(早于所有 claimed)→ orph(2h 前 claimed)悬单;fresh(1min)阈值内不判
    tf.write_text(json.dumps({"state": "idle", "ts": fmt(now - 3 * 3600)}), encoding="utf-8")
    mk("1790000000001-%s-orph.json" % R, 2 * 3600); mk("1790000000001-%s-fresh.json" % R, 60)
    got = sorted(p.name for p in _orphan_claims(R))
    chk(got == ["1790000000001-%s-orph.json" % R], "① 只判 orph(fresh 阈值内不判): %s" % got)
    st, detail = _self_wake_state(R, wait_roles=set()); chk("悬单1" in (detail or ""), "② selfwake 标悬单1: %s" % (detail or "")[:60])
    r = run("reap", "--dry-run", "--role", R)
    chk("orph" in r.stdout and "fresh" not in r.stdout and "无turn" in r.stdout, "③ reap dry 只退 orph: %s" % [l for l in r.stdout.splitlines() if "orph" in l][:1])
    # 场景 B(Master数分③):turn_state 刷新到现在(agent 领后一直在干)→ 同一条 orph 不再判悬单
    tf.write_text(json.dumps({"state": "tool", "ts": fmt(now - 10)}), encoding="utf-8")
    chk(_orphan_claims(R) == [], "③b 正常 claim 后 wait 退出·agent turn 活跃 → 不判悬单(在办消息不得退回)")
    # 回到场景 A 退回
    tf.write_text(json.dumps({"state": "idle", "ts": fmt(now - 3 * 3600)}), encoding="utf-8")
    n = _requeue_orphans(R); o = json.load(open(D / "inbox" / ("1790000000001-%s-orph.json" % R), encoding="utf-8"))
    chk(n == 1 and o["status"] == "pending" and "claimed" not in o and "无turn" in o.get("requeue_why", ""), "④ 退回 inbox·pending·抹 claimed·requeue_why")
    chk((D / "processing" / ("1790000000001-%s-fresh.json" % R)).exists(), "④b fresh 留 processing")
    # ⑤ daemon 模式不判
    pf.write_text(json.dumps({"role": R, "mode": "daemon", "daemon_pid": os.getpid(), "last_seen": fmt(now)}), encoding="utf-8")
    mk("1790000000001-%s-dorph.json" % R, 2 * 3600)
    chk(_orphan_claims(R) == [], "⑤ daemon 角色不判悬单(交 reap ttl)")
    (D / "processing" / ("1790000000001-%s-dorph.json" % R)).unlink()
    pf.write_text(json.dumps({"role": R, "mode": "listening", "claude_pid": os.getpid(), "last_seen": fmt(now), "wait_timeout": 110}), encoding="utf-8")
    # ⑥ 端到端:inbox 清空·processing 留一条悬单 → 真 wait 起手退回并领到
    (D / "inbox" / ("1790000000001-%s-orph.json" % R)).unlink(); (D / "processing" / ("1790000000001-%s-fresh.json" % R)).unlink()
    mk("1790000000001-%s-orph2.json" % R, 2 * 3600)
    r = run("wait", "--role", R, "--timeout", "3", timeout=90)
    try:
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        got = {}
    chk(got.get("id") == "1790000000001-%s-orph2" % R, "⑥ 真 wait 起手退悬单并领到: %s" % got.get("id"))
finally:
    for sub in ("inbox", "processing"):
        for f in (D / sub).glob("1790000000001-*"): f.unlink()
    D.mkdir(exist_ok=True)
    if orig_p is None: pf.unlink(missing_ok=True)
    else: pf.write_bytes(orig_p)
    if orig_t is None: tf.unlink(missing_ok=True)
    else: tf.write_bytes(orig_t)
print("ALL PASS" if ok else "SOME FAIL")
