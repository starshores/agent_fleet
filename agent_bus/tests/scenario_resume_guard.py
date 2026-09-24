# -*- coding: utf-8 -*-
"""resume 修复 C 验收(Master数分 4057c1):① 造 NUL 损坏的 presence.json(zz 测试角色)→ ab resume 列『⚠presence损坏』行(不再静默消失);
② 造两个 zz 角色同 session_id → 文本模式尾部告警行;--json 模式 rows 结构不变·告警在 stderr。测完整目录删。期待末行 ALL PASS。"""
import os, sys, json, subprocess, shutil
os.environ["PYTHONUTF8"] = "1"
ROOT = r"C:\apphome\agent_fleet"; BUS = os.path.expanduser(r"~\.claude\agent_bus"); PY = sys.executable; AB = [PY, os.path.join(ROOT, "agent_bus", "agentbus.py")]
ok = True
def chk(c, m):
    global ok; print(("PASS " if c else "FAIL ") + m); ok = ok and c
def run(*a):
    return subprocess.run(AB + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
# 角色名用 ztres_*:cmd_resume 过滤 zz/__ 前缀测试角色·zt 前缀不被过滤又一眼可辨;测完整目录删
roles = {"ztres_broken": None, "ztres_dupA": "00000000-dead-beef-0000-000000000001", "ztres_dupB": "00000000-dead-beef-0000-000000000001"}
try:
    for r, sid in roles.items():
        d = os.path.join(BUS, r); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "presence.json")
        if sid is None:
            open(p, "wb").write(b'{"role": "ztres_broken", "mode": "listening"' + b"\x00" * 317)
        else:
            open(p, "w", encoding="utf-8").write(json.dumps({"role": r, "mode": "stopped", "session_id": sid, "cwd": "C:\\tmp", "last_seen": "2026-09-01T00:00:00"}))
    r = run("resume")
    chk(r.returncode == 0 and "Traceback" not in r.stderr, "resume 跑通无TB")
    line = [l for l in r.stdout.splitlines() if l.startswith("ztres_broken")]
    chk(line and "⚠presence损坏" in line[0], "① 损坏 presence 显 ⚠presence损坏 行: %s" % (line[0][:60] if line else "缺行"))
    chk("同一会话id被多个 role 登记" in r.stdout and "ztres_dupA" in r.stdout and "ztres_dupB" in r.stdout, "② 同 sid 两 role → 文本告警行")
    rj = run("resume", "--json")
    rows = json.loads(rj.stdout.strip().splitlines()[-1])
    chk(isinstance(rows, list) and any(x.get("role") == "ztres_broken" and x.get("state") == "⚠presence损坏" for x in rows), "①b --json 含损坏行·rows 仍为 list")
    chk("同一会话id" in rj.stderr and "同一会话id" not in rj.stdout, "②b --json 告警走 stderr·stdout 结构不变")
    for x in rows:
        chk(set(x) >= {"role", "state", "last_seen", "session_id", "cwd", "mode", "resume", "note", "size_kb"}, "字段集不变(%s)" % x["role"]) if x.get("role", "").startswith("ztres") else None
finally:
    for r in roles:
        shutil.rmtree(os.path.join(BUS, r), ignore_errors=True)
print("ALL PASS" if ok else "SOME FAIL")
