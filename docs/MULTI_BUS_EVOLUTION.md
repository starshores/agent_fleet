# 多 bus 进化转移 —— 设计备忘(未立项·供未来起第二个 bus 时用)

> 2026-08-06 · 用户提出问题:"若有多个 bus 各自进化,怎么转移进化内容"。本文=设计备忘,
> 当前只有一个 bus,**不建同步机制(YAGNI)**;真正的准备=把"未来可转移"变成现在的纪律(见 §4)。

## 1. 先分层:进化内容四类,可转移性完全不同

| 进化内容 | 载体 | 可转移性 |
|---|---|---|
| 代码 + 机制 + 文档 | git(v 标签/changelog/发版 zip) | git 天生解决:多 bus=fork,转移=remote 互拉 + merge/cherry-pick |
| 注册表 | 平台面(bus_mechanisms/role_profiles 通用/capabilities)vs 实例面(config/agents/fleet) | 平台面可同步;**实例面绝不同步**(本机路径/在册角色/舰队) |
| 记忆(经验) | 各角色 memory 库 | 最难:文件粒度可合并,内容带实例语境 → 走"经验包 curation" |
| 运行态 | usage.db / events / mailbox | **不转移**(各自方舟);全局视图只做 BI 联邦聚合层 |

## 2. 各层转移机制

### 2.1 代码/机制/文档(git 层)
- 多 bus = git fork;补一个内部 bare remote 互拉,`git log vA..vB` 即进化增量清单;发版 zip(agent_fleet_releases)= 离线转移介质(转移前剥离实例数据:usage.db/运行态队列)。
- ⚠ **egress 红线前置**:跨机同步=数据离开本机,须用户对该同步通道一次性授权界定(通道、方向、内容范围)。

### 2.2 注册表(先分面再同步)
- **平台面**(可同步):bus_mechanisms.json(机制清单)、capabilities.json(能力池定义)、role_profiles.json 的通用角色模板部分、hooks、冒烟金标准。
- **实例面**(绝不同步):config.json(本机路径)、agents.json(在册角色/pid 态)、fleet.json、pid_tags、presence。
- **bus_mechanisms.json 是现成的"进化索引"**:两个 bus 各自机制表做 diff → 立刻知道对方进化了什么、自己缺什么;按机制为单位 cherry-pick。"新机制必进注册表"纪律在多 bus 场景回报翻倍。

### 2.3 记忆(经验包 curation,不做自动同步)
- **直接 rsync 记忆库 = 双向污染**(同名分叉冲突 + 实例语境失效)。
- 可行路径:像退役 agent 档案那样**人工/专责策展**——把可迁移教训提炼成去语境化 pattern(如"kill-churn 振荡"机制级教训可迁;"某盘某路径 junction 坑"不可迁),打成"经验包"(一批带 frontmatter 的 .md + 索引),对端按角色评审后并入。
- 冲突规则:同名不同内容 → 人工裁决,不自动覆盖(数据真实性纪律)。

### 2.4 运行态(联邦不合并)
- usage.db 各自累积(不同实例的消耗史,合并无意义);要全局 BI → 聚合层各自出日聚合再汇总,原始 db 不动。

## 3. 转移单元与节奏
- **最小转移单元**:一个 vX 版本(tag + changelog + 冒烟基线)或一个机制(注册表条目 + 对应 commit 串)。
- **节奏**:按版本批量转移 > 按 commit 零散转移(冒烟基线只在版本边界有保证)。
- **验收**:转移侧跑齐两套金标准冒烟(bus_smoke + ui_smoke)+ 平台三问(归属/清除/换域)。

## 4. 现在就要做的纪律(唯一 actionable)
1. 平台面/实例面/业务面三分持续执行(平台纲领已在做);
2. 新机制必进 bus_mechanisms.json;
3. changelog 连续(版本边界清晰);
4. 教训类记忆写作尽量去语境化(把"哪台机哪个路径"与"机制规律"分开写)。

> 做好这四条,将来多 bus 转移成本 = git merge + 注册表 diff + 经验包评审,无需专门同步基建。
