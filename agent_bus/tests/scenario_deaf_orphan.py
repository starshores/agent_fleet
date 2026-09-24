# -*- coding: utf-8 -*-
"""④型悬单验收(Master数分 15b5e5·2026-09-24):__smoke__ processing 造一条 claim_pid=已死 pid 的消息 →
① _orphan_claims 识别;② selfwake/who 标 ⚠悬单;③ reap --dry-run 判『claim者已亡』;④ _requeue_orphans 退回 inbox(status→pending·requeue_why·抹 claim_*);
⑤ 活 claim_pid(本进程)不判悬单;⑥ 真跑 `ab wait --timeout 3` 起手自动退回并领到该消息(端到端·真路径)。期待末行 ALL PASS。"""
import os, sys, json, time, subprocess, glob
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
for sub in ("inbox", "processing"):
    (D / sub).mkdir(parents=True, exist_ok=True)
    for f in (D / sub).glob("1790000000001-*"): f.unlink()
# 一个必死 pid:起个 python 子进程立刻退出·取其 pid
dead = subprocess.run([PY, "-c", "import os;print(os.getpid())"], capture_output=True, text=True).stdout.strip()
time.sleep(0.2)
fmt = time.strftime("%Y-%m-%dT%H:%M:%S")
mk = lambda name, pid: (D / "processing" / name).write_text(json.dumps({"id": name[:-5], "from": R, "to": R, "title": "t", "task": "x", "created": fmt, "status": "processing", "claimed": fmt, "claim_pid": int(pid)}, ensure_ascii=False), encoding="utf-8")
mk("1790000000001-%s-orph.json" % R, dead)
mk("1790000000001-%s-live.json" % R, os.getpid())
orph = [p.name for p in _orphan_claims(R)]
chk(orph == ["1790000000001-%s-orph.json" % R], "① 识别悬单(死pid)·活pid不判: %s" % orph)
pf = D / "presence.json"; orig = pf.read_bytes() if pf.exists() else None
try:
    pf.write_text(json.dumps({"role": R, "mode": "listening", "claude_pid": os.getpid(), "last_seen": fmt, "wait_timeout": 110}), encoding="utf-8")
    st, detail = _self_wake_state(R, wait_roles=set()); chk("悬单1" in detail, "② selfwake 标悬单: %s" % detail[:70])
finally:
    if orig is None: pf.unlink(missing_ok=True)
    else: pf.write_bytes(orig)
r = run("reap", "--dry-run", "--role", R); chk("claim者已亡" in r.stdout and "orph" in r.stdout and "live" not in r.stdout.split("orph")[0], "③ reap dry 判 claim者已亡(只 orph): %s" % [l for l in r.stdout.splitlines() if "orph" in l][:1])
n = _requeue_orphans(R); o = json.load(open(D / "inbox" / ("1790000000001-%s-orph.json" % R), encoding="utf-8"))
chk(n == 1 and o["status"] == "pending" and "claim_pid" not in o and "claim者已亡" in o.get("requeue_why", ""), "④ 退回 inbox·pending·抹 claim_*·requeue_why")
chk((D / "processing" / ("1790000000001-%s-live.json" % R)).exists(), "⑤ 活 claim 留 processing")
# ⑥ 端到端:再造一条悬单·真跑 wait 3s → 起手退回并领到
mk("1790000000001-%s-orph2.json" % R, dead)
(D / "inbox" / ("1790000000001-%s-orph.json" % R)).unlink()
r = run("wait", "--role", R, "--timeout", "3", timeout=90)
try:
    got = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    got = {}
chk(got.get("id") == "1790000000001-%s-orph2" % R and got.get("claim_pid"), "⑥ 真 wait 起手退悬单并领到(claim_pid=%s)" % got.get("claim_pid"))
for sub in ("inbox", "processing"):
    for f in (D / sub).glob("1790000000001-*"): f.unlink()
print("ALL PASS" if ok else "SOME FAIL")
