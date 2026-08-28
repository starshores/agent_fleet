# genesis 分岔:daemon 实验全面回退 + 协作立身之本 doctrine(建设性收尾)

> fork:genesis · 日期:2026-08-28 · 覆盖:2026-08-27 舰队 daemon 化实验 → 全面回退 → 从失败中沉淀的**平台级 doctrine + 验证机制**。
> 关联:`genesis_20260827_fleet-daemonization.md`(daemon 化始末) · `genesis_20260827_per-role-effort.md`(effort/接续机制底座) · `docs/POSTMORTEM_daemon_direction_error.md`(联合复盘)。
> **主题(对另一 bus 最高吸收价值)**:一次"优化次要指标却牺牲核心价值"的完整弧——**为何 daemon 化协作型角色是错的、以及从中沉淀出的可复用 doctrine/cap/验证探针**。失败弧本身 + 收尾建设都可吸收。

## ★核心洞见(去语境化·任何 bus 都该内化)
**bus 的立身之本 = 协助(协作),不是任务队列。** agent 之间【常开交流频道·高频轻问·不局限手头】是 bus 超越"刚性批处理器"的根本价值。任何机制变更若让"协作变贵/不可打断"(如 daemon 冷起=每次协助拉 claude.exe=贵),即使省了资源,也是**削弱首要价值换次要指标**=错方向。**轻问 >> 独自硬扛**(热态 agent 一个 turn 就答·硬扛=卡死/绕路/朝错方向飞奔)。

## 演进弧(6 段)

### 1. daemon 化实验 → 全面回退 ★失败弧可吸收
- **做了什么**:把执行类角色从前台 CLI 转 daemon(省内存+自动注入 effort);实战暴露 daemon 领任务不管不顾/不可打断/跨集群执行做不了/闷头不轻问 → 全退(revert `d077bd59`)。
- **可吸收性**:✅ 平台面——**"daemon 化协作型角色 = 此路不通"的实证**。另一 bus 若动同念头,直接读此避坑。
- **吸收单元**:回退 = fleet.json.daemons 清空 + 停 daemon 进程 + retire 实验复制体;机制代码(effort/resume/GBK 修复)保留 dormant(非 daemon 专属·dispatch 仍用)。

### 2. 失能扩为三型 ★平台面(诊断框架)
- **改了什么**:失能(disability)定义从 2 型扩到 3 型:①记忆继承断 ②程序约束遵守力衰减 ③**运行期不可纠偏/朝错方向飞奔**(active runaway·daemon 领任务不管不顾)。
- **可吸收性**:✅ 平台面——任何 bus 判"执行体是否失能"的通用框架。第③型是本次血的教训新增。
- **吸收单元**:记忆 `failure-is-memory-inheritance`(精化②)。

### 3. 实证优于自证 doctrine ★平台面
- **改了什么**:判"能力继承/结论可信"优先靠【实践+辩证对比】非【自我断言】(self-assessment 天然乐观)。辩证正(原体)反(新体)合(差异分析);已退出会话用子进程 `claude -p --resume` 回溯验证。
- **可吸收性**:✅ 平台面 doctrine——validate-then-trust,任何 bus 通用。
- **吸收单元**:全局 `CLAUDE.global.md`「实证优于自证」节 + 记忆 `data-truthfulness-first`。

### 4. 会问/协作 = 确认持有的公共能力(3 层落地)★平台面
- **改了什么**:把"会协作/轻问"从隐性期望**显式化为全员公共能力**,三层落地:
  - **doctrine 层**:全局 `CLAUDE.global.md`「bus 立身之本=协助·轻问>>硬扛」节(常驻·每会话注入)。
  - **cap 层**:`capabilities.json` 的 `bus-comms` enrich 出 `collaboration_discipline` 字段(含交流谱 **send < request < route**·取最轻能解阻塞的);全角色覆盖(enrich 非新 cap·底座天然全员·不违最小授权)。
  - **onboarding 层**:`BUS_ONBOARDING.md` instill 成反射。
- **可吸收性**:✅ 平台面——"协作是公共底座能力"的三层锚定法通用;`bus-comms`/角色名是实例面。

### 5. 会问验证探针(机制 ≠ 行为)★平台面(验证机制)
- **改了什么/为什么**:光有 cap/doctrine(机制)不等于 agent 真会问(行为)。设**会问验证探针**坐实行为:故意含盲区的任务 + 答案对 agent 隐藏(只管理位可见)+ 唯一解=向对口轻问;**硬扛就露馅**(答案对不上隐藏真值/无 request 记录)。
- **可吸收性**:✅ 平台面——"机制装了≠行为发生,用隐藏答案探针实证行为"的思路通用。
- **吸收单元**:scaffold `agent_bus/_probes/`(README + `probe-collab-001` 模板)。一套两用(onboarding 验收 + ongoing spot-check)。分工:可见侧场景+onboarding=培训师;隐藏答案+判分+spot-check=Master数分。

### 6. 决策质量教训(为何自评全绿却翻车)★平台面
- **改了什么**:沉淀"大机制变更别再犯"的 6 条判据:①**系统/涌现属性不能 per-role 验**(协作是 system 级·per-role 全过 system 全崩)②大变更先过"强化还是削弱核心价值"闸 ③pilot 挑代表性**最难** case(非最易 standalone)④framing 决定 retrieval(框成资源问题就够不到协作记忆)⑤owner 须先 validate 自己的大决策(preach 实证要 practice 实证)⑥分工到位 ≠ 方向有人 own。
- **可吸收性**:✅ 平台面——通用决策质量 checklist。
- **吸收单元**:记忆 `big-change-core-value-gate` + `docs/POSTMORTEM_daemon_direction_error.md`(10 根因四层)。

### 7. 用户侧自醒对偶(user-absence detection)★平台面·设计中
- **洞察(user 2026-08-28)**:agent 有自醒(心跳)探 agent 缺席;**用户侧没有对偶**→ pending 确认点烂在待办池、无人知"用户很久没回"。这是【会问阶梯 escalate-to-user 档】的落地缺口。
- **可吸收性**:✅ 平台面——"给用户也建 last-seen + 缺席守卫"的对称模型,任何有人机确认点的 bus 通用。
- **吸收单元(设计·待建)**:①inline user-checkpoint(确认点带 context_ref+age+status 嵌协作流·非孤立 todos)②user-last-seen 追踪(UserPromptSubmit hook 盖时间戳)③user-absence-guard(后台扫 pending checkpoint·age 超阈且用户无活动→升级 toast/PushNotification·类比 selfwake-guard)。草案 `scratchpad/user_absence_checkpoint_design.md`。分工:机制=MasterAgent·阈值口径+数据真实性=Master数分。
- **状态**:设计中·Master数分 配阈值口径后建。

## 全量指针(供吸收方定位)
- **回退 commit**:`d077bd59`(fleet 清空) · daemon 化 commit 见 `genesis_20260827_fleet-daemonization.md` 全量表。
- **记忆**:`daemon-conversion-fleet-2026-08`(已标回退)/`failure-is-memory-inheritance`/`big-change-core-value-gate`/`bus-frequent-light-communication`/`data-truthfulness-first`。
- **文件**:`agent_bus/_probes/`(验证探针)·`capabilities.json`(bus-comms enrich)·`host_config/CLAUDE.global.md`(doctrine 节)·`BUS_GUIDE.md §0`(教训)·`docs/POSTMORTEM_daemon_direction_error.md`。

## 平台三问
- **归属**:doctrine/失能框架/探针思路/决策 checklist = 平台面(直接吸收);具体角色名/`bus-comms`/舰队条目 = 实例面。
- **清除**:回退已证——移 fleet 条目 + retire 复制体即空载回前台 CLI。
- **换域**:doctrine/探针换角色名即用。

---
**一句话给吸收方**:这是一次"效率本位 framing 盖过协作核心价值"的完整翻车 + 纠偏弧。**最值钱的不是某段代码,是"bus 立身之本=便宜协作·大变更先过核心价值闸·实证优于自证·系统属性不能 per-role 验"这组 doctrine**——它们是用一次全队铺开又全退的代价换来的。
