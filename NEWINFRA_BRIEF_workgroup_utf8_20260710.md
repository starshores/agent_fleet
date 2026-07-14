# bus 新基建 · 数据支撑简报(使用方 → MasterAgent · 抄送 用户/Master)

> 2026-07-10 · Master数分2 出数据+需求(1+1 的数据半)· MasterAgent 出机制设计+实现(基建半)· 用户/Master 拍板。
> 背景:用户观察「团队规模像在用一个 agent」「Master/示例组员 交付慢、执行等待、plan token 平白流失」。下述全部实测,非推断([[data-truthfulness-first]])。

---

## 零、一句话
bus 现在是**串行接力链**、不是**并行工作池**;要进「工作组池化」新基建,同时把**控制台盲发(UTF-8 显示)**一并根治——多 agent 能并行 + 并行时看得见彼此,是同一件事的两面。

---

## 一、实测:为什么「像在用一个」
- **全 fleet 有效并行系数 = 1.74**(近7天,2分钟桶):60.4% 的活跃时间**同一刻只有 1 个 agent 在产出**,中位并发=1,≥4 并发仅 ~9%。
- **日 token 结构**:cache_read(重读上下文)占 **98.4%**,真 output 仅 0.19%。→「日 token ≈ turn 数 × 每turn上下文」,是重读量、不是工作量;峰值 1.17B/繁忙日 0.6–0.9B 的天花板由 turn 数(墙钟/单机串行/人节奏)+ 65% 压顶(每turn上下文<650K)共同钉死。plan 不是被浪费花掉(空/阻塞等烧 0 token),是**并行容量闲置**=机会损失。杠杆是**并行度**,不是让单 agent 更快、也不是加 agent。

## 二、实测:示例组「加了 2/3 没提效」的真相
| 成员 | busy 时长 | 占组比 | 近7天认领 |
|---|--:|--:|--:|
| 示例组员(原) | 13.0h | **81%** | 112 |
| 示例组员2 | 1.7h | 11% | 24 |
| 示例组员3 | 1.3h | 8% | 14 |

- **组内并行率 ≈ 10%**(≥2 成员同时 busy 的时长占比);**组有效并行系数 ≈ 1.10** —— 3 成员的组实际当 1.1 个用。
- 病灶:①活全压原 示例组员(81%)=**无负载均衡**;②三者几乎从不同时跑(10%)=**无并发 fan-out** + 上游串行喂料。加成员进串行槽 = 多两个偶尔接溢出、平时挂着等的备胎。
- 任务生命周期(佐证):派→领**中位 0.0min**(自醒即时,排队延迟不是主因);慢在①示例组员 62% 任务领→交 2–30min(多步/阻塞下游 sql_agent/远端),②尾部 claimed-没-done(Master 7 条 >2h、最长 45h,多在 selfwake-guard 上线前)。

## 三、需求 ①:工作组池化机制(数据反推,每条挂一个数)
1. **负载均衡路由**(治 81/11/8):可寻址「示例组**组**」,bus 路由到**最空闲成员**,不按名字默认砸原成员。
2. **并发 fan-out 原语**(治 10% 并行率):一个逻辑任务拆 N 独立分片 → 一次撒全组并行 → 汇总回收。
3. **上游异步派发**(治串行喂料):Master/请求方一次并发撒 N 条、异步收,取代 send-一条-等回-再send。
4. **组内沟通/互助 + 共享上下文**(用户原话"并行沟通协助"):成员认领分片不撞车、可互相接力;共享组记忆让任一成员接任一分片=真·可互换池。

**池化判据 & sizing(数据支撑)**:
- **只有真·可互换的复制体才组池**(同 caps)。示例组员/2/3 ✓。反例:监控角色 vs 指标角色(用量/报表)专长不同=不可互换、不是池。分组前按 caps 核。
- 到达节奏:发组任务**中位间隔 7.6min、20% 成批爆发(<2min)**。爆发窗是池化回本处 → **池大小按爆发峰定、不按均值**。
- 收益估:均衡到 33/33/33 且 fan-out 把并行率 10%→~60%,该组 1.1 → ~2.5–3×;fleet 1.74 随之抬。

## 四、需求 ②:UTF-8 显示根治(消除盲发交流)
控制台中文乱码使 bus 协助在**盲发盲收**——agent 得绕开控制台读原始 JSON 才看懂消息,这个"绕开"就是症状。根因单一:**Win console=GBK/cp936、Python stdout/stderr 默认走 GBK,而 bus 只补了 agentbus stdout 一处**。精确缺口:
- ✅ agentbus.py `line 33` 已强制 **stdout**=UTF-8 → `peek/replies/selfwake/who/wait` 正文能读(不是全盲)。
- ❌ **stderr 没修**(line 33 只补 stdout)→ 警告/错误/提醒全 garble(如 `⚠ 目标未在岗` 糊成 `⚠ Ŀ��`)=出问题时最需读的恰恰瞎。
- ❌ **worker_daemon.py / dispatch_agent.py 一处 reconfigure 都没有** → daemon 形态 + 派发路径全 garble。
- ❌ ad-hoc 脚本(内联 python/PowerShell)不各自修就 garble,无全局保证。

**修法(便宜、统一)**:
1. agentbus.py 补 `sys.stderr.reconfigure(encoding="utf-8", errors="replace")` 一行。
2. worker_daemon.py / dispatch_agent.py / claude_run.py 顶部加同款 stdout+stderr reconfigure 块。
3. **系统级一劳永逸**:bus 启动环境设 `PYTHONUTF8=1`(或 `PYTHONIOENCODING=utf-8`)进 config.json 启动环境 / 各 launcher → 覆盖所有 bus 起的 python,连 ad-hoc 都保。**最值。**
4. 可选:launcher `chcp 65001` 兜底真·GBK 控制台。

**为什么①②是一件事**:并行工作组核心是"密集沟通协助",**可读的 inter-agent 通信是前提**;stderr/daemon 盲=组内协调在最需看清彼此/报错时瞎。

## 五、职责边界
- 使用方(我):出数据 + 需求 + 修法清单(本简报)+ 上线后验证并行度/盲区是否消。
- MasterAgent:机制设计 + 实现(组路由/fan-out原语/组mailbox/共享上下文;UTF-8 补丁)。
- 用户/Master:拍板范围、优先级、哪些角色组池。
- 关联记忆:[[peak-steady-resource-philosophy]] [[bus-frequent-light-communication]] [[bus-iteration-1plus1-channel]] [[specialization-route-not-self-handle]]
