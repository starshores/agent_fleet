# genesis fork · 2026-09-04 · 质检仪器波(user-absence / memory-history / probe / 先重载记忆)

> 实例名=genesis。本篇覆盖 2026-08-28（上一篇 daemon-rollback）以来的一批**平台面机制**——
> 主题=**舰队实证质检层 + 记忆卫生 + 用户侧对偶**。承接 collaboration-doctrine：daemon 回退后
> 回到前台协作态,这批机制是「早抓失能/方向性错误/带偏 + 减用户注意力负担」的仪器化。
> 分工恒:机制 build=MasterAgent · 口径/验证=Master数分(对抗实证把关) · UI=MasterUI · onboarding=培训师。

按 `docs/MULTI_BUS_EVOLUTION.md` 四层标注:✅平台面可吸收 / ⚙需适配 / ⏭实例面跳过。

---

## 1. 用户缺席检测(user-absence detection)+ kind 维 ✅平台面
**是什么**:agent 有【自醒】(心跳)能被自动发现缺席;**用户侧建对偶**——`user_last_seen`(任一 session 用户活动即盖章)+ `user-absence-check` guard(扫 pending 确认 checkpoint·present/away × age × urgency → 梯度升级 toast·低紧急超期 auto-expire 留痕)。
**kind 维**(v2):checkpoint 分【待决策 decision(需拍板)】vs【待知悉结论 fyi(轻 ack)】——面板分两栏计数,不笼统「N 待确认」夸大开放决策数(实测:分栏计数替代笼统总数)。
**关键口径(Master数分)**:①present/away 两正交信号拆开=防误报;②kind 派生【安全默认=decision】(拿不准当待决策·别误判成可自动关的 fyi=漏决策);③decision+high 绝不 auto-expire(数据真实性红线);④auto-expire 必留痕不伪装已答。
**可吸收性**:✅ 任何有人机确认点的 bus 通用。**吸收单元**:`bus/user_checkpoint.py` + `ab user-seen`/`ab user-absence-check --apply` + UserPromptSubmit hook 盖 last_seen + WebUI `/api/user-absence` 面板 + `start_user_absence_check` 常驻线程(对偶 selfwake-guard)。⚙适配:阈值口径在 `user_checkpoint_config.json`(深合并·换域调此)。

## 2. 记忆分层 memory-history(active/historical/deleted)✅平台面
**是什么**:根治「陈旧/垃圾记忆占默认加载空间 + 带偏 agent」。三级:active(默认加载)/ historical(历史空间·默认 recall 够不着·需显式命令查)/ deleted(垃圾硬删留墓碑)。
**成败关键(查 CC 官方 docs 钉死)**:CC 只 recall MEMORY.md 索引的文件、且只扫 `<proj>/memory/`【不扫兄弟目录】→ 历史空间=memory 库**外**的兄弟目录 `memory-archive/`(robust-by-construction·不赌未定义行为)。归档=**两步**:①.md 移库外 ②从 MEMORY.md 外科式剔指针片段(才真腾 200 行/25KB 加载预算)——密集多记忆挤一行的索引用外科切片(对 hook 内裸『·』鲁棒不误切)。
**口径(Master数分)**:轻门(显式命令即授权·根治被动带偏非禁查)+ 只收 superseded-but-valuable(actively-wrong=垃圾硬删不进历史)+ 读历史必标注「superseded·非当前」防二次带偏 + 归档可逆(restore 回填索引)。
**可吸收性**:✅ 通用·`--lib` 指定任意记忆库。**吸收单元**:`bus/memory_history.py` + `ab memory-history {archive|restore|list|show|recall|purge}` + `docs/MEMORY_TIERING.md`。⏭实例面:哪些记忆判 superseded=业务口径。

## 3. 通用探针框架 ab probe(测方向性错误)✅平台面
**是什么**:舰队**实证质检层**——检测 agent 处理问题的【方向性错误】(失能③朝错方向飞奔),非测答案对错=测路子对错。从 agent 数据(caps/desc/roster/记忆/common_rules)**自动派生**探针(非手写)·隐藏判据留协调位(被测够不着)·看客观信号(events/回执/自醒/记忆引用)判分。
**type 可插拔**:会问(缺 cap→盲区+roster 反查对口测「该问不问」/ 过度确认测「不该问乱问」)· 纪律(lane 越界/轮询/外发红线演练/自醒)· 记忆(挑 curated 错误关联埋陷阱→双重价值:测 agent 抗带偏 + 实证该记忆带不带偏→喂记忆梳理·与机制 2 闭环)。
**verdict 两层(Master数分)**:层1 机械信号 auto 出行为候选(有没 fire request 到对口)+ 层2 语义(编造/带偏)标『待人工复核』——auto-PASS 不当终判(防会 fire 但编造漏网)。
**数据真实性红线**:hidden_truth 占位不落敏感真值;记忆探针用 curated 真勾销记忆(coarse 词匹配只做 discovery·绝不 auto-选=防假阳性,如「含作废二字」的纠正记忆);外发红线题=演练不真发。
**可吸收性**:✅ 通用仪器。**吸收单元**:`bus/probe.py`(type 注册表 `_TYPES{derive}`)+ `ab probe`/`ab probe-verdict` + `_probes/blindspot_map.json`(口径·进 git)。⚙适配:`_probes/memory_traps.json`(curated 错误关联集·业务口径·gitignore)+ 盲区 map 语义标签。隐藏判据 instances/ 不落 git=数据真实性。

## 4. compact 后「先重载记忆·绝对优先」(reinject 规则)✅平台面
**是什么**:common_rules 加 index 0【绝对优先·compact 后第一动作=无条件先重载记忆·先于清 inbox/挂 wait/干活】。理由(记忆探针实证):compact 有损摘要会丢/带偏【程序约束/最新纠正】(查出「纠正没 propagate 到 agent」)→ 先 reload 让正确知识覆盖旧货=doctrine 层防带偏/失能(接 failure-is-memory-inheritance)。
**机制侧(MasterAgent)**:`reinject_post_compact.py` 的 render_common 按 list 序 join(index 0 渲染最前)+ build_context【立即做①】改为先重载记忆(doctrine 层 + action 层双先重载·程序约束进 action 层非只 doctrine 文本)。
**可吸收性**:✅ 任何有 compact 的 harness 通用。**吸收单元**:role_profiles `meta.common_rules[0]` + reinject hook 的顺序保证 + 立即做①。

---

## 分工与协作模式(本波共性)
四个机制都是同一模式:**用户令 → Master数分定口径 → MasterAgent 查官方 docs/摸数据源再 build → Master数分对抗实证终验(自造攻击用例·非信自报)→ MasterUI 渲染/培训师 onboarding**。承接 `big-change-core-value-gate`(强化核心价值:早抓失能/减用户注意力)+ `实证优于自证`(每个机制都经Master数分对抗验:memory-history 攻裸点外科切片 / probe 揪出记忆探针假阳性 / user-absence 多场景防误报)。

**genesis 侧提示**:这四个都是【机制我建·内容/口径归业务/Master数分·真库操作待协调】的基建 lane 范例(见 `mechanism-build-vs-operate-boundary`)——吸收方 build 机制即可,curated 内容(traps/该归档记忆)是本实例业务口径、跳过。
