# -*- coding: utf-8 -*-
"""分离 A3 验收(Master数分 b3282f K2/f/清除问·桩已删版):
① 平台目录 agent_bus/ 无 external_*.py(f:业务侧记忆旧路径=0 后删桩·grep 平台源码零 external_ 工具引用);
② 新位 biz_tools/external/external_common.edge_lock 经 canonical `ab lock acquire/release --resource edge` 成功(K2);edge 被真实角色持有时跳过(不抢别人锁);
③ 新位四工具 --help exit0(有载可用);④ 清除问:业务工具目录缺失时平台 `ab lock status` 照常(平台不依赖业务工具)。不动 live 文件。
由 bus_smoke @py 调用·期待末行 ALL PASS。"""
import os, sys, json, subprocess, glob, re
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
# ① 平台目录零业务工具
chk(not glob.glob(os.path.join(BUS, "external_*.py")), "① agent_bus/ 无 external_*.py(桩已删)")
refs = []
for fn in glob.glob(os.path.join(BUS, "*.py")) + glob.glob(os.path.join(BUS, "bus", "*.py")):
    s = open(fn, "rb").read().decode("utf-8", "replace")
    if re.search(r"external_(common|canvas|msg|read|sheet_read)", s):
        refs.append(os.path.basename(fn))
chk(not refs, "① 平台源码零 external_ 工具引用(%s)" % refs)
# ② K2
if os.path.isdir(DST):
    st = json.loads(run(PY, os.path.join(BUS, "agentbus.py"), "lock", "status", "--resource", "edge").stdout.strip().splitlines()[-1])
    if st.get("locks"):
        print("SKIP ② 抢锁:edge 被 %s 持有(不抢别人的锁)" % st["locks"][0].get("who"))
    else:
        r = run(PY, "-c", "import sys; sys.path.insert(0, r'%s'); from external_common import edge_lock; edge_lock('acquire','__smoke__'); print('ACQ'); edge_lock('release','__smoke__'); print('REL')" % DST)
        chk("ACQ" in r.stdout and "REL" in r.stdout, "② K2 新位 edge_lock 经 ab lock acquire/release 成功")
    # ③ 有载可用
    for f in ("external_read.py", "external_sheet_read.py", "external_msg.py", "external_canvas.py"):
        r = run(PY, os.path.join(DST, f), "--help"); chk(r.returncode == 0 and "Traceback" not in r.stderr, "③ 新位 %s --help" % f)
else:
    print("SKIP ②③ 业务工具未安装(biz_tools 缺失=合法空载)")
# ④ 清除问:平台锁原语不依赖业务工具目录
r = run(PY, os.path.join(BUS, "agentbus.py"), "lock", "status", env={"AGENTBUS_BIZ_TOOLS": r"C:\__nonexistent_biz_tools__"})
chk(r.returncode == 0 and "locks" in r.stdout, "④ 业务工具目录缺失时 ab lock status 照常")
print("ALL PASS" if ok else "SOME FAIL")
