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

---

# 二、具体设计深化(2026-08-06 续·用户令"思考工人问题")

## 5. stale 度量:三信号,compact 为主判据

| 信号 | 含义 | 数据源 | 现状 |
|---|---|---|---|
| **S2 compact 次数** | 有损压缩已发生几次 | `reinject_post_compact` hook 在 compact 时必跑 → 顺手原子 bump `<role>/compact_count.json` | **待加一行**(hook 生命线,动前广播) |
| S1 ctx 占用 | 距 65% 自动压还有多远 | `build_ctxpanel`(session_id 精确绑定 role,pct/level/margin **已有**) | 已有,零新建 |
| S3 任务数 | 距上次 fresh 已办几单 | events.jsonl claim 计数 by role since presence.started | 可算,不新增采集 |

**判据**:`stale = compact_count ≥ 1`(主)或 `ctx level ≥ yellow 且 tasks ≥ N`(辅)。
为什么 compact 是主判据:对照实验实证【文本 survive ≠ 遵守】——程序约束遵守力随 compact 衰减,压过一次的 worker 已是有损态;ctx 占用只是"将要有损"的前瞻。**最优轮换时机 = S1 红档、压缩发生之前**(主动轮换 = 无损交接;等 compact = 已损)。

## 6. E7 轮换 SOP(可靠 reset 的操作化)

1. **触发**:S1 red(压前主动·最优)/ S2 ≥1(已损·尽快)/ S3 ≥N(兜底);先人工触发,advisory 自动提醒后补。
2. **落档**(旧会话):在飞任务与下一步写工作态档;新经验落角色记忆库 + 索引行;能 done 的先 done。
3. **交接**(新 tab):`hello --role X`(presence 换绑新 session_id)→ boot 流程(角色页+记忆索引+必读)→ `boot-check` ✅。
4. **接管耳朵**:新 tab `wait --role X --timeout 0 --takeover`——**必须 --takeover**:旧会话的 wait 还活着时,普通重挂会让位给它(512064a yield 语义),消息会被聋会话领走。
5. **旧会话退出**;`selfwake` 验新会话 ✓在岗收尾。
> 顺序铁律:先落档 → 新 tab 接管 → 旧退。短暂无执行体可接受(inbox 不丢),双执行体不可。

## 7. advisory 接线(不硬拦)

- **WebUI 派活表单**:route chip 旁加 stale 徽标(ctxpanel 已有 per-role pct/level,纯前端消费)。
- **`ab send` stderr 提示**:CLI 侧读 `<role>/compact_count.json`(S2,文件读=便宜;S1 的 jsonl 扫描 CLI 侧太贵不做),目标 stale 时提示"建议 dispatch 或让其轮换",与 `_warn_unreachable` 同位同风格。
- 豁免:requires-approval / 交互确认活 / 会话粘连活照常 send。

## 8. 实施顺序(待拍板后)
① hook 加 compact_count(一行原子写;hooks=生命线,动前广播)→ ② E7 SOP 试点一个角色(候选=compact 过的常驻)→ ③ send/表单 advisory → ④ 观察一个周期再定是否收紧为半自动轮换。

---

# 三、插件式记忆(用户定方向 2026-08-06)

> 用户原话方向:"保护 basic + 按问题选择性启用记忆填充上下文后思考 + 完成任务后整理记忆清理上下文"。
> 定位:**E7 是进程侧(怎么重置),插件式记忆是状态侧(重置后什么留下)——合起来才是无状态 worker 完整闭环。**
> 核心收益:basic 受保护 + 记忆及时整理后,**compact 从"失能风险"降级为"缓存失效"**,context 变成可弃缓存。

## 9. 四环节 × 现状 × 缺口

| 环节 | 现状 | 缺口 |
|---|---|---|
| ① 保护 basic(内核) | reinject hook compact 重注入身份+纪律;role_profiles 角色页;boot-check 机器验 | 仅差把 basic 边界写成正式口径:**basic = 角色页(role_profiles)+ 公共纪律(common_rules)+ 角色记忆索引(<role>.MEMORY.md 索引行)**——索引在 basic 内、正文在 basic 外,这是"插件目录 vs 插件本体"的分界 |
| ② 按问题选择性启用(插件加载) | 索引行制度(一行 hook·按需 Read 展开)+ CC harness 自动 recall | **接活 recall 步骤缺失**:接到任务先按任务关键词扫自己索引 → 只展开命中条 → 再动手。先做成纪律(onboarding 任务模块 + role_profiles 边界句),机制化(任务 JSON 带 mem_hints / 接活钩子提示)后补 |
| ③ 完成后整理(写回) | 落档纪律存在,无任务边界节点 | **done 边界整理动作缺失**:done 前问三件——新经验落库了吗 / 过时条更新了吗 / 工作态要不要留档。E7 轮换时为硬节点(SOP 第 2 步),日常任务为软纪律 |
| ④ 清理上下文(GC) | 会话内无法真清(§6 已析) | **不需要清**:③做到位后 context 即可弃——轮换(E7)或放任 compact(basic 由①保护)皆可,rot 不再累积成失能 |

## 10. 与每任务 fresh 的成本对比(为什么这个更优)
- 每任务 fresh:每单付全量 boot + 冷缓存(§4 已否)。
- 插件式:basic 常驻(小·受保护)+ 按题展开(只付命中条)+ 整理后可弃——**token 花在命中的记忆上,不花在重复背全库或拖着烂 context 上**。索引行制度本来就是为这个优化的,方案是对现有架构的"补两个动作",不是新架构。

## 11. 并入实施顺序
原①~④不变,追加:⑤ 接活 recall + done 整理写进 BUS_ONBOARDING 任务模块与 role_profiles(纪律层,培训师协作);⑥ 观察后再议机制化(mem_hints / 钩子提醒),不先建。
