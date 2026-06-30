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

**对策(按性价比;均待用户 t-1782724980778 拍板,先不动)**:
- **① 主干·零风险**:操作层命令小抄(`ab send/wait --role/done --id/peek/selfwake` 确切语法 + 铁律③)放进**自动重载的 CLAUDE.md**。**放全局 `~/.claude/CLAUDE.md`** = 所有 cwd 的 agent 都吃得到、不依赖任何 env、本地必生效。数分2 估单此挡 ~80% compact 失能。
- **② 加固**:PostCompact hook 自动重读 AGENTS.md/CAPABILITIES.md(先确认 hook 本地真触发)。
- **③ 治本·强制自举**(待办 t-1782724980778 同源):compact 后 agent 重跑自恢复(重读 recovery doc、重验命令)。**比"早压"治本**——早压(`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`)只改丢失【时机】、不减损失,且文档称本地 tab 可能不生效。
- **④ 软保险**:CLAUDE.md 加 "Compact Instructions" 段,指示摘要保住确切命令 + 未尽事项。
- **弃**:调 compact 比例(系统固定);单靠早压(本地未必生效 + 不减损失)。

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
