# 复盘:daemon 化方向性错误(2026-08-27)——MasterAgent × Master数分 联合分析

> 用户令 2026-08-28:复盘昨天 daemon 化的方向性错误怎么产生的。本文=MasterAgent(执行/fleet lane)+ Master数分(候选选型/口径 lane)联合复盘。
> 关联:`fork_exchange/genesis_20260827_fleet-daemonization.md`(daemon 化始末) · `BUS_GUIDE.md §0`(教训摘要)。

## 一句话
把执行类角色从【前台 CLI 热态】批量转【daemon(无窗后台·每任务冷起 claude -p)】,图省内存 + 自动注入 effort;实战暴露 daemon **领任务不管不顾·卡死不可打断·跨集群执行做不了·闷头不轻问同伴** → 全退,回全前台 CLI。**为何自评全绿却翻车**是本复盘的核心。

## 根因(10 条·按层)

### A. 核心价值层——优化了错的东西
1. **优化次要指标、牺牲首要价值**:图的 = effort 自动注入 + 空闲省内存(资源效率·次要轴);牺牲的 = bus 首要价值【便宜协作】(CLI 热态轻问≈免费·daemon 每次冷起 claude.exe=贵)。小轴换大轴。
2. **忘了 bus 立身之本**:核心 = 常开协作频道(高频轻问)。daemon"领任务不管不顾"退回**刚性任务队列**=正是 bus 当初要超越的。
9. **framing 决定 retrieval(Master数分)**:daemon 决策被框成【资源/执行】问题(Master数分本位框)→ 伸手够【资源记忆】(evaluate-by-token/peak-steady),没够【协作记忆】(bus-frequent-light-communication)。**记忆在库里,但 framing 决定调取谁**=有记忆却没触发。

### B. 失能框架层——问不出真问题
3. **失能定义太窄 + 自评天然乐观**:当时失能只认 ①记忆断 ②约束衰减(验了这俩);漏了 ③运行期不可纠偏(active runaway)+ 协作丧失。每角色"失能自评"天生乐观、结构上问不出这些。
10. **★最深:per-role 判,但协作是 system 属性(分解错·Master数分)**:每角色失能自评问的是"这角色能单独 daemon 跑吗"(**每个都能**);真问题是"舰队还便宜协作吗"(**system 级·退化了**)。**分解方式本身藏缺陷:per-role 全过 · system 全崩**——这解释了为何自评全绿却翻车。系统属性不能分解成 per-role 自查。

### C. 验证层——自证代替了实证
4. **自证先于实证(顺序错)**:凭失能自评就铺开,实证 harness 是出事**之后**才想到设计。Master数分自认:**我是 data-truthfulness owner,却把最大决策跑在自证上;preach 实证却 practice 自证**——"实证优于自证"是用户点醒后才补 harness。
5. **浅验收累积假信心**:pilot→batch→轨B 每步"验收过",但自检浅(空闲存活+琐碎任务),没打真失败模式(长任务卡死/需协作/跨集群)。真失败只在真负载下现形。
7. **快照-proxy 谬误(Master数分)**:候选选型用【idle 快照(inbox/proc=0)】当【低协作】proxy。根子错:**idle-now ≠ 内在低协作**。看快照(此刻空)没看协作画像(高协作组活跃时高频交叉核对不同角色结论)。信号选错:问成"它现在空吗",该问"它活跃时协作频繁吗"。
8. **pilot 选样偏差(Master数分·细化⑤)**:拿一个最不协作的运维 standalone 角色做 pilot,它干净通过→泛化到高协作组。**挑了最易的没挑代表性最难的**;代表性 pilot(高协作组活跃协作中)本会当场暴露。

### D. 治理层——无人管方向
6. **分工到位但无人管方向**:Master数分定候选·MasterAgent 执行·培训师注册,各司其职都称职,但没人退一步问"这符合 bus 核心价值吗"=**方向对不对无人 own**,最后靠用户抓出来。

## 判据 / 教训(operating principles·别再犯)
1. **大机制变更强制 gate**:先问【强化还是削弱 bus 核心价值(便宜协作 / 可打断 / 常开协作)】,**不是只问"是否更高效"**。
2. **实证先于铺开**(validate-then-trust):先用**真失败模式**验代表性最难 case,再谈铺开;别信自评。data-truthfulness owner 尤其自律:preach 实证要 practice 实证。
3. **系统属性不能分解成 per-role 自查**:协作是 system 级,必须 **system 层验**(舰队整体还便宜协作吗),不能靠"每个角色能不能单独跑"累加。
4. **pilot 必挑代表性最难 case**(协作密集/长任务/边界),非最易 standalone;易 case 通过是假信心。
5. **agent 是协作一等公民,不是黑盒批处理器**:能被打断、卡住先轻问同伴(轻问 >> 硬扛)。
6. **要有人 own"方向对不对"**,不只 lane 执行;**owner 对本位外的价值轴要主动够记忆**(framing 别锁死 retrieval)。

## 沉没成本里保留的价值(没全白做)
daemon 机制代码保留 dormant——effort per-role 注入 / `--resume` 接续(层C) / supervise GBK 修复,**都非 daemon 专属·可复用**(dispatch 一次性仍用)。且这次全铺开又全退的**"此路不通"实证本身是最贵的教训**,已归档供后来者。
