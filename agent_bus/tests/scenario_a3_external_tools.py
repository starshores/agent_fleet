# -*- coding: utf-8 -*-
"""分离 A3 验收(Master数分 b3282f d′/K2/清除问):① 旧扁平路径经桩转发 --help exit0(d′);② 新位 external_common.edge_lock 经 ab lock 抢/释 edge 成功(K2);
③ 桩不可 import(只 __main__);④ 空载:AGENTBUS_BIZ_TOOLS 指空目录 → 桩报『业务工具未安装』exit 2 无 Traceback(清除问)。不动 live 文件。
edge 锁被真实角色持有时 ② 跳过(不抢别人的锁)。由 bus_smoke @py 调用·期待末行 ALL PASS。"""
import os, sys, json, subprocess, tempfile, shutil
os.environ["PYTHONUTF8"] = "1"
ROOT = r"C:\apphome\agent_fleet"; BUS = os.path.join(ROOT, "agent_bus"); PY = sys.executable
ok = True
def chk(c, m):
    global ok; print(("PASS " if c else "FAIL ") + m); ok = ok and c
def run(*a, env=None, timeout=90):
    e = dict(os.environ); e.update(env or {})
    return subprocess.run(list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=e)
sys.path.insert(0, BUS)
from bus.config import cfg
DST = os.path.join(os.environ.get("AGENTBUS_BIZ_TOOLS") or cfg("biz_tools") or r"C:\apphome\agent_fleet_data\biz_tools", "external")
for f in ("external_read.py", "external_sheet_read.py", "external_msg.py", "external_canvas.py"):
    r = run(PY, os.path.join(BUS, f), "--help"); chk(r.returncode == 0 and "Traceback" not in r.stderr, "d' 旧路径桩转发 %s --help" % f)
st = json.loads(run(PY, os.path.join(BUS, "agentbus.py"), "lock", "status", "--resource", "edge").stdout.strip().splitlines()[-1])
if st.get("locks"):
    print("SKIP K2 抢锁:edge 被 %s 持有(不抢别人的锁)" % st["locks"][0].get("who"))
else:
    r = run(PY, "-c", "import sys; sys.path.insert(0, r'%s'); from external_common import edge_lock; edge_lock('acquire','__smoke__'); print('ACQ'); edge_lock('release','__smoke__'); print('REL')" % DST)
    chk("ACQ" in r.stdout and "REL" in r.stdout, "K2 新位 edge_lock 经 ab lock acquire/release 成功")
r = run(PY, "-c", "import sys; sys.path.insert(0, r'%s'); import external_common" % BUS)
chk(r.returncode != 0 and "ImportError" in r.stderr and "Traceback" in r.stderr, "桩 import 被拒(ImportError·只 __main__)")
tmp = tempfile.mkdtemp(prefix="a3_empty_")
try:
    r = run(PY, os.path.join(BUS, "external_read.py"), "--help", env={"AGENTBUS_BIZ_TOOLS": tmp})
    chk(r.returncode == 2 and "未安装" in r.stderr and "Traceback" not in r.stderr, "空载:业务工具清除后桩 exit2 报未安装无TB")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASS" if ok else "SOME FAIL")
