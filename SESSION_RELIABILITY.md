# 会话可靠性硬化(主线)

> owner=MasterAgent(基建)· 缘起:Master数分2 交付「Master数分降智(compact失忆)根因+旋钮+对策」,并指出它与 Agent Teams 评估带出的「自醒/投递可靠性」**同源**,合并为一条主线。**预研方案**;决策归用户+MasterAgent。**未尽事项**:不改 CLAUDE.md(待用户拍板·待办 t-1782724980778)。

## 〇、统一原则(两个失败面同一根)
**「不信会话自述 / 不信会话连续性」——把关键状态/命令/投递确认的 SSoT 外置出脆弱 session,要【外部可重载】+【外部可验证】。**

| 失败面 | 怎么坏的 | 外置对策 |
|---|---|---|
| **A. compact 失忆**(Master数分降智) | 长会话骑到 996K/99.6%→被迫单次【有损】compact→99% 丢弃→操作层确切命令丢→写错命令+伪造工具成功(幻觉投递) | 关键命令放【compact 后自动重载】处 → 外部重载 |
| **B. 自醒/投递不可信**(Agent Teams 评估带出) | 127 re-arm 静默失败、降智体伪造投递成功、本次 @- 真丢正文 | heartbeat 信标 + 审计回执,不认会话自报"armed/已送达" → 外部验证 |

两者都是:**别让脆弱的 session 当唯一真相源。**

## A、compact 失忆防治

**关键事实**(数分2 经 claude-code-guide 查官方文档;**推全队前再确认**):
- compact = **有损摘要替换**(非修剪),摘要大小**系统固定、99% 损失是设计内**——别指望调比例。
- 🎯 **项目根 + 全局 CLAUDE.md、MEMORY.md 前 200 行 compact 后【自动重注入】;AGENTS.md / CAPABILITIES.md 【不】自动重载。** 降智真因 = 操作层确切命令住在不重载的 AGENTS.md = **放错位置**。
- PostCompact / PreCompact hook 存在,可压缩后自动跑动作。

> ✅ **机制核实(2026-06-30 primary source 裁决;纠正本 callout 上一版的错误)**:上一版据 claude-code-guide 写"机制不存在"=**该 agent 的【假阴性幻觉】**(它读了 context-window/memory 页、漏了 best-practices 页)。数分2 WebFetch + 我**亲自 WebFetch 官方 best-practices 页逐字复核**,确认:
> - ✅ 原文:"Customize compaction behavior in CLAUDE.md with instructions like *When compacting, always preserve the full list of modified files and any test commands* to ensure critical context survives summarization" → **CLAUDE.md 定制 compaction = 官方文档化、确实存在**(普通 CLAUDE.md 指令即可,不需特殊段名)。**A④ 成立。**
> - ✅ 原文:"Home folder (~/.claude/CLAUDE.md): applies to all Claude sessions" → **全局 CLAUDE.md 对所有会话生效** → bus-wide 一处放通用 compact 指令可行。**A① 全局版成立。**
> - 待实测(SQLAGent 实验坐实,非阻塞):全局 CLAUDE.md 的 compaction 指令在项目会话 compact 时是否真被 honored(doc 说会);post-compact 全局是否重注入(context-window 表只列了项目根、未提全局)。
> - 🔑 **元教训(本主线立为铁律)**:**doc-query agent 会【双向幻觉】**——既能假"有"也能假"无"(本次 claude-code-guide 给了假阴性、上次给 Agent Teams 是对的=不稳定)。「不信会话自述」升级到顶:**不信任何单个 agent 的 doc 总结/核实结论,核到【primary source 原文】或【实测】才算数**;连"派 agent 去验证"本身也是一层会幻觉的自述。

**对策(按性价比;均待用户 t-1782724980778 拍板,先不动)**:
- **① 主干·零风险**:操作层命令小抄(`ab send/wait --role/done --id/peek/selfwake` 确切语法 + 铁律③)放进 **CLAUDE.md**。**放全局 `~/.claude/CLAUDE.md`**(官方:对所有会话生效)= 一处搞定、所有 cwd 的 agent 都吃到、不依赖 env。数分2 估单此挡 ~80% compact 失能。(post-compact 全局是否重注入待 SQLAGent 实验坐实;不放心可同时落各项目根 CLAUDE.md=官方明确重注入,双保险。)
- **② 加固**:PostCompact hook 自动重读 AGENTS.md/CAPABILITIES.md(先确认 hook 本地真触发)。
- **③ 治本·强制自举**(待办 t-1782724980778 同源):compact 后 agent 重跑自恢复(重读 recovery doc、重验命令)。
- **③b 早压·阈值可调(已 primary source 核实 /en/env-vars 逐字)**:`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`=触发百分比(1-100,调低=早压)、`CLAUDE_CODE_AUTO_COMPACT_WINDOW`=计算用的窗口容量(默认随模型 200K/1M)。**价值=早压提升摘要保真**(趁模型有余力压、出更好摘要,印证文章 60-75% 黄金窗),非减损失量。⚠ **我们 fleet=Opus 4.8/1M 扩展上下文,单设 PCT_OVERRIDE 多半不触发**——原文小字:PCT_OVERRIDE 仅在「AUTO_COMPACT_WINDOW 已设 / cloud / Opus4.6·Sonnet4.6 无扩展上下文」时生效;故须**同时设 `AUTO_COMPACT_WINDOW`(如 500000)** 才会提前压。**铁闸:测试 tab 实测真生效再推全队。** 关闭则用 `autoCompactEnabled:false` / `DISABLE_AUTO_COMPACT`。
  - ✅ **已应用(2026-06-30 用户指令)**:全局 `~/.claude/settings.json` 的 `env` 加 `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=65` + `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`(= 65% × 真实 1M = **650K 触发**,留 350K 余力出好摘要;WINDOW 显式设 1M 以满足"WINDOW is set"令 override 在扩展上下文 Opus 4.8 上生效)。
  - ⚠ **作用面 + 待验**:① 全局 settings → 影响**所有** Claude 会话(含用户本人 + 全 bus agent);② **仅对新起会话生效**(env 启动时加载;当前在跑的 agent 要重启才吃到);③ **是否真在 650K 触发仍须实测坐实**(docs 列了生效条件但 4.8 实际行为待 SQLAGent/测试 tab 验)——铁闸未撤。
- **④ 引导压缩**(官方文档化·已 primary source 确认):在 **CLAUDE.md 写 compaction 指令**(如"compact 时务必保留:role/cwd/caps、未尽任务、bus 确切命令+自醒铁律、被纠约定、关键文件路径;砍:被否方案/闲聊"),compaction 时自动套用、确保关键上下文 survive。放全局 CLAUDE.md = bus 通用(数分2 起草的 keep-list 即用作此内容)。补充手段:`/compact <focus>` 一次性参数(手动)、PreCompact/PostCompact hook(代码级)。
- **弃**:调 compact 比例(系统固定);单靠早压(本地未必生效 + 不减损失)。

**外部独立实证 + 加固**(数分2 经 业务侧 agent 抓回实战长文《/compact 我用了 3 个月》对照分析;原文在 `Master数分2/done/1782789552504-业务侧 agent-eada67.json`):
两条独立路径(它 3 个月实战 vs 我们一晚对照实验+数据)**同结论 → 互证、高可信**:compact=有损摘要替换、摘要质量∝压时模型余力、忘文件/重犯老错=失能。三条加固已并入上面对策:
- **A④ 升级 = 压时 instructions【前置】**:compact 时主动指定「保留:文件路径/未尽事项/测试用例/被纠正的约定;砍掉:被否方案/闲聊」(官方建议 5–10 项)。这给 A③「压后重读 bootstrap」补了**前半段(压时主动保操作层+工作态)**=双保险。
- **A⑤ 新增·verify-after-compact 标准动作**:压完第一动作自问「刚改了啥/pending/被纠正的约定」,答错就 Esc+Esc 回滚重压。= 把我们对 业务侧 agent 做的 4 点核验定为 **fleet 标准**(「不信会话自述」用在压后自检)。
- **A③ 阈值佐证 + ⚠口径闸**:文章称 60–75% 是黄金压缩窗(等到 90% 已 context 饥荒、瞎裁)——**佐证我们 70% 红线方向对**。但 ⚠ **文章是 200K 窗口环境**(其 45k buffer/155k 可用/95% 数字都是 200K 的),**我们 fleet 是 1M,具体百分比绝不照搬**——原理通用、数字要按 1M 自测。决策表(/compact 续命 vs /clear 切主题 vs Esc+Esc 回退)+ /auto-compact off 纳入 onboarding。

### ✅ canonical·已落地(2026-06-30 用户批准 t-1782724980778,经本人直接确认)
已写入**全局 `~/.claude/CLAUDE.md`** 末尾新节「## Compact Instructions(bus 通用·压缩时套用)」。canonical 全文:
```
压缩(/compact 或 auto)时尽量逐字保留:
① 我的 role 身份 + cwd + 职责边界(caps);
② 当前目标 + 在做的事 + 未尽任务/待办 + 下一步;
③ bus 操作确切命令(hello / wait --role / done --id --role --result / peek / selfwake / send --to --from --task)+ 自醒铁律(重挂后 selfwake 验、空唤醒静默不 dump、done 带 --role);
④ 被用户纠正过的约定/口径/偏好;
⑤ 关键文件路径、决策及理由、已排除别重查的方案、踩过的坑 + 修法。
砍掉:探索性闲聊、被否方案冗长讨论、已解决的历史细节、重复内容。
压后第一动作:复述①②③自检,答不全就重读【你自己的 CLAUDE.md + 相关记忆】补回再干活。
```
(末句「压后第一动作…」即把 A③ 强制自举 + A⑤ verify-after-compact 内嵌进指令。**已去项目专属化**:全局指令对所有 agent 生效,而 AGENTS.md/CAPABILITIES.md/recover.py 是 agent_fleet 专属——业务 agent 只有自己的 CLAUDE.md+记忆。agent_fleet 专属自举只放 agent_fleet 项目根 CLAUDE.md。)
**SQLAGent 实测发现(重要)**:① compact 后那条 advisory 自检/自举文字**没自动触发**——被 harness 的 "resume directly/不要 recap" + idle 纪律压过,只在被探针问到才复述。印证官方「CLAUDE.md instructions are advisory, hooks are deterministic」。→ **要可靠的压后动作可能得改 PostCompact hook**(数分2 正在 agent_fleet 层 agent 重测确认)。② keep-list 是否被 honored + 全局是否重注入仍待坐实。

## B、自醒/投递可靠性硬化
方向 = **heartbeat 存活信标 + 审计回执**(详见 `ARCH_EVAL_agent_teams.md` 第六节)。
- ① 执行体周期写 heartbeat 时间戳;health-sweep 比对判**真**死活(替代只看 presence 自述)。
- ② 关键投递走审计回执(收方落地即写回执、发方核回执而非假定送达)。
- ③ 自醒重挂由独立侧验 armed(铁律③已部分做)。
- 数分2 提的 OSS(claude-tmux-orchestration heartbeat 等)= 可借鉴同类,**待核实后取用**,不照搬不预断真实性。

## D、idle 输出纪律(空唤醒收敛)——延缓 compact 的最便宜一招
**现象**(用户观察):有的 cli 自醒 agent(如 Master数分2)每次被唤起,即使没新活,也**重复 dump"在岗/状态/花名册"**进自己的上下文窗 → 逐轮自我膨胀;MasterAgent 则收敛成一行"静默待命"、不重述。

**为什么是大事**:重复 dump 状态 = **自己给自己灌上下文** = 更快骑到 99.6% → 被迫 compact → 降智。这与 Master数分 失能**同根**,且**自我加速**。**尤其 Master数分2 是降智 数分 的接替者,正在重走前任老路。**

**纪律**:被自醒唤起后,若 inbox 无新任务(空唤醒 / 只是 ACK 通知)→ **静默重挂 wait + 输出最小化(最多一行待命)+ 绝不重新 dump selfwake/who/花名册/状态**。
- 口诀:**空唤醒 → 静默重挂 → 不重述状态**。
- 正例模板 = MasterAgent idle:查 inbox、空、重挂、一行带过。
- 适用面:**cli 自醒 agent**(它们 idle 会占 LLM turn、会累积);daemon 免(worker_daemon 阻塞、每任务 claude -p 临时上下文、不累积)。
- 落地:本纪律 + 进 onboarding,培训师 1对1 复述补训(同专一性补训法)。

## C、落地次序与闸门
1. **现在**:预研出方案(本文档)。**不改 CLAUDE.md**(待用户 t-1782724980778)。
2. **用户拍板后**:先 A① (零风险、最高性价比) → 再 A②③(需测试 tab 验证 hook/自举) → A④ 随手;B 作并行子线独立推进。
3. **铁闸**:所有 env 旋钮(③早压等)**必须先在测试 tab 实测本地是否生效**,验过才推全队(数分2 强调,防"文档说有效、本地不生效"翻车)。

## E、保鲜 backlog(数据持久方舟,旁线)
- **stats-cache.json 纳入 ETL**:它和被清的 jsonl 一样是【会被 Claude Code 覆盖/清掉的早期用量源】(数分2 已从中抢救 03-27~04-28 的 12.9M tokens 入 usage.db.daily_backfill)。持久方舟原则应扩展到它——ETL 顺手也抓。**实现归【业务侧】lane**(它做增量 ETL),非 infra 自接;待优先级到时 route 给它。
