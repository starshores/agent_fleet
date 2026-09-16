#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bus_smoke.py — BUS 回归冒烟基线(P0 · BUS 2.0 重构回归金标准)

用途:2.0 重构每阶段后跑一次,确认没把命令面/行为改坏。
设计:
  - **结构验证,不做字节比对**:who/health/selfwake 等命令输出含活体状态(agent 来去、pid、时间戳)
    天然不确定,故只验【命令仍在册 + 退出码符合预期 + 无 Python traceback + 关键结构标记在】。
  - 全部用 `__smoke__` / `__smokegrp__` 测试角色(_is_test_role 过滤 __* → 不污染花名册/计数),跑完自清理。
  - 跳过会劫持身份(hello --role/serve)、起 daemon(supervise 非 dry-run)、烧 claude(dispatch)、
    阻塞(request 无应答方用短超时兜底)的破坏性命令。
  - --apply/写态类一律走 --dry-run。
  - 数据不出本机:纯本地 subprocess + 本地 JSON 报告。

跑法:
  python bus_smoke.py            # 跑测试,若无基线则存基线;有基线则对比并报回归
  python bus_smoke.py --save     # 强制(重新)存基线为金标准
  python bus_smoke.py --json     # 机器可读结果

归属:Master数分 owns 运行/口径把关(基线验证是数分 lane);MasterAgent 建。
"""
import os, sys, json, subprocess, re, argparse, shutil

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

PY = os.environ.get("AGENTBUS_PY", r"C:\anaconda3\envs\server\python.exe")
AB = os.environ.get("AGENTBUS_PY_SCRIPT", os.path.expanduser(r"~/.claude/agent_bus/agentbus.py"))
BUS_DIR = os.path.dirname(AB)
HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "bus_smoke_baseline.json")

SMOKE_ROLE = "__smoke__"
SMOKE_GRP = "__smokegrp__"
TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)")


def run(args, timeout=30, stdin_text=None):
    """跑一条 agentbus 命令,返回 {exit, out, err, tb}。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run(
            [PY, AB] + args,
            capture_output=True, timeout=timeout, env=env,
            input=(stdin_text.encode("utf-8") if stdin_text else None),
        )
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        tb = bool(TRACEBACK_RE.search(out) or TRACEBACK_RE.search(err))
        return {"exit": p.returncode, "out": out, "err": err, "tb": tb}
    except subprocess.TimeoutExpired:
        return {"exit": -99, "out": "", "err": "TIMEOUT", "tb": False}


def discover_commands():
    """从 --help 解析全部子命令名(集合)——重构中命令消失即回归。"""
    r = run(["--help"], timeout=15)
    m = re.search(r"\{([a-z0-9,\-]+)\}", r["out"])
    if not m:
        return []
    return sorted(m.group(1).split(","))


# ---- 检查器 ----
def chk_exit0(r):
    return r["exit"] == 0 and not r["tb"]

def chk_no_tb(r):
    """跑通(可能非 0 退出,但不能崩 traceback)。"""
    return not r["tb"] and r["exit"] != -99

def chk_json(*keys):
    def _c(r):
        if r["exit"] != 0 or r["tb"]:
            return False
        try:
            j = json.loads(r["out"])
        except Exception:
            return False
        if isinstance(j, list):
            return True
        return all(k in j for k in keys)
    return _c

def chk_contains(*subs):
    def _c(r):
        if r["tb"] or r["exit"] == -99:
            return False
        return any(s in r["out"] for s in subs)
    return _c


def check_reexports():
    """C5(2026-07-21):验所有【外部导入方】(import agentbus as ab)引用的 ab.<name> 都在 agentbus 顶层可达。
    起因=_load_caps_pool 教训:P1 抽层把 helper 搬进 bus/ 但漏 re-export → 命令面冒烟测不到、只在
    server.py 那条代码路径真跑时才 500 炸。命令面盘点覆盖不到 helper re-export 缺口,故补此 in-process 检查。
    动态扫 agent_bus/ + ui/ 下 import agentbus 的 .py 里的 ab.<name>/agentbus.<name> 属性引用 + hasattr 断言。
    返回 (ok, missing:[(file,name)])。纯本地静态扫 + import·不外发。"""
    import importlib
    if BUS_DIR not in sys.path:
        sys.path.insert(0, BUS_DIR)
    try:
        ab = importlib.import_module("agentbus")
    except Exception as e:
        return (False, [("<import agentbus>", str(e)[:100])])
    files = []
    for d in (BUS_DIR, os.path.join(HERE, "ui")):
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.endswith(".py") and fn != "agentbus.py":
                    files.append(os.path.join(d, fn))
    ref_re = re.compile(r"\b(?:ab|agentbus)\.([A-Za-z_]\w*)")
    NOISE = {"py"}
    missing, seen = [], set()
    for fp in files:
        try:
            src = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        if "import agentbus" not in src:
            continue
        # 跳过整行注释,减少注释里 ab.xxx 的假阳性
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        for name in set(ref_re.findall(code)):
            if name in NOISE:
                continue
            key = (os.path.basename(fp), name)
            if key in seen:
                continue
            seen.add(key)
            if not hasattr(ab, name):
                missing.append(key)
    return (len(missing) == 0, sorted(missing))


def setup():
    run(["register", "--role", SMOKE_ROLE, "--desc", "smoke test role"])


def teardown():
    """清掉 __smoke__ / __smokegrp__ 一切痕迹(agents.json / retired / 目录 / parked / group)。"""
    # agents.json(dict) + retired_roster.json(list of {"role":..}) 去除测试角色
    smoke_keys = (SMOKE_ROLE, SMOKE_GRP)
    for fn in ("agents.json", "retired_roster.json"):
        p = os.path.join(BUS_DIR, fn)
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            changed = False
            if isinstance(d, dict):
                for key in smoke_keys:
                    if key in d:
                        del d[key]; changed = True
                    if "retired" in d and isinstance(d["retired"], dict) and key in d["retired"]:
                        del d["retired"][key]; changed = True
            elif isinstance(d, list):
                new = [e for e in d if not (isinstance(e, dict) and e.get("role") in smoke_keys)]
                if len(new) != len(d):
                    d = new; changed = True
            if changed:
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=1)
                os.replace(tmp, p)
        except Exception:
            pass
    # 目录 / parked / group（组实际建在 _groups/<grp>；旧路径 BUS/<grp> 是历史遗留，一并清）
    for sub in (SMOKE_ROLE, SMOKE_GRP, f"_groups/{SMOKE_GRP}", f"_groups/{SMOKE_ROLE}",
                f"BUS/{SMOKE_GRP}", f"parked/{SMOKE_ROLE}.json"):
        path = os.path.join(BUS_DIR, sub)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass
    # parked 文件常见位置
    for cand in (os.path.join(BUS_DIR, SMOKE_ROLE, "parked.json"),):
        try:
            if os.path.isfile(cand):
                os.remove(cand)
        except Exception:
            pass
    # 探针用例残留:_probes/instances/ 里派给测试角色的 instance(probe.dispatch.visible 产生)
    inst_dir = os.path.join(BUS_DIR, "_probes", "instances")
    if os.path.isdir(inst_dir):
        for fn in os.listdir(inst_dir):
            if SMOKE_ROLE in fn:
                try:
                    os.remove(os.path.join(inst_dir, fn))
                except Exception:
                    pass


def build_tests():
    """
    返回 [(name, argv, checker, note)] 安全测试用例。
    分组:只读态 / 只读dry / 有态测试角色 / 边界(期望被拒/兜底)。
    """
    T = []
    # ---- 只读态命令(exit 0 + 无 traceback / JSON 结构)----
    T.append(("who.json", ["who", "--json"], chk_json(), "活体核对 JSON"))
    T.append(("who.text", ["who"], chk_no_tb, "who 人看表格"))
    T.append(("list", ["list"], chk_no_tb, "队列深度"))
    T.append(("health.json", ["health", "--json"], chk_json(), "健康总检 JSON"))
    T.append(("health.text", ["health"], chk_no_tb, "健康总检文本"))
    T.append(("selfwake", ["selfwake"], chk_contains("自醒", "在岗", "role"), "自醒检测"))
    T.append(("caps", ["caps"], chk_no_tb, "能力池"))
    T.append(("roster", ["roster"], chk_no_tb, "花名册"))
    T.append(("route", ["route", "数据"], chk_no_tb, "路由助手"))
    T.append(("retired", ["retired"], chk_no_tb, "退役档案馆"))
    T.append(("tasklines.json", ["tasklines", "--json"], chk_json(), "任务线看板"))
    T.append(("stale.json", ["stale", "--json"], chk_no_tb, "停滞待办"))
    T.append(("mem-cap", ["mem-cap"], chk_no_tb, "内存感知并发上限"))
    T.append(("whoami", ["whoami", "--json"], chk_no_tb, "身份反查(未绑定 exit 2 也算跑通)"))
    T.append(("sessions.json", ["sessions", "--json"], chk_no_tb, "可恢复会话列表"))
    # ---- 只读 dry-run(不改状态)----
    T.append(("reap.dry", ["reap", "--dry-run"], chk_no_tb, "孤儿回收 dry"))
    T.append(("supervise.dry", ["supervise", "--dry-run"], chk_no_tb, "舰队对账 dry(不起 daemon)"))
    T.append(("webui-guard.dry", ["webui-guard", "--dry-run"], chk_no_tb, "WebUI 守卫 dry"))
    T.append(("prune.dry", ["prune", "--dry-run"], chk_no_tb, "清空目录 dry"))
    T.append(("selfwake-guard.dry", ["selfwake-guard", "--dry-run"], chk_no_tb, "自醒守卫 dry"))
    T.append(("health-sweep.detect", ["health-sweep"], chk_no_tb, "丢失巡检(不带 --apply=只检测)"))
    T.append(("todos-archive.dry", ["todos-archive"], chk_no_tb, "完成态归档 dry(不带 --apply=只报)"))
    T.append(("route-reap", ["route-reap"], chk_no_tb, "状态路由超时回收 tick(无组=干净 no-op)"))
    # ---- 有态:测试角色核心任务流 send→peek→wait→done ----
    T.append(("register", ["register", "--role", SMOKE_ROLE, "--desc", "smoke"], chk_no_tb, "注册测试角色"))
    T.append(("send", ["send", "--to", SMOKE_ROLE, "--from", SMOKE_ROLE, "--title", "smoke", "--task", "smoke task body"], chk_contains(SMOKE_ROLE, "id", "-"), "派活(打印 task id)"))
    T.append(("peek", ["peek", "--role", SMOKE_ROLE], chk_no_tb, "非侵入查看 inbox"))
    # wait 会领走上面 send 的任务(inbox 已有,应即返)
    T.append(("wait", ["wait", "--role", SMOKE_ROLE, "--timeout", "5"], chk_contains("smoke task body", "id", "idle"), "阻塞等待/领取"))
    T.append(("replies", ["replies", "--role", SMOKE_ROLE], chk_no_tb, "收回填"))
    # ---- 有态:工作组池化 ----
    T.append(("join-group", ["join-group", "--role", SMOKE_ROLE, "--group", SMOKE_GRP], chk_no_tb, "运行时入组"))
    T.append(("send-group", ["send-group", "--group", SMOKE_GRP, "--from", SMOKE_ROLE, "--title", "g", "--task", "grp task"], chk_no_tb, "发往工作组(pull 模型)"))
    T.append(("send-group.route", ["send-group", "--group", SMOKE_GRP, "--from", SMOKE_ROLE, "--route", "--title", "gr", "--task", "route task"], chk_no_tb, "状态路由 push(无 available→排队·测不崩)"))
    T.append(("leave-group", ["leave-group", "--role", SMOKE_ROLE, "--group", SMOKE_GRP], chk_no_tb, "运行时离组"))
    T.append(("fanout", ["fanout", "--group", SMOKE_GRP, "--from", SMOKE_ROLE, "--title", "f", "--shards", '["a","b"]'], chk_no_tb, "拆片撒组 inbox"))
    # ---- 有态:park / unpark 测试角色 ----
    T.append(("park", ["park", "--role", SMOKE_ROLE, "--days", "1", "--note", "smoke"], chk_no_tb, "故意暂离"))
    T.append(("unpark", ["unpark", "--role", SMOKE_ROLE], chk_no_tb, "解除暂离"))
    # ---- 有态:edge 锁(测试 who + 短超时)----
    T.append(("edge-acquire", ["edge-acquire", "--who", SMOKE_ROLE, "--timeout", "2"], chk_no_tb, "抢 Edge 锁(被占则 timeout 也算跑通)"))
    T.append(("edge-release", ["edge-release", "--who", SMOKE_ROLE, "--force"], chk_no_tb, "释放 Edge 锁"))
    # ---- 有态:retire → revive 测试角色(自清路径)----
    T.append(("retire", ["retire", "--role", SMOKE_ROLE, "--reason", "smoke test"], chk_no_tb, "退役归档"))
    T.append(("revive", ["revive", "--role", SMOKE_ROLE], chk_no_tb, "复用回路"))
    # ---- 只读:hello 预览(不带 --role=无副作用)----
    T.append(("hello.preview", ["hello", "--preview"], chk_no_tb, "onboarding 预览(只读无副作用)"))
    # ---- 边界:ask-user 非成员应被拒(捕获拒绝行为)----
    T.append(("ask-user.reject", ["ask-user", "--from", SMOKE_ROLE, "--title", "smoke q", "--detail", "x"], chk_no_tb, "非成员 ask-user 应硬拒(不崩)"))
    # ---- 边界:answer 不存在的 todo 应优雅兜底 ----
    T.append(("answer.notfound", ["answer", "--todo", "__nonexist__", "--reply", "x", "--from", "用户"], chk_no_tb, "答不存在 todo 优雅兜底"))
    # ---- 质检仪器波机制(v2.8/v2.9·2026-09-16 收编)----
    # probe:派生只读 + ★guard/绕行成对用例(教训:防露馅 guard 挡住 dispatch 真分支→ImportError 潜伏·
    # 358f5a0 修;纪律=加 guard 必配"guard 拦住"与"合法绕行"两条用例·真分支必须被跑到)
    T.append(("probe.derive", ["probe", SMOKE_ROLE, "--type", "会问", "--subtype", "A", "--json"], chk_json(), "探针派生(caps缺口→盲区+对口反查·只读)"))
    def chk_refuse(r):  # guard 拒派:非零/拒派字样·无 traceback
        return (not r["tb"]) and ("拒派" in (r["out"] + r["err"]))
    T.append(("probe.dispatch.guard", ["probe", SMOKE_ROLE, "--type", "会问", "--subtype", "A", "--dispatch"], chk_refuse, "模板占位→防露馅 guard 应拒派"))
    T.append(("probe.dispatch.visible", ["probe", SMOKE_ROLE, "--type", "会问", "--subtype", "A", "--visible", "smoke真实壳:请说明你会怎么拿到X", "--dispatch"], chk_contains("已派"), "★绕行合法路径真派(dispatch 真分支必须被跑到·派给测试角色自足)"))
    T.append(("probe-verdict", ["probe-verdict", "probe-会问-A-" + SMOKE_ROLE], chk_contains("行为", "候选", "待人工"), "机械协查层1(刚 dispatch 的实例)"))
    T.append(("memory-history.list", ["memory-history", "list"], chk_no_tb, "记忆分层·历史空间清单(只读)"))
    T.append(("user-absence.dry", ["user-absence-check"], chk_no_tb, "用户缺席检测 dry(无 --apply=只报)"))
    T.append(("ack-notices.dry", ["ack-notices", "--role", SMOKE_ROLE, "--dry"], chk_no_tb, "notice 代谢档A dry(只列不动)"))
    return T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="强制(重新)存基线金标准")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    print(f"[bus_smoke] AB={AB}")
    setup()
    try:
        commands = discover_commands()
        tests = build_tests()
        results = []
        # 扫描重的命令(全量 proc_scan + 遍历所有 role)并发负载下慢,给足超时防 flakiness
        HEAVY = {"reap", "health-sweep", "supervise", "health", "who", "selfwake",
                 "selfwake-guard", "webui-guard", "prune", "sessions"}
        for name, argv, checker, note in tests:
            to = 90 if argv[0] in HEAVY else 30
            r = run(argv, timeout=to)
            ok = bool(checker(r))
            results.append({"name": name, "cmd": argv[0], "ok": ok, "exit": r["exit"], "tb": r["tb"], "note": note})
        # C5:外部导入方 ab.* 引用的 re-export 完整性(命令面盘点覆盖不到的 helper re-export 缺口)
        rx_ok, rx_missing = check_reexports()
        results.append({"name": "reexport.completeness", "cmd": "reexport", "ok": rx_ok,
                        "exit": 0 if rx_ok else 1, "tb": False,
                        "note": "外部 ab.* re-export 完整性(C5)" + ("" if rx_ok else " 缺:%s" % (rx_missing[:6]))})
    finally:
        teardown()

    report = {
        "commands": commands,
        "command_count": len(commands),
        "tests": results,
        "test_count": len(results),
        "pass": sum(1 for x in results if x["ok"]),
        "fail": sum(1 for x in results if not x["ok"]),
    }

    # 对比基线
    regressions = []
    if os.path.isfile(BASELINE) and not args.save:
        with open(BASELINE, encoding="utf-8") as f:
            base = json.load(f)
        base_cmds = set(base.get("commands", []))
        now_cmds = set(commands)
        gone = base_cmds - now_cmds
        added = now_cmds - base_cmds
        if gone:
            regressions.append(f"命令消失: {sorted(gone)}")
        base_pass = {t["name"] for t in base.get("tests", []) if t["ok"]}
        now_fail = {t["name"] for t in results if not t["ok"]}
        broke = base_pass & now_fail
        if broke:
            regressions.append(f"原本过、现在挂的测试: {sorted(broke)}")
        report["baseline_compare"] = {
            "commands_gone": sorted(gone), "commands_added": sorted(added),
            "regressed_tests": sorted(broke), "regressions": regressions,
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"\n命令面: {report['command_count']} 个子命令")
        print(f"冒烟: {report['pass']}/{report['test_count']} 过")
        for x in results:
            if not x["ok"]:
                print(f"  ✗ {x['name']:22s} exit={x['exit']} tb={x['tb']}  ({x['note']})")
        if regressions:
            print("\n⚠ 回归:")
            for rg in regressions:
                print(f"  - {rg}")
        else:
            print("\n✓ 无回归" + ("(对比基线)" if os.path.isfile(BASELINE) and not args.save else "(尚无基线)"))

    # 存基线
    if args.save or not os.path.isfile(BASELINE):
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[bus_smoke] 基线已存: {BASELINE}")

    # 退出码:有回归或有 fail 则非 0(供 CI/闸用)
    sys.exit(1 if (regressions or report["fail"] > 0) else 0)


if __name__ == "__main__":
    main()
