# 立项备忘:在 DeepSeek Harness 上深改建 bus·GPLv3 开源

> 2026-08-17 用户令立项。状态 = **设计备忘(待用户拍板范围/优先级)**。MasterAgent 执笔。
> 源码基线:`C:\apphome\deepseek-harness`(dsh v0.1.0-rc.5·MIT·clone 于 2026-08-17)。

## 0. 一句话
把两年实战验证的 agent bus(指挥体系)+ 缺失的两中心(自动生产/激活调度),在 dsh 插件架构上重建为**开源 LLM 舰队平台**(GPLv3);现有 bus 降级为参考实现+需求规格,永不整仓开源。

## 1. 已拍板决策(用户令·2026-08-17)
- **D1 底座**:基于 DeepSeek Harness(dsh)深度改造,不从零造轮子。
- **D2 许可**:GPLv3 开源。MIT→GPLv3 单向兼容已核验(根 LICENSE+全包+vendor Cordis 全 MIT·许可干净);战略含义=我们的深改上游不能吸回 MIT 主干(单向护城河)。
- **D3 执行面**:**执行体进程依然是终端**;各终端启动的 agent client **不做限制**——可以是 Claude Code、Codex、或其他任意支持终端的 agent,按实际配置定。⇒ 控制面(dsh 深改)与执行面(异构终端 agent)彻底解耦,**客户端无关是平台第一基因**。
- **D4 数据边界**:开源的是全新代码;现有 bus 仓(内部路径/角色/记忆/usage.db)不出本机。发布动作按 egress 红线走用户放行+脱敏清单。

## 2. 定位:LLM 舰队 = 控制面 + 异构执行面
```
┌─ 控制面(dsh 深改·开源本体)────────────────┐
│ 指挥体系:presence/roster/caps/boot-check → Cordis 服务 │
│ 激活调度中心:触发器/预算闸/激活谱系                    │
│ 自动生产中心:孵化+战斗装配+质检回收                    │
│ 观测:Web UI(dsh-web-app 已有底子)                     │
└──────────┬─────────────────────────┘
           │ 跨进程契约(信箱+PTY)= 核心深改
┌─ 执行面(异构终端进程·不开源约束)─────────────┐
│ 终端1: Claude Code │ 终端2: Codex │ 终端N: 任意 agent │
└──────────────────────────────────────┘
```

## 3. 客户端无关契约(执行面接入分层)
最低公分母 = **文件系统信箱 + CLI 命令**(现 bus 已验证:任何能跑 shell 的 agent 都能 hello/wait/done)。按客户端能力分层适配:

| 层 | 客户端能力 | 接入形态 | 唤醒机制 |
|---|---|---|---|
| **T1 全能力** | 有 hooks + 后台任务(如 Claude Code) | 身份绑定+事件自醒+turn 观测全套 | 事件 wait(现 bus 形态) |
| **T2 基础** | 只能跑 shell 命令(如 Codex 等) | hello/peek/done 命令面 | **PTY stdin 注入唤醒**(控制面握 PTY 所有权,直接写提示进终端)——不依赖客户端任何特性,普适 |
| **T3 一次性** | headless 单发(claude -p / codex exec / dsh headless) | 生产中心孵化,done 即回收 | 无需唤醒(孵化即带任务) |

**关键洞察**:dsh 已有 `ctx.terminals`(持久 PTY·精确 agent 所有权·就绪检测·`packages/terminal/`)——控制面用它孵化并**持有**执行终端,stdin 注入=客户端无关的普适唤醒,这是 T2 层的解法,也让"掉线"问题从根上换形态(控制面持有终端=不存在失联的独立 wait 进程)。

## 4. 三中心 → dsh 机制映射
| 中心 | 我们的实战资产(spec 输入) | dsh 落点 |
|---|---|---|
| 指挥体系 | presence/roster/caps/boot-check/fleetlog/维修模式/退役档案 | Cordis 服务族(`ctx.fleet`?)+ 会话事件持久化(它的 append-only 日志比我们文件态更强) |
| 激活调度中心 | 禁轮询铁律/预算闸(token 限额窗口)/激活谱系(热→温→冷→排队)/one-shot 闹钟 | `ctx.jobs` + schedule 扩展点 + 新服务 `ctx.activation` |
| 自动生产中心 | dispatch_agent/claude_run/插件式记忆方向(战斗装配)/reap 质检回收 | `ctx.terminals`(孵化宿主)+ `ctx.subagent` seam(进程外委派先例=subagent-acp)+ 新服务 `ctx.production` |
| 跨进程信箱 | mailbox inbox/processing/done + wait 注册表 + yield-not-kill + **inject 不唤醒**(吸 dsh 三级唤醒 followup/steer/inject) | **新 seam**(我们最大深改+最大开源差异化):进程外 agent 的 inbox 语义 |

## 5. 许可合规清单(发布前逐项过)
- [ ] 保留 DeepSeek MIT copyright + 许可全文(逐包 LICENSE 不删);新增顶层 GPLv3 盖整体
- [ ] **改名换 scope**(商标:MIT 不授商标权,fork 不得叫 DeepSeek 系;上游 vendor README 自己也要求防 squat)
- [ ] THIRD_PARTY_NOTICES.md 逐条审计 GPLv3 兼容性(JS 生态 Apache-2.0/BSD/ISC 均兼容·风险低但必须过)
- [ ] 每个我们新增/深改文件带 GPLv3 头 + 改动声明(GPLv3 §5a)
- [ ] 发布 push = egress:用户放行 + 脱敏清单(内部路径/角色名/业务词零残留)

## 6. 分期(待拍板)
- **P0 骨架验证**:锁定 rc 基线;跑通 dsh web/headless;写"跨进程信箱"最小插件(文件信箱读写 + 一个 T2 PTY 孵化 demo:起一个终端跑任意 CLI、stdin 注入唤醒、done 回收)——**验证 D3 契约可行即算过**
- **P1 指挥体系插件化**:presence/roster/caps/boot-check → Cordis 服务;Web UI 舰队面板
- **P2 生产+调度中心**:孵化流水线(战斗装配=插件式记忆)+ 激活决策(触发器/预算闸)
- **P3 开源发布**:改名/合规清单/首版 GitHub(egress 放行门)
- 全程:现有 bus 照常运行不动摇(它是生产系统+对照组);新平台按 fork_exchange 思路吸收现 bus 持续进化

## 7. 风险
- 上游 rc 破坏性变更 → 锁基线、阶段性 rebase,浅协议先行深定制后置
- GPLv3 传导劝退商业插件作者 → 已知取舍,备选(AGPL/LGPL/双许可)仅备忘不改向
- 体量=产品级工程,与现 bus 维护并行 → 分期小步,P0 验证不过就止损
- 单进程 dsh 与跨进程舰队的阻抗 → P0 专门验证;subagent-acp 先例证明进程外委派在它架构里是一等公民

## 8. 未决(待用户)
- 新平台名字(改名义务已定,叫什么待定)
- P0 启动时机与资源分配(与现 bus 维护的并行度)
- 首个 T2 客户端选型(Codex?)用于契约验证
