# -*- coding: utf-8 -*-
"""分离 A2 验收(Master数分 b3282f c/g + 回滚线⑥):派活 prompt 的共享资源互斥提示。
有载 g:近 30 天用过 edge 锁的每个角色(events.jsonl edge_acquire/lock_acquire who·排除测试/伪角色)·daemon prompt 必含
        『lock acquire --resource edge』(角色页 shared_resources 全覆盖·防静默丢锁提示);
空载 c:AGENTBUS_BIZ_REGISTRY 指空目录 → 同角色 daemon prompt 与 dispatch prompt 零业务词(云文档/外部 IM/Edge/CDP);
平台 e:worker_daemon.py / dispatch_agent.py 源码零 云文档/外部 IM/edge-acquire 字样。不动 live 文件。由 bus_smoke @py 调用·期待末行 ALL PASS。"""
import os, sys, json, re, time, tempfile, shutil, importlib
os.environ["PYTHONUTF8"] = "1"
ROOT = r"C:\apphome\agent_fleet"; BUS_SRC = os.path.join(ROOT, "agent_bus"); sys.path.insert(0, BUS_SRC)
ok = True
def chk(c, m):
    global ok; print(("PASS " if c else "FAIL ") + m); ok = ok and c
import agentbus as ab
wd = importlib.import_module("worker_daemon"); da = importlib.import_module("dispatch_agent")
BIZ = re.compile(r"云文档|外部 IM|Edge|CDP|edge-acquire|connect_over_cdp", re.I)

# g) 近 30 天真用过锁的角色
cut = time.time() - 30 * 86400; users = set()
try:
    with open(str(ab.BUS / "events.jsonl"), "rb") as f:
        for ln in f:
            try:
                o = json.loads(ln.decode("utf-8", "replace"))
            except Exception:
                continue
            if o.get("event") in ("edge_acquire", "edge_steal", "lock_acquire") and (o.get("resource") in (None, "edge")):
                ts = (o.get("ts") or "")[:19]
                try:
                    if time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S")) >= cut:
                        users.add(o.get("who"))
                except Exception:
                    pass
except Exception:
    pass
users = sorted(u for u in users if u and not u.startswith("__") and not ab._is_test_role(u))
chk(users, "g0 近30天用过 edge 锁的真实角色: %s" % users)
miss = [u for u in users if "lock acquire --resource edge" not in wd.build_prompt(u, "t", "x")]
chk(not miss, "g 有载:每个用锁角色 daemon prompt 含 edge 锁提示(缺:%s)" % miss)
if users:
    p = wd.build_prompt(users[0], "t", "x")
    chk("你的专责领域" in p, "g2 有载:daemon prompt 含专责 desc(agents_view·B1 剥 desc 后仍在)")

# c) 空载:业务注册表空 → prompt 零业务词
tmp = tempfile.mkdtemp(prefix="a2_empty_")
try:
    os.environ["AGENTBUS_BIZ_REGISTRY"] = tmp
    for u in (users or ["示例角色"]):
        p = wd.build_prompt(u, "t", "x")
        plat = "\n".join(l for l in p.replace(u, "<role>").splitlines() if not l.startswith("你的专责领域"))   # 剔角色名(身份)与 desc 行(业务内容·B1 前过渡)
        chk(not BIZ.search(plat), "c 空载:daemon prompt(%s)平台段零业务词" % u)
    p = da.build_prompt("adhoc-x", "MasterAgent", "t", "x")
    chk(not BIZ.search(p) and "lock acquire --resource" in p, "c 空载:dispatch prompt 零业务词且含通用锁原语")
finally:
    os.environ.pop("AGENTBUS_BIZ_REGISTRY", None); shutil.rmtree(tmp, ignore_errors=True)

# e) 平台源码零业务词
for fn in ("worker_daemon.py", "dispatch_agent.py"):
    src = open(os.path.join(BUS_SRC, fn), "rb").read().decode("utf-8")
    hits = re.findall(r"云文档|外部 IM|edge-acquire|connect_over_cdp", src, re.I)
    chk(not hits, "e 源码 %s 零业务词/edge-acquire 字样(%s)" % (fn, hits[:3]))
print("ALL PASS" if ok else "SOME FAIL")
