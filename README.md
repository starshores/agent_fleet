# agent_fleet — Agent Bus 基础设施

简体中文 · [English](README.en.md)

> **分支与版本说明**
>
> | 分支 | 定位 | 状态 |
> | --- | --- | --- |
> | `main` | Claude Code 主力使用版本（2.x 系列） | 稳定，适合日常使用；当前最新 tag 为 `v2.16` |
> | `codex/masteragent-3-boundary` | Codex 预览版（MasterAgent 3.x） | 预览，功能暂不完善，接口与行为可能变化；不建议用于生产或关键流程 |
>
> 许可证：[MIT](LICENSE)。

多个 Claude Code CLI 会话(各是一个 *role*)互相分派任务的**文件总线 + 协调基础设施**。
无服务端、纯文件 + watchdog 事件(不轮询);一个 role 一个 tab,事件自醒、可旁观。

由 **MasterAgent**(基建演进)/ **MasterUI**(WebUI)/ **Master数分**(监控·口径·验证)三会话共同维护;
**Master** 是消费方(用总线把业务任务外派给业务 agent),不属核心。

> 关于本仓历史重建、AI 署名、作者身份与许可证状态，见 [`docs/ABOUT_THIS_REPO.md`](docs/ABOUT_THIS_REPO.md)。

## 目录布局
```
CLAUDE.md          会话自恢复入口 + 角色/纪律(Claude Code 自动加载·必须在根)
AGENTS.md          总线操作手册(命令/执行模型/边界)      ┐ 根级锚点
CAPABILITIES.md    已打通的对外通道(sql_agent/EDGE CDP/Airflow) ┘ (自恢复要读)
recover.py         活体自检:谁在跑/待办/回填/Edge 锁/fleet 期望态

agent_bus/         总线本体(物理原件;~/.claude/agent_bus 是指向它的 junction)
  agentbus.py      CLI 入口 + 薄命令分发器(175 行·46 子命令)
  bus/             ← BUS 2.0 一体化包(内部模块化)
      config·io·roster·proc·presence·selfwake·notify·mailbox …
  worker_daemon.py / dispatch_agent.py / claude_run.py / longrange_net.py / pool_dispatcher.py …
  *.json           config / agents / fleet / capabilities / retired_roster …(运行态与配置)

ui/                协调 WebUI 控制台(127.0.0.1:8799 · server.py)
hooks/             Claude Code 生命周期 hook(自定位/防阻塞询问/compact 重注入/turn 观测)
docs/              设计 / 评审 / 简报 / 计划文档(ARCHITECTURE_REVIEW / NEWINFRA_* / BUS_2.0_PLAN …)
memory/            基座运行记忆硬链镜像(源=~/.claude/projects/XXX/memory)
host_config/       宿主全局配置快照(~/.claude/CLAUDE.md + settings.json)
githooks/          发版 tag 时自动 zip 快照(名带 fork 前缀)
fork_exchange/     多 bus 分岔进化交换区(FORK_NAME=genesis·每次发版配 genesis_<日期>_*.md)
scripts (根)       sync_code.ps1 / sync_memory.ps1 / sync_host_config.ps1(硬链镜像维护)
bus_smoke.py       回归冒烟金标准(46 命令面 + 39 用例·2.0 重构回归闸)
bus_mechanisms.json 机制注册表(单一源·供 WebUI 🔧机制 tab)
```

> **逐文件详版**:`docs/DIRECTORY_MAP.md` —— 每个目录/文件/bus 模块的作用一览。

## 常用入口
- 自检活体:`python recover.py`
- 总线命令:`ab <cmd>`(hello / wait / done / who / send / health / selfwake …;`ab` 或 `ab commands` 看分组总览)
- 启 WebUI:`powershell -File ui/start_ui.ps1`(幂等·CIM 后台常驻)
- 回归冒烟:`python bus_smoke.py`

## 迁移 / 换机
本目录已自包含(源码+记忆硬链镜像),按 `CLAUDE.md` 的「迁移/换机清单」走。

> **BUS 2.0 已定版**(`v2.0`·重组 + 机制优化):`agentbus.py` 单体已抽成 20 个 `agent_bus/bus/` 模块(3267→175 行);
> 机制优化(verdict 4处归一 / 跨引用契约化 / 退役 archive-aware)+ 分组入口 `ab` 全部 partner 验证。
> 计划见 `docs/BUS_2.0_PLAN.md`、改进详情见 `docs/BUS_2.0_P2_DESIGN.md`、变更清单见 `docs/BUS_2.0_CHANGELOG.md`,回归闸 `bus_smoke.py`。
> 后续小项(非阻塞·post-2.0):P4 server.py→ui/ 子模块拆分(MasterUI lane)/ P5 剩余文档收敛。

## 许可证

本项目采用 [MIT License](LICENSE) 开源。
