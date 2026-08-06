# Worker 无状态化 / 抗 context rot —— 机制分析(供用户拍板)

> 2026-08-06 · 业务侧发起、Master 转派、MasterAgent 分析。议题:worker=无状态纯函数、状态留协调层、
> 常驻 stale 时优先 dispatch fresh、任务间 reset 到 basic。

## 结论先行

理念方向【采纳】——它与 bus 已实证的失能规律完全一致;但两处要修正:
**① 状态该留在【持久文件层】,不是协调层;② "stale→dispatch fresh"该做成【分活性质 + stale 信号】的混合规则,不是硬规则。**
可靠 reset = 新进程(dispatch / 会话轮换),不是会话内 clear。落地形态 = 已在待做池的 **E7 长会话轮换**的具体化 + send/route 加 stale 提示。

## (a) "无状态 worker + 状态留协调层"可行性

- **理念已被实证背书**:对照实验(failure-is-memory-inheritance)——同 ~98% 压缩,裸 compact 的会话失能退役,「工作态落档 + 操作层重读」的会话满血接续。即:**worker 的可靠状态本来就不该在 context 里**,context 只是工作缓存。
- **但"状态留协调层"是错位**:协调层(Master)context 同样会 rot(前 Master数分 74% 空转、996K 骑顶失能是史实)。把 worker 状态搬进协调层 = 把 rot 集中到最不能倒的节点。**正确归属 = 持久文件层**:角色记忆库(经验)+ 工作态档(在飞任务)+ mailbox/done(任务流)+ longrange net(跨任务 DAG)。协调层只持有【路由与验收状态】——这正是 bus 现架构,不用改层次,要改的是纪律执行力(落档及时性)。
- **代价**:无状态化把"记忆"从 context 转成文件读写 → 每次 fresh 都要走 boot(角色页+记忆索引+boot-check),这是真实 token 成本(见 d)。
- **既有事实**:daemon 形态的 worker 本来就是 per-task 起 claude -p = **每任务全新 context,天然无状态**。前台化迁移(为可观测性)才把 rot 重新引入。这个议题本质是在前台形态下找回 daemon 的无状态优点。

## (b) dispatch/send 规则:是否改"常驻 stale → 优先 dispatch fresh"

不建议做成硬规则,建议**三分**:

1. **跨域/一次性/杂活 → dispatch fresh**(现行 doctrine 已是,问题在执行:杂活喂给常驻 worker 正是 specialization-route-not-self-handle 记的失能加速器。Master 观察到的"常驻接杂活被喂傻"= 路由纪律问题多于机制问题)。
2. **角色主线活 → send 常驻**:专属域的 warm context 是资产不是 rot(缓存命中、域内惯性、会话粘连);fresh 子代理"结论干脆"部分因为杂活本就不该带历史。
3. **stale 信号接入 send/route 做 advisory**:现有可观测信号(ctx 面板/usage.db 单会话累计/turn_state)可派生"该 worker 的 context 健康度";send 时目标超阈 → stderr 提示"建议 dispatch 或让其轮换",**不硬拦**(高风险审批活、需交互的活、粘连活必须仍可 send)。
   - ⚠ 前提工程:先定义并验证 stale 度量(单会话累计 token?距上次 fresh 的任务数?compact 次数?)——没有可靠度量就上硬规则=误伤。

## (c) 任务间 reset:新进程 vs 会话内 hard clear

**新进程可靠,会话内 clear 不可靠**,原因是可验证性:

- 新进程(dispatch / 关旧 tab 开新 tab):干净是**结构保证**,外部可验(新 pid/新 session_id,boot-check 机器核验必读装载)。
- 会话内 `/clear` + 重载:①外部无法验证它真 clear 了/真重读了(自述不可信,boot-check 只认 Read 记录但 clear 后的会话是否照做无外部闸);②清完重读的 token 成本与新进程几乎相同,却少了结构保证;③依赖 worker 自觉=纪律滑坡面。
- **落地形态 = E7 长会话轮换**(已在边界确认书待做池):工作态落档 → 新 tab hello/boot → 满血接续(Master数分实证路径)。轮换粒度建议"ctx 阈值或 N 任务",不是每任务(见 d)。

## (d) 成本/延迟/记忆一致性

| 维度 | 每任务 fresh(纯无状态) | 常驻 + 阈值轮换(建议) |
|---|---|---|
| token | 每任务付一次 boot(角色页+索引+必读,数千~数万级)且 **cache 命中大跌**(现全队输入侧命中 ~91%,fresh=冷缓存) | boot 成本摊到 N 任务;warm cache 保住 |
| 在岗延迟 | dispatch 冷启动(起进程+boot)分钟级 | send 到 armed worker 秒级 |
| 记忆一致性 | **更好**——所有 fresh 都读同一记忆库,无 context 漂移;但**强依赖落档纪律**(死前没写库的经验=丢,同"剪前先入库") | 会话内知识可能未落档即 rot/compact 丢 |
| 抗 rot | 结构性免疫 | 靠轮换阈值兜底 |

## 建议落地(最小三步,均在现机制上加,不动架构)

1. **E7 轮换 SOP 成文**(MasterAgent):落档清单 → 新会话 boot → boot-check 验 → 旧会话退。先人工触发,阈值自动提醒后补。
2. **send/route stale advisory**(MasterAgent,依赖 stale 度量先定义):目标 worker 超阈时 stderr 提示,不硬拦。
3. **Master 派活规则微调**(Master):杂活默认 dispatch(执行已有 doctrine)、主线活照旧 send;观察一段再定是否要更强机制。

> 相关:业务侧记忆 worker_stateless_fresh_context(发起方视角);本文 = 基建侧分析。拍板项:①是否按"混合规则"走(vs 硬规则);②E7 是否升优先级启动。
