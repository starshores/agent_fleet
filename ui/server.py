#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多 agent 管理 WebUI — agent bus 的可视化控制台。

本机内部工具,只绑 127.0.0.1。复用 agentbus.py 的数据模型与函数,不另写一套逻辑。

起服务:  python C:/apphome/agent_fleet/ui/server.py
          可选:  --port 8799  --host 127.0.0.1

功能:
  A 舰队总览(同 who):verdict / claude_pid / 心跳age / cwd + 在跑但未认领进程
  B 队列(同 list):每 role inbox/processing/done 计数,可点开看任务 JSON
  C 派活:表单 → 复用 agentbus send --from Master
  D 回填查看:读 <role>/done(默认 Master),展示结果,可清除
  E 自动刷新:watchdog 文件事件 → SSE 推送;客户端可调轮询兜底

隐私模型(用户 2026-07-02 定):【只有显式标记(pid_tags.json private=true)的才藏,其余一律如实显示】。
  - private=true = 用户主动 opt-in 隐藏(如截图/投屏给外部那刻):只显 pid+label🔒,不探 cwd。
  - 未标记 / 已标注非 private:如实显 cwd/proj/标题(自看控制台绑 127.0.0.1、唯一观众=所有者本人,
    无外部方→默认不脱敏;掩码只丢信息,如看不出哪个个人会话吃上下文)。旧「自动掩码个人会话」已去除。
"""
import argparse
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

# ---- ui/ 子模块(BUS 2.0 P4 拆分·2026-07-20)----
# server.py 4575 行 → 拆为 ui/ 子模块:common(共享基础层)/ api(数据·build_*/get_*/compute_*/缓存/
# refresher/notify/send/todos)/ page(前端 INDEX_HTML 常量)/ routes(HTTP Handler)。本文件只做【装配入口】。
from common import ABDIR, ab, BUS  # noqa: F401

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# facade:import api 后把其全部名字(含 _change_cv 等下划线名 + 缓存对象 + compute_fleet)并入本命名空间,
# 保:① main 裸名调 start_*_refresher / build_bi ② 子进程 `import server; server.compute_fleet()` 兼容
#     ③ start_supervisor / start_selfwake_guard 用的 _NO_WINDOW 也来自 api;子进程解释器沿用 sys.executable。
import api  # noqa: E402
for _k, _v in vars(api).items():
    if not _k.startswith("__"):
        globals()[_k] = _v

from routes import Handler  # noqa: E402


def start_supervisor(interval=90):
    """后台 supervisor 线程:周期跑 `agentbus supervise`,自愈 fleet.json 里已死的 daemon。
    复用常驻 WebUI 进程当宿主(它本身已登录自启 + WmiPrvSE 脱壳常驻),不另起需要再看护的进程。
    用 Event.wait 计时(非 sleep 轮询忙等),进程退出即停。"""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "supervise"],
                               capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
            except Exception:
                pass

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print("[supervise] fleet 自愈线程已启动,间隔 %ds(读 fleet.json,重启已死 daemon)" % interval)
    return t


def start_selfwake_guard(interval=180):
    """每 ~interval 秒跑 `ab selfwake-guard`：Codex 尝试信使恢复并失败上报，
    旧 Claude 才向掉自醒角色投重挂提醒与 WinRT toast。
    由 WebUI 8799 always-on 驱动,不依赖任何 agent 醒着(把「18h 没人发现掉自醒」变「3 分钟内报警」)。
    探测逻辑全在 agentbus(cmd_selfwake_guard),此处只周期调它。
    · daemon=True 随 server 退出;· 首轮先 sleep(避开重启后 agent 尚未重挂的假警);
    · 失败静默 try/except,绝不掀翻 8799;· 子进程带 CREATE_NO_WINDOW 免弹窗(见记忆 popen-claude-no-window)。"""
    def loop():
        while True:
            time.sleep(interval)
            try:
                subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "selfwake-guard"],
                               capture_output=True, timeout=60, creationflags=_NO_WINDOW)
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()


def start_codex_messenger_guard(interval=45):
    """Repair safe exact-session Codex losses; report unresolved failures to MasterAgent."""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                subprocess.run([_PYEXE, "-B", str(ABDIR.parent / ".codex" / "codex_messenger_guard.py")],
                               capture_output=True, timeout=60, creationflags=_NO_WINDOW)
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()


def start_user_absence_check(interval=300):
    """常驻【用户缺席检测】(用户侧自醒对偶·2026-08-28):每 ~interval 秒跑 `ab user-absence-check --apply`——
    扫 pending 确认 checkpoint,交叉 present/away × age × urgency:离席+超阈→toast 升级(夜间压制)、
    低紧急超期→auto-expire(留痕不伪装已答)。对偶 start_selfwake_guard(那个探 agent 缺席·这个探用户缺席)。
    同模子:8799 always-on 驱动;首轮先 sleep;try/except 静默不掀翻 8799;子进程 CREATE_NO_WINDOW 免弹窗。"""
    def loop():
        while True:
            time.sleep(interval)
            try:
                subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "user-absence-check", "--apply"],
                               capture_output=True, timeout=60, creationflags=_NO_WINDOW)
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()


def start_maint(interval=1800):
    """常驻 bus 维护心跳(B2+B3·2026-07-21·MasterAgent 联合分析):每 ~interval 秒后台跑【两件】——
    ① reap:退回 owner 死/超 ttl 的滞留 processing 到 inbox(requeue+1·poison 超 max-requeue 进 failed/);
    ② todos-archive --older-min 1440 --apply:归档完成>24h 的待办(近期完成态留用户过目·防池膨胀·
       落地前备份 todos.json.pre-archive.bak)。
    跟 start_selfwake_guard 一个模子:8799 always-on 驱动;首轮先 sleep;各自 try/except 静默不掀翻 8799;
    子进程带 CREATE_NO_WINDOW 免弹窗(见记忆 popen-claude-no-window)。二者都幂等只读判据、静默失败不影响 8799。
    安全:reap 只动真卡死(owner 不在听且 age>120s·或 age>4000s ttl),正常长任务(<67min alive)不误伤;
    todos-archive 只动完成>24h。治此前 stuck/孤儿 + 完成态待办攒数周(手动清 25 条那类)。"""
    def loop():
        while True:
            time.sleep(interval)
            for argv in (["reap"], ["todos-archive", "--older-min", "1440", "--apply"]):
                try:
                    subprocess.run([sys.executable, str(ABDIR / "agentbus.py")] + argv,
                                   capture_output=True, timeout=90, creationflags=_NO_WINDOW)
                except Exception:
                    pass
    threading.Thread(target=loop, daemon=True).start()


def start_route_reap(interval=300):
    """工作组路由超时自愈 tick(MasterAgent 令·commit 15441c4·Master数分定 5min cadence·2026-07-23)。
    每 ~interval 秒后台跑 `ab route-reap`:扫所有组 assignments·超 15min 未 done 的路由任务回收 re-route。
    ★独立于 start_maint(30min):路由任务池对 30min 太粗(排队最坏等 30min 才补超时),故单开 5min tick。
    命令幂等·恒 exit0·无组/无超时=干净 no-op(有超时才动);活跃期 MasterAgent 用 send-route lazy 扫兜即时性,
    本 tick 只兜 parked 静默期。同 start_maint 模子:8799 always-on 驱动·首轮先 sleep·try/except 静默不掀翻 8799·
    CREATE_NO_WINDOW 免弹窗(popen-claude-no-window)。"""
    def loop():
        while True:
            time.sleep(interval)
            try:
                subprocess.run([sys.executable, str(ABDIR / "agentbus.py"), "route-reap"],
                               capture_output=True, timeout=90, creationflags=_NO_WINDOW)
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--no-supervise", action="store_true", help="关闭 fleet daemon 自愈线程")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost"):
        print("拒绝:内部工具只允许绑 127.0.0.1 /", args.host)
        sys.exit(2)

    start_watcher()
    start_fleet_refresher(20)         # 唯一舰队刷新者:20s 子进程扫描刷新 _fleet_cache(请求路径零扫描)
    start_health_refresher(25)        # 唯一健康刷新者:25s 子进程 health --json 刷新 _health_cache(请求路径零扫描)
    start_ctx_refresher(12)           # 唯一上下文占用刷新者:12s bounded jsonl 尾读刷新 _ctx_cache(1M 撞墙预警,请求路径零现读)
    start_who_refresher(20)           # 唯一 who 刷新者:20s 子进程 who --json 刷新 _who_cache(供上下文模块校验 role 在岗,请求路径零扫描)
    start_bi_refresher(12)            # 唯一 BI 刷新者:12s 跑 _compute_bi(psutil 进程树遍历)刷新 _bi_cache(请求路径零 psutil·根治 BI tab 卡顿 2026-08-11)
    start_status_pusher(15)           # daemon→agent 定时通信:15s 刷新 fleet_status.json(复用上面缓存的扫描,不再自扫)
    if not args.no_supervise:
        start_supervisor()
    start_selfwake_guard(180)         # 常驻自醒探测网(Master令·B):180s 跑 ab selfwake-guard,掉自醒→inbox提醒+toast(8799 always-on 驱动,不依赖 agent 醒着)
    start_codex_messenger_guard(45)   # 近期空闲 Codex 信使安全自愈；维修模式或未完成任务时跳过
    start_user_absence_check(300)     # 用户侧自醒对偶(2026-08-28):5min 跑 ab user-absence-check --apply,pending确认点离席+超阈→toast升级/低紧急超期auto-expire留痕(对偶selfwake-guard探用户缺席)
    start_maint(1800)                 # B2+B3(2026-07-21):30min 维护心跳=reap(退卡死/孤儿)+ todos-archive(归档完成>24h·防池膨胀)·仅动真卡死/旧完成态·安全无人值守
    start_route_reap(300)             # 工作组路由超时自愈(MasterAgent令·2026-07-23):5min 独立 tick 跑 ab route-reap(超15min未done路由任务re-route)·不搭30min start_maint(任务池太粗)·幂等no-op
    # BI 预热 + 保鲜已由 start_bi_refresher(12) 首轮立即跑接管(暖 _proc_cache + 填 _bi_cache),
    # 原一次性 _prewarm build_bi() 已并入,不再单列。舰队缓存由 start_fleet_refresher 首轮填充。
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    url = "http://%s:%d/" % (args.host, args.port)
    print("=" * 56)
    print(" Agent Bus 控制台已启动")
    print("   ", url)
    print("    BUS =", BUS)
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止。")
        srv.shutdown()


if __name__ == "__main__":
    main()
