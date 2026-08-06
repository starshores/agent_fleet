# 重构与优化边界确认书(2026-08-06 · 全模块巡检)

> 巡检人 MasterAgent;起因=用户令「各项模块看一遍,确认重构与优化边界」。
> 体量快照:bus 包 16 模块 5507 行 + agentbus.py 220(薄分发)/ ui 6228 行(api.py 2692 最大)/ hooks 6 个 680 行 / 根工具 1209 行。

## 一、分层边界(可动 / 慎动 / 禁动)

### 1. bus 核心(agent_bus/bus/ + agentbus.py)——【封板区·只修不拆】
- BUS 2.0 P0-P5 已全交付封板(3267 行单体→180 薄分发+16 模块),**不再发起结构性重构**;只做 bug 修与新机制增量。
- **慎动点**:`core.py`(全员自醒命脉,wait/done/hello 每回合 shell 它)、`mailbox.py` O_EXCL 并发 claim、`io.json_lock` 并发原语。动这些=全员风险,必须 bus_smoke 全绿 + 实测并发 gate(N 争 1 恰 1 胜)。
- **known debt(sanctioned 待做,非急)**:`health.py` 755 行为包内最大,可按 P1 抽公共模式瘦身——候选、不立项。
- **勘误(2026-08-06)**:role verdict 4 处散算已在 2.0 P2 收敛到 `_who_data` 单一源(BUS_2.0_CHANGELOG 二;ui/api.py:34 实证吃权威源),初版误列为待做,删除。改状态语义仍须逐视图实测(单一源≠免验)。
- **re-export 盲点铁律**:抽层时凡被外部 `ab.*` 引用的符号必 re-export(worker_daemon 引 `ab._wait_core` 等)。

### 2. agent_bus 根周边执行体——【维护区·不重构】
- `worker_daemon`/`claude_run`/`dispatch_agent`:daemon 前台化迁移中的过渡遗留,方向是淘汰而非优化,**只修不投资**。
- `longrange_net.py`(807 行,P0 闭环+P1 部分):按业务需求演进,重构前提=数据契约(MasterAgent/MasterUI/Master数分三方)不破。
- `external_*` 5 个(704 行):对外通道工具,使用权已归「external」agent(2026-08-05 上岗);工具壳属基建、操作属对外 lane。
- `pool_dispatcher`(工作组):现阶段=状态路由器(push-by-state)已拍板,**不重议 pull 池化**(用户 2026-07-23 定调)。

### 3. ui/ ——【刚收口·稳定优先】
- UI 2.0(U0-U4)+ U5/U5b 视觉升级已收口(UI_2.0_CLOSEOUT.md);金标准 ui_smoke 79 用例。
- **下一个重构候选 = `api.py` 2692 行**(现存最大单体);若立项,照 U4 模式:纯搬运拆分 + sha256 字节级一致 + 冒烟基线先行。**现阶段不动**(刚收口,先跑稳一个周期)。
- `page_js.py` 2083:morphdom 已全铺,增量改动走状态驱动化模式,禁回退 innerHTML 重刷。
- **遗留归 MasterUI 运行位**:B2(start_maint 周期 reap + todos-archive)/ D6(cost→token 口径);route chip 扩展(待办卡下拉)按需以 U3 同源端点做。
- **lane**:UI 实施 MasterAgent 主实现(2026-07-31 分工变更)+ MasterUI 运行位与验收;端点/面板契约改动 MasterUI↔MasterAgent 互相知会(对称协作律)。

### 4. hooks/ ——【生命线·动前广播】
- 6 个 hook 是 compact 自举/观测/纪律的机制层载体(reinject_post_compact = 失能防线)。
- 边界:**行为变更=全员影响**,动前先广播(先广播→动刀→再确认);fail-open 原则不可破(非 bus 成员放行);强制 UTF-8 不可去(GBK 崩)。注册表在 host_config/settings 快照,改后跑 sync_host_config.ps1。

### 5. 根工具——【验收闸与迁移机制·禁随意动】
- `bus_smoke.py`(43 用例)+ `ui_smoke.py`(79 用例)= **重构验收金标准**,任何动刀前基线在手、动刀后全绿才算完。改冒烟本身=改验收标尺,须Master数分交叉审。
- `sync_memory/sync_code/sync_host_config.ps1` + junction 守卫 = 迁移/自举机制,与 path_registry.json 联动,**不随意动**。
- `recover.py`/`bus_tray.py`:外围观测,可自由优化(口径以 /api/tray 单一源为准)。

### 6. 注册表(单一源)——【原则区】
- `role_profiles.json`(角色页 SSoT)/`bus_mechanisms.json`(91 机制)/`capabilities.json`/`fleet.json`/`config.json`/`path_registry.json`。
- 边界:**凡有注册表的信息不得再散落硬编码**;新机制先问注册表归属。caps 登记权归培训师(MasterAgent 只管 caps 系统机制)。机器路径只改 config.json 一处。

### 7. 运行态与数据——【禁动区】
- `<role>/` 队列、`usage.db`(历史方舟,resume 剪裁前必 --etl)、`events.jsonl`、`metrics.jsonl`、`projects/`、`_data/session-archive`、junctions。
- 重构**绝不触碰**;清理只走卫生代谢机制(done 轮转 14d/死信 7d,supervise 自动跑)。

## 二、横切作业规程(任何重构立项都适用)
1. **金标准先行**:动刀前冒烟基线在手,动刀后全绿(bus 43 / ui 79)。
2. **纯搬运拆分**:sha256/字节级一致验证(U4 已验证的模式)。
3. **对称协作律**:接口/机制/契约改动→主动通报全部消费方;文件系统级迁移=先广播→动刀→再确认。
4. **平台三问**(新机制验收):归属问(机制 or 业务?)/清除问(清完平台能空载跑?)/换域问(换业务域要改平台代码?)。业务内容一律业务侧存储+登记 path_registry biz-data 清单。
5. **验收配对**:1+1 惯例(MasterAgent 实现 + Master数分验证),重要决议成文进 docs/。
6. **素材纪律**(2.4):>1MB raw/数据包落 `agent_fleet_data/` 或 scratchpad,不落 agent_fleet 根;`ab storage-audit` 巡检。

## 三、已封板不再重议
BUS 2.0 P0-P5 全部决议(含 P5 决议 B:运行时操作锚点留根)/ UI 2.0 收口决议 / workgroup push-by-state / wait yield-not-kill 语义(512064a)/ 事件自醒不降级轮询(用户令)/ daemon 前台化方向。

## 四、当前 sanctioned 待做池(按优先序)
| 项 | 归属 | 状态 |
|---|---|---|
| B2:todos-archive + start_maint 周期 reap | MasterUI 运行位 | 待排期(todos.json 168 完成态未清出=看板虚高源) |
| D6:cost→token 口径 | MasterUI 运行位 | 待排期 |
| E7:长会话轮换 | MasterAgent+用户 | 待议(非急) |
| health.py / api.py 体量优化 | MasterAgent | 候选,不立项;api.py 待稳定一个周期后再议 |

> 勘误:初版曾列「role verdict 收敛单一源」——经核 2.0 P2 已完成(_who_data 单一源),移除。
