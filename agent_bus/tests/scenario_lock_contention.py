# -*- coding: utf-8 -*-
"""分离 A1 并发 gate(os-replace-not-atomic 记忆·上线前 gate·Master数分 b3282f 验收 a/b):
N=20 进程同抢一把通用锁·恰 1 胜 ×3 轮;acquire/release 四态 JSON 键集(成功/timeout/他人持锁/无锁)与旧 edge-* 同构。
不碰 live edge.lock(用 __smoke_lock__ resource)。由 bus_smoke @py 调用·期待末行 ALL PASS。"""
import os, sys, json, subprocess, glob
os.environ["PYTHONUTF8"] = "1"
BUS = os.path.expanduser(r"~\.claude\agent_bus"); PY = sys.executable
AB = [PY, r"C:\apphome\agent_fleet\agent_bus\agentbus.py"]
R = "__smoke__"; RES = "__smoke_lock__"
ok = True


def chk(c, m):
    global ok; print(("PASS " if c else "FAIL ") + m); ok = ok and c


def ab(*a, timeout=60):
    return subprocess.run(AB + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def last_json(s):
    return json.loads(s.strip().splitlines()[-1])


def clean():
    for f in glob.glob(os.path.join(BUS, "locks", RES + "*")):
        os.remove(f)


clean()
# 持锁者须是【有活进程的 role】(stale 判定=持锁 role 进程死→锁可被夺·与旧 edge 逻辑同):给 __smoke__ 写一份 presence
# 绑本测试进程 pid(测试期间必活);全部 20 竞争者同用 who=__smoke__(不借别人 role 测·纪律)。测试完按原样还原。
PD = os.path.join(BUS, R); PF = os.path.join(PD, "presence.json"); os.makedirs(PD, exist_ok=True)
_orig = open(PF, "rb").read() if os.path.exists(PF) else None
open(PF, "w", encoding="utf-8").write(json.dumps({"role": R, "claude_pid": os.getpid(), "mode": "smoke"}, ensure_ascii=False))
for rnd in range(3):
    procs = [subprocess.Popen(AB + ["lock", "acquire", "--resource", RES, "--who", R, "--timeout", "0.5", "--ttl", "600"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
             for i in range(20)]
    outs = [p.communicate(timeout=120)[0] for p in procs]
    wins = 0
    for o in outs:
        try:
            wins += 1 if last_json(o).get("acquired") else 0
        except Exception:
            pass
    chk(wins == 1, "round %d: 20 争 1 → 胜者 %d(恰 1)" % (rnd, wins))
    r = ab("lock", "release", "--resource", RES, "--force")
    chk(last_json(r.stdout).get("released") is True, "round %d force release" % rnd)

# 四态键集同构(旧 edge-acquire/release 契约:{acquired,who} / {acquired,timeout,holder} / {released,by} / {released,reason})
a1 = ab("lock", "acquire", "--resource", RES, "--who", R, "--timeout", "1")
a2 = ab("lock", "acquire", "--resource", RES, "--who", R + "x", "--timeout", "0.3")
r1 = ab("lock", "release", "--resource", RES, "--who", R + "x")
r2 = ab("lock", "release", "--resource", RES, "--who", R)
r3 = ab("lock", "release", "--resource", RES, "--who", R)
k = lambda r: set(last_json(r.stdout).keys())
chk(k(a1) == {"acquired", "who"} and k(a2) == {"acquired", "timeout", "holder"}, "acquire 两态键集 %s / %s" % (sorted(k(a1)), sorted(k(a2))))
chk(k(r1) == {"released", "reason"} and k(r2) == {"released", "by"} and k(r3) == {"released", "reason"}, "release 三态键集(他人持锁/自己释放/无锁)")
st = ab("lock", "status"); chk("locks" in last_json(st.stdout), "lock status 列表")
bad = ab("lock", "acquire", "--who", R, "--timeout", "0.1"); chk(bad.returncode != 0 and "Traceback" not in bad.stderr, "acquire 缺 --resource 拒绝无 TB")
clean()
if _orig is None:
    try: os.remove(PF)
    except Exception: pass
else:
    open(PF, "wb").write(_orig)
print("ALL PASS" if ok else "SOME FAIL")
