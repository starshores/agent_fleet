#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""worker_daemon.py — 无可见 tab 的后台 worker。

轻量守护进程:阻塞在 watchdog 上等任务(零 token、无窗口),任务来了才临时拉起
`claude -p` 跑完,再 done 回填。比"tab 里跑 /loop"更省、更独立(脱离任何会话)。

用法:
  python worker_daemon.py --role <角色名> [--cwd <项目目录>] [--wait-timeout 300]
通常用 start_worker.ps1 以隐藏窗口后台拉起(见同目录脚本)。
"""
import argparse, json, os, sys, time, subprocess, socket
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentbus as ab  # 复用同一套总线逻辑

# 机器相关路径集中到 agent_bus/config.json(ab.cfg),不再各处硬编码;CLAUDE_BIN 环境变量仍兼容覆盖。
CLAUDE = os.environ.get("CLAUDE_BIN") or ab.cfg("claude_bin", str(Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd"))
DEFAULT_CWD = ab.cfg("default_worker_cwd", r"C:\BaseSpace\XXX")


def log(role, msg):
    d = ab.BUS / role
    d.mkdir(parents=True, exist_ok=True)
    line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(d / "daemon.log", "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", flush=True)


def touch(role, mode, timeout):
    ab._touch_presence(role, mode, {"daemon_pid": os.getpid(), "wait_timeout": timeout})


def run_task(role, cwd, task):
    """临时拉起 claude -p 执行任务,返回 (status, result_text)。"""
    instr = task.get("task", "")
    title = task.get("title", "")
    # 注入角色身份 + 专责(从 agents.json 的 desc 读),让 daemon 真正"扮演"该角色而非通用 worker
    try:
        desc = (ab._load_agents().get(role, {}) or {}).get("desc", "") or ""
    except Exception:
        desc = ""
    abpy = str(ab.BUS / "agentbus.py")
    ident = "你是 agent bus 上的【%s】角色执行体。\n" % role
    if desc:
        ident += "你的专责领域:%s\n" % desc
    prompt = (
        ident +
        "下面是已经分配给你的一项具体任务,请【以该角色的专责视角,直接执行这项任务本身】并完成它。\n"
        "重要:任务内容已在下面给全;**不要**去 agent bus 取任务、**不要**起 loop/serve/wait、"
        "**不要**把自己当成轮询 worker。只做下面这件事。\n"
        "【若本任务要操作 Edge/云文档(复制/点击/粘贴/编辑)】:先抢 Edge 互斥锁再操作、用完释放——\n"
        "  python \"%s\" edge-acquire --who %s  →  (connect_over_cdp 编辑)  →  python \"%s\" edge-release --who %s\n"
        "  抢不到(timeout)就稍候重试或回报忙;不碰 Edge/云文档的任务忽略此条。\n"
        "完成后用一段简洁中文回复结果/产物路径(你的最终回复会被原样作为回填内容)。\n\n"
        "【任务标题】%s\n【任务内容】\n%s" % (abpy, role, abpy, role, title, instr)
    )
    # 流式跑 claude -p:过程实时写 <role>/live.log(tail -f 可看),并拿最终结果。
    # (prompt 经 stdin 传,避免 claude.cmd 批处理 shim 在首换行截断多行 argv。)
    import claude_run
    live = ab.BUS / role / "live.log"
    return claude_run.run_streaming(CLAUDE, cwd, prompt, str(live), timeout=3600)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True)
    ap.add_argument("--cwd", default=DEFAULT_CWD)
    ap.add_argument("--wait-timeout", type=float, default=300.0)
    args = ap.parse_args()
    role, cwd, wt = args.role, args.cwd, args.wait_timeout

    ab._role_dir(role)
    log(role, "daemon 启动 pid=%s cwd=%s claude=%s" % (os.getpid(), cwd, CLAUDE))
    while True:
        touch(role, "daemon", wt)               # 心跳 + 声明(无 claude tab,按 daemon_pid 判活)
        task = ab._wait_core(role, wt)           # 阻塞在 watchdog,空闲超时返回 None
        if task is None:
            continue
        if task.get("error"):
            log(role, "坏任务文件:%s" % task); continue
        tid = task.get("id"); title = task.get("title", "")
        # 高风险闸:requires_approval 的任务【无人值守的 daemon 不执行】(写库/外发须先审)。
        # 移到 <role>/held/ 留存(不丢、不被本 daemon 反复重领),并 done-error 通知发起方去交互 tab 处理。
        if task.get("requires_approval"):
            held = ab.BUS / role / "held"; held.mkdir(parents=True, exist_ok=True)
            ab._write_json(held / (tid + ".json"), task)
            msg = ("requires_approval=true:daemon 无人值守,不执行高风险任务(写库/外发须先审)。"
                   "已移至 %s/held/%s.json,请在交互 tab 领取/人工批准后再做。" % (role, tid))
            subprocess.run([sys.executable, str(Path(__file__).parent / "agentbus.py"),
                            "done", "--id", tid, "--role", role, "--result", msg, "--status", "error"],
                           check=False)
            log(role, "任务 %s requires_approval → daemon 拒绝执行,已 held + 报错回发起方" % tid)
            continue
        log(role, "领到任务 %s 《%s》→ 拉起 claude -p" % (tid, title))
        status, result = run_task(role, cwd, task)
        # 回填(走 agentbus done,自动归档 + 路由给发起方)
        try:
            subprocess.run([sys.executable, str(Path(__file__).parent / "agentbus.py"),
                            "done", "--id", tid, "--role", role,
                            "--result", result, "--status", status],
                           check=False)
        except Exception as e:
            log(role, "done 回填失败:%s" % e)
        log(role, "任务 %s 完成 status=%s" % (tid, status))


if __name__ == "__main__":
    main()
