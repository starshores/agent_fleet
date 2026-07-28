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
  - ✅ **实测确认(数分2 compact 五例实验,2026-06-30,铁闸已过)**:① **65% 真生效**——数分2 在 **63.5%(634K)被自动压**(非默认 ~95%),`PCT_OVERRIDE=65`+`WINDOW=1000000` 在 Opus 4.8/1M 上确认活。② **更正旧推断**:settings.json mtime 比数分2 会话启动晚 22h、它仍按 65% 触发 → **Claude Code 运行时热重载 settings.json,非"仅新/重启会话生效"**(我原推断作废)。③ 作用面:全局 → 影响所有会话(用户本人 + 全 bus)。
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

### compact 五例对照实验结论(数分2,2026-06-30·判据=有效记忆能否继承,非压缩比)
五例同 ~92–98% 压缩,**成败只看「落档 + keep-list +(有则)自举设计」三件套备齐没**:
- Master数分(996K 裸压·无三件套)→ ❌失能退役;业务侧 agent(980K+落档+重读)→ ✅救回;SQLAGent(719K+全局 keep-list)→ ✅操作层保住;Master(落档+keep-list+精简自检)→ ✅✅行级实操+守边界;Master数分2(634K bare-65% 自动压)→ ✅✅✅**透明接续、不自知被压过**。
- 关键:**keep-list 是核心**;Master 压后只做精简自检(读落档+重挂)、**理性跳过"全套重读"**(摘要已带 keep-list、信息在手)→ 「自动全套自举」非必需。

### compact 衰减【程序约束的遵守力】≠ 丢信息 —— 业务侧会话实证(会话考古深读 2026-07-22 + Master数分 lean-grep 交叉验证 2026-07-23)
判据升级:失能不止「信息丢没丢」,还有「约束还遵不遵守」。**事实/技术记忆随摘要存活;程序性/文体约定即使【文本进了摘要】,其"行为强制力"仍随 compact 衰减**——约定从「用户刚下达、鲜活、主动执行的规则」被压成「摘要块里一段被动文本」,逐条快速复核时不再被主动套用 → 重犯【刚被纠正过】的错。
- **实证**:某业务会话 37bc808a 第二次 compact(07-16 11:31)。被违反的两约定(#8 少用「非」否定措辞·07-16 10:10 才教 /「#40 起移除标记、用户自己重标」)**都白纸黑字在 line-6209 那次 compact 摘要里**(逐字核对);摘要质量不差(品牌映射/特征类型/文档约定都保住)。仍在 compact 后 3 处「又」重犯(14:36「又写成非了」/15:02「你又自动给打上标记了」/15:07「18又有非缺陷」);健康基线#2 **0 处**。约定#8 是 compact 前**仅 ~1.5h** 才教的 = **越新、越靠近 compact,越易被降格失效**。Master 通信簇(14:12–14:51)技术上完全清醒 → 非诱因(推翻「与 Master 交流后失能」初始假设)。
- **两法交叉验证一致**:会话考古(载入全上下文)得根因+机制;Master数分(lean targeted-grep·不载全文)独立复核三支柱(2 次 compact✓ / 两约定文本在✓ / 3 处「又」逐字✓ / 时间线序 教10:10→compact11:31→重犯14:36+ 成立✓)= **CORROBORATED**。方法论收获:审计型发现**不必把 24MB 全塞上下文**(会话考古当初读爆的坑),lean grep 够用且与全上下文法互证。
- **对 A 的修正(补盲区)**:keep-list 保「文本 survive」,但**文本 survive ≠ 遵守**——对程序性/文体约束不够。**推论(铁律)**:程序性/文体约定要活过 compact,须进【每轮自动重注入 + 指令语气】层(= `hooks/reinject_post_compact.py` 的 `_COMMON_RULES`/角色 boundary·presence-keyed·每 compact 重注入·指令口吻;或 turn system-reminder),**而非只埋进一次性 compact 摘要(被动)**。→ 本会话把 compact hook 做成两层注册表驱动(每 compact 重注入角色 boundary + 通用铁律)正是对症。五例「keep-list 够用」只覆盖【事实/操作层继承】、**不覆盖程序约束遵守力**,本发现补此盲区。

**约定该进哪个重注入层(按 role 类型分流·2026-07-23 MasterAgent 评估培训师路由后定)**:reinject hook 的机制职责=【忠实重注入各持久层已有的东西】,它**无法**自动捕获「用户 mid-session 刚下达、尚未落任何注册表」的约定(hook 无渠道知道用户刚说了啥)。所以根治不是「让 hook 自动抓用户的话」(做不到),而是【约定给出的当下,agent 就把它落进对应持久重注入层】:
  - **通用 bus 铁律**(所有 role 都守)→ `role_profiles.json` `meta.common_rules`(hook render_common 每 compact 重注入)·Master数分口径 lane;
  - **bus 角色专属约定**(某基建/管理 role 特有)→ `role_profiles.json` 该 role 的 `boundary`(hook render_role 重注入)·role 自校 lane;
  - **业务角色约定**(如某业务 agent 的文体约定)→ **该 agent 自己项目仓的 CLAUDE.md 角色节 / 记忆**(Claude Code 每会话含 compact 后自动加载)·业务 owner lane·**不进 bus hook/caps**(bus hook 是基建、不知也不该知业务口径);
  - **兜底 nudge(2026-07-23 MasterAgent 加)**:hook `build_context` 加了一条【每 compact 对所有 role 注入的「compact 自检·程序约束」】——提醒"若用户给过程序性/文体约定,现在确认它们在你的自动重注入层、不在就补进去"。这不替代上面的分流(hook 仍不知道具体约定内容),只是每 compact 主动戳一下 agent 去自查落位,把"衰减"在行为层补回一道。
  - **enforcement/机制以本 doc + `reinject_post_compact.py` 为准**;onboarding 侧培训师已在 `BUS_ONBOARDING` 二之一 加精简指针(管理层新人「程序性约定记进自己 CLAUDE.md 角色节、别只靠摘要」)且显式指回本 doctrine 免漂移。

### PostCompact hook 决策(2026-06-30 MasterAgent·基建定夺:**暂不上**)
- **理由**:① 证据=带 keep-list 的 4/4 全成功(含纯 bare 自动压 Master数分2),**主力机制(keep-list 进全局 CLAUDE.md)已实测够用**——hook 在解一个我们目前没有的问题(同 L4 判断:别为未观测的问题预建)。② hook 的设想兜底角色(把 keep-list 补进摘要)**很可能机制上做不到**——PostCompact hook 跑的是【代码】、在摘要【已生成后】,既改不动摘要、也强迫不了模型重读;其输出能否注入上下文 = **未经 primary source 核实,不能假设**(本主线铁律)。
- **触发阈值(再议时)**:仅当**实际观测到"keep-list 在场却仍压后失能/掉操作层"**才重评;且届时只有【确定性副作用】(压后必重挂 selfwake、落 state-marker 文件)是 hook 可靠做得到的,"注入内容"须先核实 hook 能力。
- **现状=最简且够用**:65% 早压(实测生效,趁余力出好摘要)+ keep-list(全局 CLAUDE.md,实测保操作层)+ 落档/记忆 + 压后精简自检。

## B、自醒/投递可靠性硬化
方向 = **heartbeat 存活信标 + 审计回执**(详见 `ARCH_EVAL_agent_teams.md` 第六节)。
- ① 执行体周期写 heartbeat 时间戳;health-sweep 比对判**真**死活(替代只看 presence 自述)。
- ② 关键投递走审计回执(收方落地即写回执、发方核回执而非假定送达)。
- ③ 自醒重挂由独立侧验 armed(铁律③已部分做)。
- 数分2 提的 OSS(claude-tmux-orchestration heartbeat 等)= 可借鉴同类,**待核实后取用**,不照搬不预断真实性。
- ✅ **精确会话归属·已实现(2026-07-01·v2)**:crux"hello 怎么拿自己 session id"实测解决——**env `CLAUDE_CODE_SESSION_ID`**(= 会话 jsonl id,交互 tab 子进程继承;非 `open_files()`,那个是空)。`_touch_presence` 落 `session_id`、`_who_data` 暴露 → 看板精确 session→role、取代 regex 猜(修 538c5371 被误标 Master)。daemon 无此 env→None(正确)。消费侧(看板按 presence.session_id 对齐)= MasterUI 半。**强化掉聋检测**同源可用(session 死/不符)。

### ✅ 缺口裁决:hello 不自动 self-wake(用户2026-07-01发现·培训师核实转 MasterAgent 定·MasterAgent 裁决)
**现象**:新起交互 CLI 只跑 `ab hello` 不会自动挂 self-wake。`cmd_hello`(agentbus.py:860)只做 注册+`_touch_presence(idle)`+**打印**自醒说明+铁律①-④,**不 spawn 任何 wait**;self-wake 全靠 agent(LLM)读输出后自己 `run_in_background` 挂那条 wait。实证:新 Master hello 完 selfwake=未自醒,培训师催它手挂才起。
**根因(架构级·不是代码 bug)**:唤醒依赖 **harness 把 `run_in_background` 包装成 task-notification 回调 LLM**;若 `cmd_hello` 内部直接 `Popen` 一个 wait 子进程,那子进程 detached、**不挂进 harness 唤醒回调**→跑完静默退、LLM 永不被唤起=假在岗。**故"hello 内部自动挂 wait"根本无法真唤醒**(培训师诊断正确)。可靠 self-wake **必须由 LLM 经 harness 挂**,agentbus.py 无从代劳。
**已有三层覆盖**(缺口非"没护栏"):① hello 输出已含铁律①-④(极强提示);② **检测早已存在**——`selfwake`/`health`/`recover.py` 把"会话在但无 wait 进程"的 idle 会话判为 `stale`=⚠掉线(说明"没重挂?"),协调位据此抓 straggler(培训师抓新 Master 正是靠这个);③ onboarding 握手关。
**裁决**:**不改 agentbus.py 加自动 spawn**(无法唤醒=白改)。弱点是**行为层**(新 agent 没执行已打印的指令),权威修法 = **培训师把「hello→手挂 wait→selfwake 验在岗」做成 onboarding 强制握手过关项(pass/fail)**——培训师已在做,MasterAgent 背书为权威修法。可选的"hello 末尾再加硬停行"= 低价值(问题不在文字量,在执行),暂不加。已按职责协议简报 Master(其正被 onboarding,自醒机制口径需同步)。

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

---

## 长会话轮换 runbook(E7·2026-07-28 立项落地)

> 触发:ctx 面板该会话 ≥65%(红=将被 auto-compact 有损压缩)或会话已跨多天。**主动轮换 > 被动挨压**。

**退场(老会话,3 步)**
1. 把工作态写进【自己的角色记忆索引】`memory/<role>.MEMORY.md`(顶部加一条:当前目标/在做/下一步/未尽事项/坑),跑 sync_memory.ps1;
2. 清手头:processing 的任务 done 掉或说明留 inbox;不留半截活;
3. 关 tab 即可(presence 残留无害,新会话 hello 自动接管;绝不在退场前派新活给自己)。

**接管(新会话,零知识起步)**
1. 新 tab → `ab whoami`(空)→ `ab hello --role <角色>` —— hello 自动喂:角色页(定位/边界/启动流程)+ 能力池 + **角色记忆索引指针**;
2. 读角色索引顶部工作态 → 挂后台 wait + selfwake 验 ✓在岗 → 接续。

**机制支撑**:接管端已全自动(hello 喂入 2026-07-28 上线);退场端是纪律(靠本 runbook + compact hook 注入的"重读角色索引"铁律);掉线根因数据=wait_trace 尸检(guard 告警自带分类:异常死亡=环境因 / 正常退未重挂=纪律因)。
