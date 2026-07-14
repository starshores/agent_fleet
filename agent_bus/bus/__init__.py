# -*- coding: utf-8 -*-
"""BUS 2.0 一体化包 —— 内部模块化的 bus 基础设施。

增量抽取自 agentbus.py(原 3267 行单文件)。原则:
  - 行为不变;agentbus.py 通过 `from bus.xxx import ...` re-export 保住既有公共面
    (dispatch_agent / longrange_net / pool_dispatcher / worker_daemon / ui.server 都 `import agentbus as ab`
     并用 ab.cfg / ab._now / ab._write_json / ab._gen_id / ab.BUS 等,名字不能丢)。
  - 依赖单向无环:config(叶) ← io / roster ← (上层 cmd_*)。
  - canonical `~/.claude/agent_bus/` 全程可跑(全员每次 wait/done/hello 都 shell agentbus.py)。

P1 已抽:config(路径/配置/时间)· io(原子写/id/审计/BI指标)· roster(agents.json/角色目录/测试角色)。
"""
