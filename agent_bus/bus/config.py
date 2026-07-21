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
    """自醒 idle timeout 按【日/夜】返回秒数(用户 2026-07-21 令:日间 2h·夜间 6h)。
    ★真任务事件【即时接活】:watchdog Observer 对 inbox 事件的响应与 timeout 无关,有活立刻触发。
    本策略只调"没活时多久空返一次 idle"= 砍闲置协调位的 idle 空唤醒重读,【不牺牲接活】。
    `ab wait --timeout 0`(哨兵)走此策略;显式正 timeout 仍覆盖。边界/时长可 config.json
    的 idle_timeout_policy 覆盖(换机/调整改一处)。本机 local time(Shanghai)。"""
    p = cfg("idle_timeout_policy", {}) or {}
    day_start = int(p.get("day_start_hour", 7))
    night_start = int(p.get("night_start_hour", 23))
    day_s = int(p.get("day_timeout_s", 7200))       # 日间 2h
    night_s = int(p.get("night_timeout_s", 21600))  # 夜间 6h
    h = time.localtime().tm_hour
    is_day = (day_start <= h < night_start) if day_start < night_start else not (night_start <= h < day_start)
    return day_s if is_day else night_s
