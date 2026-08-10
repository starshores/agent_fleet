# -*- coding: utf-8 -*-
"""bus.config —— 路径/机器配置/时间(最底层叶子,不依赖任何上层)。

BUS / METRICS 是全文引用的模块级根路径;cfg 集中读机器相关配置(换机改一处)。
"""
import json, os, time
from pathlib import Path

# BUS 根目录:默认 ~/.claude/agent_bus/;AGENTBUS_DIR 环境变量可覆盖
# (ui.server 在 import agentbus 前就设好 AGENTBUS_DIR,故此处 import 时读到的即最终值)。
BUS = Path(os.path.expandvars(os.path.expanduser(os.environ.get("AGENTBUS_DIR", str(Path.home() / ".claude" / "agent_bus")))))

# BI 资源消耗指标落地文件(daemon/dispatch 任务完成后 append 一行)。
METRICS = BUS / "metrics.jsonl"

_CONFIG_CACHE = None


def cfg(key, default=None):
    """读机器相关路径配置(集中在 agent_bus/config.json,迁移/换机改这一处即可)。
    优先级:环境变量 AGENTBUS_<KEY大写> > config.json > default。
    被 worker_daemon / ui.server / recover / hook 复用,避免硬编码绝对路径散落各处。"""
    env = os.environ.get("AGENTBUS_" + key.upper())
    if env:
        return os.path.expandvars(os.path.expanduser(env))
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        try:
            _CONFIG_CACHE = json.loads((BUS / "config.json").read_text(encoding="utf-8"))
        except Exception:
            _CONFIG_CACHE = {}
    value = _CONFIG_CACHE.get(key, default)
    return os.path.expandvars(os.path.expanduser(value)) if isinstance(value, str) else value


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _idle_timeout_policy():
    """`ab wait --timeout 0`(哨兵)的空返超时秒数。真任务事件【即时接活】:watchdog Observer
    对 inbox 事件的响应与 timeout 无关,有活立刻触发——本策略只调"没活时多久空返一次 idle"。

    ★sentinel_mode(用户令 2026-08-10:自醒改"有消息才被叫醒",消灭 idle 冷唤醒白烧):
      - "block"(默认):返回 block_timeout_s(默认 7 天)= 实践上"阻塞到消息才醒",极限消灭
        idle 空返触发的 context 冷重建(缓存 TTL 1h 已过期,每次空唤醒白付整窗冷建 ~$4.2·
        近两周 171 次 ≈ 周 $361;Master数分 usage.db 实证)。用【大有限值】非真无限=保留极限
        自愈:万一 harness 清理超长后台任务,7 天到点仍空返一次触发重挂,不永久掉聋。
      - "daynight"(旧·可回退):按日/夜返回(日 2h·夜 6h,用户 2026-07-21 旧令)。
    为何不牺牲可靠性:2h/6h 空返本就不防进程猝死(死进程不会 timeout 空返),只带来周期重挂
    churn;真·死 wait 兜底=bus守卫 selfwake 巡检+用户再激活,与本策略无关。改 config.json
    一处即可切换/调时长(换机同)。本机 local time(Shanghai)。"""
    p = cfg("idle_timeout_policy", {}) or {}
    mode = p.get("sentinel_mode", "block")
    if mode == "block":
        # 真无限(Master数分 spec 修正 2026-08-10·实证):返 None → _wait_core 走 ev.wait(None) 永久阻塞,
        # "有消息才醒"、零周期空返、无溢出。❌ 绝不返巨大秒数:wait 底层按毫秒,>2^31-1 ms≈24.8 天
        # 抛 OverflowError(Master数分试 --timeout 25920000=300天 实证;虽被 _serve_wait 异常兜底不崩,但等于没生效)。
        return None
    day_start = int(p.get("day_start_hour", 7))
    night_start = int(p.get("night_start_hour", 23))
    day_s = int(p.get("day_timeout_s", 7200))       # 日间 2h
    night_s = int(p.get("night_timeout_s", 21600))  # 夜间 6h
    h = time.localtime().tm_hour
    is_day = (day_start <= h < night_start) if day_start < night_start else not (night_start <= h < day_start)
    return day_s if is_day else night_s
