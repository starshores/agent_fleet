# BUS 2.0 — Phase 2 机制优化 · 设计与协调(2026-07-17)

> P0/P1 已完成(域抽取 -95%·18 bus/ 模块·薄分发器)。本文启动 P2,是**需跨角色签字/契约**的协调闸项的设计底。
> 分工:MasterAgent(设计+实现)· 使用方(count 逐视图核平 + 口径签字)· MasterUI(server.py 契约·P4 lane 但 2a 需其配合)。

## 2a 状态源收敛(灭 4 处分叉)

**现状(实证)**:role verdict 在 ≥4 处各算:
1. `bus/status.py::_who_data`(结构化·自算 verdict 字符串:`✓ worker在听`/`✗ 进程已退(残留)`/`🅿️ 休眠(用户故意暂离…)`/`未声明(无 presence)`/`◇ <plabel>`/`⊘ 退役`)
2. `bus/status.py::cmd_who`(**表格模式自算**·字符串略不同:`◇ 伪角色`/`🅿️ 休眠`+另列 note/…)
3. `bus/selfwake.py::_self_wake_state`(另一套词表:armed/recent/stale/off/parked/polling)
4. `ui/server.py`(140-192·**自算** verdict·tray 经 /api/tray→bus_tray_counts 也自算)

**目标**:单一权威源 `_who_data`,who/list/health/selfwake-display/tray/WebUI 全 format-from 它,不再各自 recompute。

**方案**:
- **步骤 1(MasterAgent·agentbus 侧)**:`cmd_who` 表格模式改为 iterate `_who_data()` 输出 format,不再自算。**副作用**:表格 verdict 字符串统一到 _who_data 词表(#2→#1 收敛)。→ **需使用方:① 逐视图核 count 不变(在岗/掉线/… 各桶数);② 认可统一后的 verdict 词表(display 会变字符串)**。
- **步骤 2(MasterAgent·词表对齐)**:`_who_data` 的 process-verdict 与 `_self_wake_state` 的 self-wake-state 语义**不同维度**(前者=presence/进程活死,后者=事件自醒 arming),不强行合一;但 `_who_data` 应**引用** `_self_wake_state` 结果填一个 `self_wake` 字段(而非各判 parked/stale),消除 parked/polling 判定的重复。→ 需使用方核 parked/polling/stale 计数不变。
- **步骤 3(MasterAgent+MasterUI·server.py 契约)**:`ui/server.py` fleet/verdict 改吃 `ab._who_data()`(已 `import agentbus as ab` 可达),删自算逻辑;tray /api/tray 同源。**契约:端点响应结构 + 面板显示口径不变**。→ **MasterUI lane**:MasterAgent 给 _who_data 字段契约,MasterUI 改 server.py 渲染层;使用方逐视图核 WebUI count 与 agentbus 对齐(验人看页面·非仅 --json)=灭 4 处分叉的最终验收点。

## 2b 待办/任务/决策数据模型归一(**需使用方口径签字后才改**)

**现状**:三套并存 + ref 交叉:`todos.json`(ask-user 待办池)/`tasklines.json`(长期任务线看板)/longrange(生长网)。决策点带 ref → todos 条目。

**待定选项(请使用方选/改)**:
- **A 归一**:统一一个 items store + type 字段(todo/taskline/decision),ref 变内部外键。收益=单源;风险=迁移 + 语义压平丢信息。
- **B 分层保留 + 契约化 ref**:三 store 各留,但把 ref 关联做成显式契约(schema + 校验),不合并。收益=低风险保语义;收益少于 A。
- **MasterAgent 倾向 B**(P1 刚证明"保语义低风险"优于大合并),但**口径归使用方**,请签字选定 + 定 ref 语义不变清单。

## 2c fleet/fleet_status 合并 + daemon 死路径清理(MasterAgent 独立·低外部依赖)

- 现 daemon 前台化淘汰中(记忆:新角色优先前台 cli)。清 fleet.json 与 fleet_status 的重复表达 + 已死 daemon 代码路径(supervise/reap 里)。→ 使用方核 supervise/reap 行为不变。

## 2d health-sweep 去重 + edge/云文档 收编 + 死机制退役(MasterAgent·部分已启动)

- ✅ **已做**:守卫提醒 fire-and-forget(`no_receipt`·已落地)=退役"守卫回执洪水"噪声。
- 待做:health-sweep 与 _collect_health 逻辑去重;bus_mechanisms.json 里标记的死机制盘点退役。→ 使用方核 health 输出不变。

## 推进顺序建议

1. **2b 先签字**(纯口径决策·不阻塞就一直卡)→ 使用方选 A/B。
2. **2a 步骤1+2**(MasterAgent 独立可做·使用方 count 闸)→ 收敛 agentbus 侧。
3. **2a 步骤3**(MasterAgent 给契约 + MasterUI 改 server.py + 使用方逐视图核)→ 灭 server.py/tray 分叉。
4. **2c/2d 剩余**(MasterAgent 独立 + 使用方核)。
5. 全绿 → P3(busctl 别名)已部分/P5 文档/P6 切版+tag。

> 数据不出本机;每项:使用方验优化前后行为/计数一致 + git commit + canonical 恒可跑。
