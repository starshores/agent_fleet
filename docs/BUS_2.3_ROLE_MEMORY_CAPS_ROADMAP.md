# BUS 2.3 路线图 —— 角色 / 记忆 / 能力(用户立项 2026-07-28)

> 用户令:「新的版本我想围绕角色/记忆/能力来做优化」+「该读没读的问题要在新版解决」。
> 基线 = v2.2(维修大修版:工程质量层已硬化);2.3 主题 = **认知装载层**——agent 上岗/运行中
> "该知道的真知道了吗"从提示词约定升级为**可验证机制**。owner 默认 MasterAgent,标注处除外。

## P0 —— 该读没读(headline·用户点名)

**问题**:hello 喂入只能"告知清单",走没走完靠自觉——实证:新会话被问"角色和能力都加载了吗"
才发现两份必读文件没读。提示词层(boot 自检闸·v2.2 已加)只是软化,新版要**确定性验证**。

**方案(已选型·Read 追踪 + boot-check 核验)**:
1. **session_reads 采集**:PostToolUse hook(扩展 turn_state.py·零新 hook 注册)——bus 成员会话每次
   Read 工具落盘 `<role>/session_reads.jsonl`(session_id + file_path + ts,append-only,按会话滚动)。
   哪些文件真被读过从此**可观测**,不靠 agent 自述。
2. **boot_reads 声明**:role_profiles.json 每角色加 `boot_reads: [必读文件…]`(角色页/喂入/核验同一源)。
3. **`ab boot-check --role X`**:比对 boot_reads ⊆ 本会话 session_reads → ✓装载完成 / ✗列缺失清单。
   hello 尾部提示跑它;培训师握手改为核 boot-check 结果而非口头问。
4. **软闸联动**:未 boot-check 通过的会话领活时 warn(同培训闸手法·不硬拦防死锁);WebUI 角色页显
   每会话装载状态。

## P1 —— 角色

- **角色页完备度体检**:role_profiles 各角色补齐 startup_flow/boot_reads/boundary(现有条目粗细不一;
  培训师协作);新角色注册时缺字段即提示。
- **角色生命周期一页通**:hello→带训→结业→运行→park→轮换(E7)→退役→复活 全链在 WebUI 角色页
  可视(MasterUI);retired 档案卡与舰队日志交叉引用。
- **策略注册表续深**:P1 遗留的"守卫伪造 from / bus-router 伪角色名单"随 meta.policy 扩展收口。

## P1 —— 记忆

- **记忆分锅完备**:业务库(XXX)也按角色分锅(现只 agent_fleet 库分了;Master/业务角色共锅依旧);
  孤儿记忆巡检(索引没指到的 .md)进卫生代谢。
- **记忆装载核验**:boot-check 同机制覆盖角色记忆索引(<role>.MEMORY.md 也是 boot_reads 一员)。
- **记忆写入纪律自动化**:PostToolUse 已有 on_memory_write 重链;补"新记忆落角色索引"的漂移检测
  (写了 .md 却没进索引 → 提醒)。

## P1 —— 能力

- **caps 池与角色页联检**:agents.json caps ↔ capabilities.json 池双向核(池里没有的 cap/挂了池却无
  desc 的角色 → 体检报告);登记归培训师、机制归 MasterAgent(既定分工)。
- **能力→必读映射**:capabilities.json 每个 cap 可挂 `reads`(掌握该能力该读什么),boot_reads 自动
  聚合 = 角色必读清单不再手维护。

## P2

- 装载状态进 fleet_state(顶栏可见"有会话未完成装载");
- 舰队日志自动记"新会话装载完成"事件(train/boot 维度的编年史)。

## 非目标(明示不做)

- 不做硬拦(boot 未完成禁止领活)——防培训师/文件缺失死锁,沿用软闸+可观测路线;
- 不引入外部存储/服务,全部文件总线原语。
