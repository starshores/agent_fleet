# bus 新基建 · 实施方案(MasterAgent 机制设计+实现 → 使用方/用户/Master)

> 2026-07-10 · 对 [NEWINFRA_BRIEF_workgroup_utf8_20260710.md](NEWINFRA_BRIEF_workgroup_utf8_20260710.md)(使用方 数据+需求)的机制/实现答复。
> 分工:MasterAgent 出机制+实现;使用方 上线后验并行度/盲区;用户/Master 拍板范围·优先级·哪些角色组池。
> 分期:**Phase 0(UTF-8 根治)见效快 → 已实现**;**Phase 1(工作组池化)设计如下,待拍范围后建**。

---

## Phase 0 · UTF-8 显示根治 —— ✅ 已实现(commit 待提)

按简报四缺口逐一补,自包含、不依赖 env 传播:

| 缺口 | 修法 | 状态 |
|---|---|---|
| agentbus 只补 stdout、**stderr 漏**(⚠警告/报错 garble) | `agentbus.py` 加 `sys.stderr.reconfigure(utf-8)` | ✅ |
| **worker_daemon** 零 reconfigure(daemon 全 garble) | 顶部加 stdout+stderr reconfigure 块 | ✅ |
| **dispatch_agent** 零 reconfigure(派发 garble) | 同上 | ✅ |
| **claude_run** 复用体无保 | 加 `import sys,os` + reconfigure 块 | ✅ |
| ad-hoc python 无全局保 | 各入口 `os.environ.setdefault("PYTHONUTF8","1")` 传子进程 + `config.json` 加 `env:{PYTHONUTF8:1}` 中心声明 | ✅ |

**实测**:`import agentbus` 后 stdout=stderr=utf-8、PYTHONUTF8=1;4 文件语法过、config.json 合法。
**注**:控制台**显示层**仍可能 GBK 花(终端 codepage,非数据)——修的是 python **发出 UTF-8 字节**,bus agent 用 UTF-8 管道读即正确(盲发根因=以前发 GBK 字节,现已发 UTF-8)。

**待用户/Master 可选拍板(Phase 0 增强)**:
- 是否在 `start_worker.ps1` / WebUI launcher / 用户环境设 `PYTHONUTF8=1` 覆盖**所有** ad-hoc(含 agent 在 bash 里内联跑的 python)。设用户级=一劳永逸但影响非 bus python;设 launcher 级=仅 bus。**默认只做了进程内 + 子进程传递,未动系统/用户环境**(改系统环境属宿主机器、需用户点头)。

---

## Phase 1 · 工作组池化 —— 机制设计(待拍范围后实现)

### 核心洞见:一个原语统摄 4 需求 = 【共享组 inbox + 成员 pull-claim】
简报 4 需求(负载均衡/fan-out/异步派发/组内协助)本质是同一件事——**把"按名 push 给固定成员"换成"共享队列、空闲成员自取"**。以 pull 为核最省最稳:

- **负载均衡(治负载不均)**:活丢进**组共享 inbox**,谁空谁 pull(原子 claim 已防撞车)→ 天然均衡,不砸原成员。比"路由算最空闲成员"push 更稳(无需实时探每个成员忙闲,pull 自带背压)。
- **并发 fan-out(治并行不足)**:一逻辑任务拆 N 分片全撒进组 inbox → 多个空闲成员同时 pull 不同分片 = 真并行。
- **上游异步派发(治串行喂料)**:Master 一次把 N 条丢进组 inbox 即走、不等 → 组自行消化。
- **组内协助/共享上下文**:组共享记忆目录 `BUS/<group>/shared/`,任一成员 pull 任一分片都有上下文 = 真·可互换池。

### 定稿硬约束(用户方向 + 使用方 精炼 2026-07-10)
1. **运行时自加入**(取代静态配组):`ab join-group <组>` / `ab leave-group <组>`,**不改配置、不重启**;加入=开始监听/pull 该组 inbox,离开=停。成员数按爆发峰临时涨缩。组成员是**运行时状态**(presence.groups[] / `BUS/<组>/members.json`),不是 fleet.json 静态配置。
2. **状态外置 = 池化地基,共享上下文非可选**:用户点破——同类复制体分开命名本为防同名撞身份 + 记忆交接;而 **65% 自压后已不靠"脑内记忆接续"**(恢复=再培训+重新指导,非脑内搬运)。既不依赖成员脑内状态,就把状态**外置到共享存储**(组记忆/文件/任务 payload)→ 任一成员接任一活。故**共享上下文存储契约必须在 1a 就定死**(1c 的富功能随后,但契约前置)。"分开命名=不可池"的旧顾虑本质是"状态锁在 agent 脑内",外置即解。
3. **组任务三层语义**(按"状态在哪"分,非二选一):
   - **默认·丢组 inbox(可互换)**:任务自带/引用**全部上下文** → 谁空谁 pull-claim。绝大多数活走这条。
   - **会话粘连(session-affinity)**:某成员已 pull 多轮线程、持活工作态 → 后续路由回同一成员;但它掉线/超时 → **自动回落组池**别人接(复用 reap→requeue,改投组 inbox)。既并行又不丢连续。
   - **点名直投**:仅需特定专长(非同 caps 可互换)时用 = 例外非常态。
   - 原则:状态外置越彻底 → 粘连越少 → 池化越纯。

### 实现分层(架构一步到位·1a 地基须能长出 1b/1c·不搭 MVP 捷径)
- **⚠ claim 原语实证修正(2026-07-10 gate① 压测)**:并发 pull-claim **必须用 `os.open(<task>.lock, O_CREAT|O_EXCL)` 独占创建锁**(谁建成锁谁赢→再移 task 到自己 processing;败者 FileExistsError=干净落空续等)。**实测本机 Windows 上 `os.replace` 争同源并非单胜(200-300 轮出 2-8 胜者=会双跑)**,O_EXCL 恒 1 胜。现有 `_claim` 的 os.replace 手法只在单消费者/幂等wait下没暴露,多消费者组 inbox 绝不能沿用。
- **1a 地基(先落地验收)** = 组注册(**运行时 join/leave**)+ 共享组 inbox + **O_EXCL 原子 pull-claim**(上条)+ 成员双听(自己 inbox + 所属组 inbox,watchdog 加监视目录,复用 `_wait_core`)+ `ab send --to-group <g>` 丢组 inbox。**并须在 1a 就定**:①**组任务 schema**(强制自带/引用上下文,不许依赖脑内状态)②**共享上下文存储契约** `BUS/<组>/shared/`(即使富功能留到 1c)③会话粘连的 affinity 字段 + 超时回落组池的语义。`ab join-group` 时按 caps 核一致(不一致拒/警)。**仅 1a 即治负载不均 + 让新成员真分担**(空闲即 pull)。
- **1b fan-out / collect**:`ab fanout --group <g> --shards <json|->` N 分片打 `fanout_id` 撒组 inbox;`ab fanout-collect --id <id> [--wait]` 收齐 N 个 done;`ab scatter`=不收尾的批量异步派发糖。
- **1c 共享组上下文富化**:组记忆/工件/分片 manifest（契约已在 1a 定,此处填实现）。

### sizing(数据支撑)
到达中位间隔 7.6min、20% 成批爆发(<2min)→ **池大小按爆发峰定**;示例组 3 成员足够吃 20% 爆发。收益估该组并行度显著提升,fleet 随之抬。

### 首个组池 & 待拍
- **示例组(同 caps)= 首个确定组池**,以此验机制。其余角色是否组池 = **产品决定**待用户/Master(命名洞见:分开命名不再是障碍,但是否把某些角色重新收拢成池由你们定)。
- **PYTHONUTF8 级别(使用方 建议·待用户最终点头)**:建议**用户级 `setx PYTHONUTF8=1` + `chcp 65001` 兜底显示**(一次设永久覆盖所有 ad-hoc 含 agent 内联 python;UTF-8 是现代正确默认、近乎无副作用=最不将就);launcher 级留 ad-hoc 缺口=将就。但**机器级环境改动需用户明确点头**才动——本方案按此设计,落地待用户确认。

---

## Phase 2 · 弹性池工人(elastic pool worker)—— 设计(用户拍板·2026-07-10·待评审后建)

**动机(使用方 实测)**:撞内存墙(31G/90%/swap 换出 15G 颠簸);持久 claude tab ~360MB/个 vs 空闲 daemon python ~33MB(11×);池并行率仅 ~13%、有效并行 1.14 → 池工人 90% 时间空等却按 ~0.4G 常驻 = 为"偶尔并行"付"永久常驻"内存价;每加一持久 tab +0.4G,可用仅剩 3G → 快 OOM/爆 swap。

**核心:池成员从「持久 tab」改「按需弹性 worker」**——空闲近乎 0 内存,来分片才 spin `claude -p` 跑、跑完即释。**改用法/模式,不造新原语**(复用 dispatch_agent 一次性 claude -p / claude_run / 1a 的 O_EXCL 组 claim)。

### 机制设计(MasterAgent lane)
- **弹性派发器(每组一个·极轻)**:watchdog 监组共享 inbox(1a 已有),有分片 + 当前并发 < 上限 → spin `min(pending, headroom上限)` 个一次性 `claude -p` worker;每 worker 用 **O_EXCL 组 claim** 拉【一个】分片 → 处理 → done(结果按 from 路由回 fanouter,1b collect 流式收)→ 退出。空闲时只剩派发器(watchdog 阻塞·~0 token/极小内存),无持久成员 tab。
- **内存感知并发上限(防爆 swap)**:spin 前查可用内存 headroom / 每 worker ~0.4–0.5G RSS 封顶(如可用 8G→上限 ~16 并发)。到顶则分片留组 inbox 等 worker 退让出内存再 spin(pull 背压天然配合)。**上限公式 + 实时内存/并发看板口径由使用方 定/做**;MasterAgent 实现"spin 前 headroom 闸"。
- **与 1a/1b 整合**:`send-group`/`fanout` 撒分片 → 弹性派发器按上限 spin worker pull → `fanout-collect --stream` 边到边收。**状态外置(1a 契约)是前提**:worker 临时、任一 worker 靠分片自带上下文接任一活,跑完即弃无需脑内接续。
- **执行体身份**:弹性 worker 是 ephemeral(不占 role tab);claim/done 复用现有路由(from=fanouter)。持久 tab 只留【协调者/交互/会话粘连】角色。

### 权衡 & 边界(据实)
- `claude -p` 冷启 ~数秒;业务任务 ~2min 级 → 冷启可忽略 = **小延迟换大并行 + 省内存**,划算。**仅秒级以下任务不适用**(那种保持粘连/常驻)。
- 与前台化趋势不冲突:前台 tab 仍宜协调/交互/粘连;**纯可互换池工人走弹性 = 按内存墙 + 可互换性的合理例外**([[onboarding-execution-body-migration]] 的边界补充)。

### 分期 & 待评审
- **2a**:弹性派发器(watchdog 组 inbox → headroom 闸 spin claude -p worker → O_EXCL pull 一分片 → done → 退)。先小规模验"同内存下并行峰值升 + 空闲内存回落"(使用方 验收口径)。
- **2b**:上限动态调参 + WebUI 实时内存/并发看板(使用方 口径)。
- **待使用方/用户**:内存感知上限公式(每 worker RSS 基准 + headroom 保留)、哪些组走弹性 vs 保持持久 tab、冷启延迟可接受阈。

## 边界与关联
- MasterAgent lane:上述全部机制+实现(bus 协调层)。使用方:出数据/并发上限公式/看板口径 + 上线后验并行系数是否提升、内存回落、盲区是否消。
- 关联记忆:[[bus-serial-relay-vs-parallel-pool]] [[peak-steady-resource-philosophy]] [[bus-frequent-light-communication]] [[bus-iteration-1plus1-channel]] [[specialization-route-not-self-handle]]
