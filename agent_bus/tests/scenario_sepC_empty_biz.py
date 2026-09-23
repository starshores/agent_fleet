# -*- coding: utf-8 -*-
"""分离 C·空载清除测试【隔离版·不动 live 文件】(Master数分 fdbd6d 建议固化):
AGENTBUS_BIZ_REGISTRY 指向一个空临时目录 = 业务注册表"被清除"的等价态(cfg 环境变量优先级 > config.json),
live 的 biz_registry / 舰队实时读 不受扰动。验:① load_caps_pool 只剩 infra;② hook build_context 业务角色不抛·退通用层;
③ roster.load_role_profiles 无业务角色;④ ab roster / who / caps 子进程空载渲染无 Traceback;⑤ 恢复 env 后有载视图回来。
由 bus_smoke 调用·期待末行 ALL PASS。"""
import os, sys, json, tempfile, shutil, subprocess, importlib
os.environ["PYTHONUTF8"] = "1"
ROOT = r"C:\apphome\agent_fleet"; BUS = os.path.join(ROOT, "agent_bus")
PY = sys.executable; AB = [PY, os.path.join(BUS, "agentbus.py")]
ok = True
def chk(c, m):
    global ok; print(("PASS " if c else "FAIL ") + m); ok = ok and c
def ab(*a, env=None, timeout=60):
    e = dict(os.environ); e.update(env or {})
    return subprocess.run(AB + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=timeout, cwd=ROOT)

tmp = tempfile.mkdtemp(prefix="sepC_empty_")            # 空目录 = 业务表全清
EMPTY = {"AGENTBUS_BIZ_REGISTRY": tmp}
sys.path.insert(0, BUS); sys.path.insert(0, os.path.join(ROOT, "hooks"))
try:
    from bus.roster import load_caps_pool, load_role_profiles, biz_registry_dir
    hk = importlib.import_module("reinject_post_compact")
    # 有载基线(当前 live)
    pool_live = load_caps_pool(); prof_live = load_role_profiles()
    biz_roles = [r.get("role") for r in json.load(open(os.path.join(str(biz_registry_dir()), "role_profiles_biz.json"), encoding="utf-8")).get("roles", [])]
    chk(len(pool_live) >= 8 and biz_roles, "0 有载基线:caps %d·业务角色 %d(live 未动)" % (len(pool_live), len(biz_roles)))
    # 进空载(仅本进程 env·不动文件)
    os.environ.update(EMPTY)
    chk(str(biz_registry_dir()) == tmp, "1 biz_registry_dir 跟随 AGENTBUS_BIZ_REGISTRY(隔离·不动 live)")
    pool0 = load_caps_pool()
    chk(pool0 and all(k in pool_live for k in pool0) and not ({"external-tool-edge", "sql-fetch", "confidentiality"} & set(pool0)), "2 空载 caps=仅 infra(%d·无业务 cap)" % len(pool0))
    prof0 = load_role_profiles(); roles0 = {r.get("role") for r in prof0.get("roles", [])}
    chk(not (set(biz_roles) & roles0), "3 空载 role_profiles 无业务角色(%d→%d)" % (len(prof_live.get("roles", [])), len(roles0)))
    ctx = [hk.build_context(r) for r in biz_roles[:3]]
    chk(all(isinstance(c, str) for c in ctx), "4 空载 hook build_context 业务角色不抛(退通用层·len=%s)" % [len(c) for c in ctx])
    for sub in ("roster", "who", "caps"):
        r = ab(sub, env=EMPTY); chk(r.returncode == 0 and "Traceback" not in (r.stdout + r.stderr), "5 空载 ab %s 子进程渲染" % sub)
    r = ab("caps", env=EMPTY); chk("external-tool-edge" not in r.stdout and "bus-comms" in r.stdout, "6 空载 ab caps 输出仅 infra")
    # 恢复有载
    os.environ.pop("AGENTBUS_BIZ_REGISTRY", None)
    chk(len(load_caps_pool()) == len(pool_live), "7 恢复 env 后有载视图回来(%d)" % len(pool_live))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASS" if ok else "SOME FAIL")
