# agent_fleet — Agent Bus 基础设施

后续接手从 [下一会话入口](docs/MASTERAGENT_NEXT_SESSION.md) 开始，再按 [版本路线](docs/MASTERAGENT_VERSION_ROADMAP.md) 和 [执行工单](docs/MASTERAGENT_EXECUTION_BACKLOG.md) 推进。已验版本包集中在 [releases](releases/README.md)，当前正式版仍为 `3.0.0`，开发版为 `3.1.0-dev.4`。历史临时预览已离线；归档不改变生产服务。副本清理记录见 [本机收尾说明](docs/MASTERAGENT_LOCAL_HANDOFF_20261019.md)，会话最终清理由用户按 [会话清理手册](docs/SESSION_CLEANUP.md) 执行。

项目整体判断见 [2026-10-20 重新评估](docs/MASTERAGENT_PROJECT_REASSESSMENT_20261020.md)：对照原目标核查业务分离、统一工作台、运行时、存储与迁移，记录 13 项发现及下一阶段建议；三个关键缺口有独立合成复现。本次是分析交付，未修改产品代码或发布新版本。

项目历史与未来开源写作参考 [模型贡献重新评估](docs/MASTERAGENT_MODEL_CONTRIBUTIONS_REASSESSMENT_20261020.md)；[首轮分析与数据](docs/MASTERAGENT_MODEL_CONTRIBUTIONS_20261020.md)保留为基线。材料区分成果证据、模型归因与实际承接范围，尚未对外发布。

## MasterAgent 3.1 开发预览

当前开发代码为 `3.1.0-dev.4`，状态 schema 6，公共 API 保留 `3.1-dev1`；正式发行仍为 3.0.0。阶段 B 新增受控本机来源、准入前端点绑定与独立常驻搬运；完整异常核对工作台、旧 fleet 接入及真实文本试点仍待验收。成果页与跨代任务链分开展示平台保存、目的端持久保存、agent 处理和用户已读；后两项没有证据时保持未知。[受控搬运](docs/MASTERAGENT_3_1_TRANSPORT.md) · [工作台启动与契约](docs/MASTERAGENT_3_1_WORKBENCH.md) · [3.1 方案](docs/MASTERAGENT_3_1_PLAN.md) · [功能迁移清单](docs/MASTERAGENT_3_1_UI_COVERAGE.md) · [实施进度](docs/MASTERAGENT_3_1_PROGRESS.md)。

默认只读入口：`python -B -m masteragent --state-root <显式绝对目录> workbench --port 0`。增加 `--enable-commands` 才开放工作台写操作；可用 `--legacy-console-url http://127.0.0.1:8799` 显式提供旧控制台导航地址。页面不会初始化状态、启动成员、探测旧服务或合并两套队列。

当前保持纯文本模式，业务工具与人工原生审批未开放。schema 1/2/3/4 只能显式备份迁移至 5；本批没有迁移任何运行中的旧状态。管理操作与回执核对见 [持久文本通道说明](docs/MASTERAGENT_3_1_ROUTING.md)。旧 fleet 领取入口尚未适配或切流，真实单角色→双角色文本试点仍待完成，不能把核心排他视为生产已接管。21 项旧功能仍未全部迁移，制品索引与工具隔离按各自依赖继续推进。

本批没有新增真实 Codex 模型验收，Claude Code 和混合团队仍未真实验收。原 3.0 验收目录的认证复用授权不得延用到 3.1。本批结果不代表阶段 B 全部完成或 3.1 正式发行，测试、浏览器证据与构建摘要按批次记录于实施进度。

## MasterAgent 3.0 正式发行入口

`masteragent/` 是业务可替换的独立协调基座，当前本机正式发行版为 `3.0.0`，状态 schema 4。已完成隔离状态根中的真实 Codex 双角色持续接单、纠偏、停止、断线后显式恢复与 A→B→A 验收。现有 `agent_bus/` 舰队仍独立运行，尚未迁移或由 3.0 接管；正式版范围与未交付能力见 [发行说明](docs/MASTERAGENT_3_RELEASE.md)。

- 使用和边界：[3.0 基础切片](docs/MASTERAGENT_3_FOUNDATION.md)
- 协作与状态升级：[3.0 公共协作核心](docs/MASTERAGENT_3_COORDINATION.md)
- Codex 单次文本执行：[3.0 运行时接入](docs/MASTERAGENT_3_RUNTIME.md)
- 常驻成员：[3.0 成员手册](docs/MASTERAGENT_3_MEMBERS.md)
- 进度与验证：[3.0 进度](docs/MASTERAGENT_3_PROGRESS.md)
- 隔离测试：`python -B -m unittest discover -s tests_v3 -v`
- 命令入口：`python -B -m masteragent --help`
- 正式包入口：`python -B <本机交付目录>/masteragent-3.0.0.pyz --version`

下面保留旧版本说明，供迁移参考。新入口不导入旧 `agent_bus`，旧入口和运行数据仍保持原状。

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
