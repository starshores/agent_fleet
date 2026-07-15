# agent_fleet — Agent Bus 基础设施

多个 Claude Code CLI 会话(各是一个 *role*)互相分派任务的**文件总线 + 协调基础设施**。
无服务端、纯文件 + watchdog 事件(不轮询);一个 role 一个 tab,事件自醒、可旁观。

由 **MasterAgent**(基建演进)/ **MasterUI**(WebUI)/ **Master数分**(监控·口径·验证)三会话共同维护;
**Master** 是消费方(用总线把业务任务外派给业务 agent),不属核心。

## 目录布局
```
CLAUDE.md          会话自恢复入口 + 角色/纪律(Claude Code 自动加载·必须在根)
AGENTS.md          总线操作手册(命令/执行模型/边界)      ┐ 根级锚点
CAPABILITIES.md    已打通的对外通道(sql_agent/EDGE CDP/Airflow) ┘ (自恢复要读)
recover.py         活体自检:谁在跑/待办/回填/Edge 锁/fleet 期望态

agent_bus/         总线本体(源码硬链镜像;运行态从 ~/.claude/agent_bus/ 跑)
  agentbus.py      CLI 入口 + 命令分发(~40 子命令)
  bus/             ← BUS 2.0 一体化包(内部模块化)
      config·io·roster·proc·presence·selfwake·notify·mailbox …
  worker_daemon.py / dispatch_agent.py / claude_run.py / longrange_net.py / pool_dispatcher.py …
  *.json           config / agents / fleet / capabilities / retired_roster …(运行态与配置)

ui/                协调 WebUI 控制台(127.0.0.1:8799 · server.py)
hooks/             Claude Code 生命周期 hook(自定位/防阻塞询问/compact 重注入/turn 观测)
docs/              设计 / 评审 / 简报 / 计划文档(ARCHITECTURE_REVIEW / NEWINFRA_* / BUS_2.0_PLAN …)
memory/            基座运行记忆硬链镜像(源=~/.claude/projects/XXX/memory)
host_config/       宿主全局配置快照(~/.claude/CLAUDE.md + settings.json)
githooks/          发版 tag 时自动 zip 快照
scripts (根)       sync_code.ps1 / sync_memory.ps1 / sync_host_config.ps1(硬链镜像维护)
bus_smoke.py       回归冒烟金标准(43 命令面 + 39 用例·2.0 重构回归闸)
bus_mechanisms.json 机制注册表(单一源·供 WebUI 🔧机制 tab)
```

## 常用入口
- 自检活体:`python recover.py`
- 总线命令:`python ~/.claude/agent_bus/agentbus.py <cmd>`(hello / wait / done / who / send / health / selfwake …)
- 启 WebUI:`powershell -File ui/start_ui.ps1`(幂等·CIM 后台常驻)
- 回归冒烟:`python bus_smoke.py`

## 迁移 / 换机
本目录已自包含(源码+记忆硬链镜像),按 `CLAUDE.md` 的「迁移/换机清单」走。

> **BUS 2.0 重构进行中**(重组 + 机制优化):`agentbus.py` 单体正逐层抽入 `agent_bus/bus/` 包;
> 计划见 `docs/BUS_2.0_PLAN.md`,回归闸 `bus_smoke.py` 每阶段守。
