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

### 实现分层
- **1a 组注册 + 共享组 inbox + 成员双听(地基·最大收益)**
  - `groups.json`:`{"<组名>":{"members":[...],"require_same_caps":true}}`;`ab group-check` 上线前核成员 caps 一致(判据:只有真·可互换复制体才组池)。
  - 成员 `wait` 同时监听【自己 inbox + 所属组 inbox】(watchdog 加一个监视目录即可,复用现有 `_wait_core`)。
  - `ab send --to-group <g>` → 丢进组 inbox(不指定成员);原子 claim 保证只一个成员领到。
  - **仅此一层即治负载不均 + 让新加入成员真正分担**(空闲即 pull)。
- **1b fan-out / scatter + collect(便利原语)**
  - `ab fanout --group <g> --shards <json|->`:N 分片打 `fanout_id` 撒进组 inbox;`ab fanout-collect --id <id> [--wait]`:阻塞收齐 N 个 done(复用 done/ + manifest)。
  - `ab scatter`:批量异步派发的糖(=fanout 不收尾)。
- **1c 共享组上下文**:`BUS/<group>/shared/` 组记忆 + 分片 manifest,任一成员可接任一分片。

### sizing(依据)
按实测到达分布定池大小:以批量爆发峰为容量依据(示例组 3 成员可覆盖爆发)。收益估该组并行度显著提升,fleet 随之抬。

### 待用户/Master 拍板(建 Phase 1 前)
1. **哪些角色组池**:同类复制体(caps 一致、明确可池=首选)。其它组?(职责/专长不同=**不可池**,已排除)。
2. **范围/优先级**:先只做 **1a**(共享组 inbox·最大收益·改动最小)验收,再 1b/1c?还是一次到位?
3. **组任务的语义**:哪些活适合丢组 inbox(可互换分片)vs 仍须点名某成员(带状态/续接)。

---

## 边界与关联
- MasterAgent lane:上述全部机制+实现(bus 协调层)。使用方:上线后验并行系数是否提升、盲区是否消。
- 关联记忆:[[bus-serial-relay-vs-parallel-pool]] [[peak-steady-resource-philosophy]] [[bus-frequent-light-communication]] [[bus-iteration-1plus1-channel]] [[specialization-route-not-self-handle]]
