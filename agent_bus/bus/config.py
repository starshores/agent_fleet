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
