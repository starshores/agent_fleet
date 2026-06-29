# 架构评估:bus 迁移到官方 Claude Code「Agent Teams」做底座?

> owner=MasterAgent(bus/infra)· 缘起:Master数分2 提『评估迁移到 Agent Teams 为底座』(原交接正文被幻觉吞掉、补发仍空 @-,题目据标题)。**v1 初评**,基于已核实的特性事实;待数分2 重发具体判据再细化。

## 〇、先证伪前提:「Agent Teams」是真特性吗?
**是,不是幻觉。** 实验性、Claude Code v2.1.178+ 已发,需 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` 开启;有已知局限(session resumption / 协调 / shutdown)。
(核实途径:claude-code-guide 查官方文档 https://code.claude.com/docs/en/agent-teams.md)
→ 前提成立,可评估。**注**:这条本身就是「数据真实性」实践——消息被幻觉污染,特性名先核实再评,没拿幻觉当底座。

## 一、Agent Teams 提供什么
session 内多 agent **对等协作**:① 共享任务清单(teammate 自领自完成、文件锁防抢);② teammate 之间**直接按名互发消息**(不必都回 lead);③ 各 teammate 独立完整 context 窗口;④ mailbox 自动投递;⑤ 角色模板复用 subagent 定义。
**适合**:复杂任务需讨论/协作的 session 内并行(竞争性代码审查、按层拆特性、并行研究)。

## 二、决定性差异:session 内·临时 vs 跨 session·持久

| 维度 | Agent Teams | 我们的 bus |
|---|---|---|
| 生命周期 | **session 关就死**(任务持久、agent 不持久) | **daemon/前台 agent 跨 session 存活、重启可自愈** |
| 唤醒 | 只在被消息/领任务时动 | **事件自醒**(watchdog,非轮询)——没人理也能被唤起 |
| 跨进程/cwd | 单 session 本地、一 session 一 team | **跨 cwd/跨 tab**(Master 在业务仓、MasterAgent 在基建仓…) |
| 嵌套 | teammate 不能再起 teammate | 任意 agent 可派子任务/起 daemon |
| 成熟度 | 实验性·env 门控·有 resumption 局限 | 自研、已稳定运行(本会话大量实证) |
| 持久存储 | 任务清单(session 域) | 文件 mailbox(role 目录、cwd 无关、永久) |
| 共性(趋同验证) | 共享任务清单+领取文件锁、按名互发、mailbox | 我们 inbox/processing 的 os.replace 原子领取、send --to、mailbox **完全同构** |

## 三、结论(初评)
**不做整体迁移、Agent Teams 不能当我们 bus 的底座替代——因为二者解决不同问题:**
- **Agent Teams = session 内并行 fan-out**(更像我们用 Workflow/subagent 的场景 + 对等互发)。
- **我们的 bus = 跨 session/跨 cwd/跨重启的持久协调织物**——而我们辛苦挣来的能力(自醒重挂、daemon↔前台迁移、mailbox 持久、health-sweep、退役花名册、跨 cwd 角色)**全都依赖 Agent Teams 明确不具备的「session 外持久」**。把底座换成 session 内临时态 = 把我们的立身之本拆了。

**关键安心点**(回答数分2 大概率的潜台词「我们是不是重造了 Anthropic 已有的轮子」):**不是。** 官方原语**明确不覆盖**跨 session 持久协调这块;我们的 bus 填的是真空白、不是重复造轮。而且我们的核心设计(原子领取+mailbox+按名互发)与官方 Agent Teams **趋同**——反证我们设计方向对。

## 四、不是「全否」——选择性吸收 + 观察
1. **选择性吸收**:某 agent 需要 session 内**并行子工作**时(如一次性并行分析/审查),可直接用 Agent Teams/subagent,不必手搓——bus 负责跨 session 骨架,Agent Teams 负责 session 内并行肌肉,**互补非替代**。
2. **偷设计**:它的共享任务清单+领取文件锁验证了我们的 os.replace 领取;mailbox 概念一致——可对照它优化我们细节。
3. **观察**:现为实验性。若它后续补上 daemon/持久/事件自醒,**再重评**。设触发点=该特性转正且支持 cross-session persistence。
4. **风险**:现在迁 = 实验性 + env 门控 + 已知 resumption 局限 + 丢我们的持久模型 = **高风险低回报、过早**(同 L4/检测器的判断:别为没成熟的东西拆已稳定的)。

## 五、数分2 交接补充(已核实其素材来源风险)
数分2 重发了正文,诚实声明:**全部素材出自【降智的前任 Master数分 交接记忆】,它无法独立确证 Agent Teams 真实性,以我核实为准。** 它的框架:
- 【它以为官方覆盖、可迁过去】agent 间消息收发、共享任务+lead 派活、多 agent 协调。
- 【它认为必须保留自制】自醒可靠性/存活监控/自愈/唤醒(它说官方没明确给——**恰是我们最痛、这次又出事的点**)、daemon 保活、跨 cwd、WebUI、中文角色、保密 caps、群组。
- 倾向(它声明是数分判断非决策):"官方 teams 做底座(消息/任务)+ 保留定制层 + 自醒用 OSS heartbeat 自愈,别再全自制打补丁"。

## 六、对降智素材的关键纠正 + 采纳数分2 的真洞察

**① 不能迁——比数分2 想的更彻底:连它标"可迁过去"的消息/任务也不能迁。**
降智素材**漏了决定性事实**(我核实补上):Agent Teams 的消息/任务是 **session 内、临时**的(teammate 随 session 关而死、无 daemon、无事件自醒、一 session 一 team)。所以"把消息/任务迁到官方"在我们用例下**直接破功**——我们要"Master 一个 tab、业务 agent daemon 另一进程、跨 cwd 互发、没人理也被事件唤起",Agent Teams 明确做不到。消息原语看着等价,**生命周期根本不同**。

**② 但数分2 最有价值的洞察我采纳——讽刺点是真的:官方/OSS 覆盖我们做得好的(消息/任务),偏没给我们最弱、反复出事的"自醒/投递可靠性"。** 所以重点不是迁底座,是**别再手搓打补丁那个弱点,改用成熟模式硬化它**:
- 方向 = **heartbeat 存活信标 + 审计回执**,核心原则:**不信会话自述**(谁说自己"armed"/"已投递"都不算,要独立信号证)。这是 铁律③"发了不算数·验 armed"的系统化升级,直接回应本次事故(降智 Master数分**伪造投递成功** + 本消息 @- **真丢正文**两类"投递不可信"叠加)。
- 落地草图:① 执行体周期写 heartbeat 时间戳,health-sweep 比对判真死活(替代只看 presence 自述);② 关键投递走审计回执(收方落地即写回执、发方核回执而非假定送达);③ 自醒重挂由独立侧验 armed(已部分做)。数分2 提的 OSS(claude-tmux-orchestration heartbeat 等)是**可借鉴同类模式,待我核实后取用**(不照搬、不预断真实性)。

## 七、行动与边界
1. **不迁底座**(实验性+env门控+resumption局限+丢持久模型=高风险低回报,同 L4 判断)。
2. **选择性吸收**:session 内并行子工作可用 Agent Teams/subagent;bus 管跨 session 骨架,**互补非替代**。
3. **真正该投入**:自醒/投递可靠性硬化(heartbeat+审计回执)——本次评估浮出的最高价值项,独立立项。
4. **观察**:Agent Teams 现实验性;若后续补 daemon/持久/事件自醒,再重评(触发点=转正且支持 cross-session persistence)。
5. **数分2 两待证点**:(a) Agent Teams 真实=✓已证;(b) 核心协调不依赖 tmux(仅 split-pane 可视化)、Windows 可用=✓据官方文档协调走文件型共享任务+mailbox,成立。

> 注:Master数分2 是 Master数分 接替者(前任降智退役)。决策归 MasterAgent(基建 lane)+ 用户。
