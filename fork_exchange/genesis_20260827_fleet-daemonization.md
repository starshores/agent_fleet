# genesis 分岔改动:舰队 daemon 化(fleet daemonization)+ 实证验证 harness

> fork:genesis · 日期:2026-08-27 · 覆盖:把空闲执行类角色从【前台交互 CLI】批量转【daemon 执行体】(轨A kill-convert + 轨B 复制体)+ supervise GBK 修复 + daemon 记忆继承机制 + 「实证优于自证」验证 harness(设计)。
> 前置依赖:本单元建立在 `genesis_20260827_per-role-effort.md` 之上(那篇的改动1 effort 注入 + 改动2 daemon 路径复活 + 改动3 daemon 接续 是本次批量 daemon 化的机制底座)。
> 主题:**一个 bus 想把"常驻前台 CLI 舰队"降本增效为"daemon 舰队"时,会撞上的可行性边界、安全闸、记忆继承断点,及"不失能"如何用实证(而非自评)坐实**——对另一个 bus 吸收价值最高的是【何时该 daemon 化 + 三个断点的补法】。

## ★核心洞见(最高吸收价值·去语境化)

### 洞见1:前台 CLI ↔ daemon 是【可逆策略选择】,不是单向架构
genesis 曾于 2026-07-01 用户令「全部 daemon 移除、全前台化」;2026-08-27 又批量转回 daemon。**教训:执行体形态(前台 CLI vs daemon)应按【当下需求】随需切换,不是一次定死的架构**。判据=**哪面能满足当前主要需求就用哪面**:
- 需 per-role effort 自动注入 / 空闲省内存(daemon=python 15-30MB vs CLI=claude 341-469MB)/ 无人值守 → **daemon**。
- 需跨轮交互状态 / 长线协调 / 可观测 attach → **前台 CLI**。

### 洞见2:daemon 化的准入闸 = 【失能自评】,不是一刀切
不是所有角色都能 daemon 化。判据=**该角色是否依赖"跨任务的长交互记忆/上下文"**:
- **离散执行类**(每任务独立·无跨任务交互态·如数据诊断/批处理/例行任务)→ 天然 daemon-fit,直接转。
- **强依赖长交互记忆** → 转 daemon 会失能(daemon 每任务冷起丢上下文)→ 要么保留前台,要么走【复制体 + 记忆继承 + 接续机制】。
- **准入靠自评 + 实证复核**(见改动4 harness):角色自评"不失能",再用"重做过往已知任务比对"坐实。

### 洞见3:daemon 有三个"断点"必须显式补,否则静默丢
前台 CLI 靠 hello/交互天然拥有的东西,daemon 形态会静默失去,必须逐个补:
| 断点 | 前台 CLI 怎么有 | daemon 怎么丢 | 补法 |
|---|---|---|---|
| **角色记忆** | hello 认角色→导航角色记忆索引 | claude -p 不走 hello,不导航角色索引 | **desc 内嵌"上岗必读记忆"指针块**(列记忆名+绝对路径,daemon 开工按名 Read);worker_daemon 每任务注入 desc=每任务都看到 |
| **高风险审批** | 人在 tab 前把关部署/写库 | 无人值守+`--dangerously-skip-permissions` 无提示 | **requires_approval 闸**:高风险任务 daemon 拒领(_wait_core 不领)+ 二次拒(held/ + 回发起方要人工批准);tab/daemon 一致 |
| **交互兜底**(如页面偶发要人手刷新) | 人当场处理 | 无人 | **走 ask-user 待办池**(daemon 投非阻塞待办,人异步处理) |

## 改动 1:轨A kill-convert —— 空闲执行类角色批量转 daemon ★模式可吸收
- **改了什么/为什么**:把空闲执行类角色从前台 CLI 转 daemon(省内存 + effort 可注入 + 无人值守)。
- **可吸收性**:◐ 模式可吸收(kill-convert 编排 + 先杀后拉防双 holder);✅ 值得借鉴;具体角色=实例面不吸收。
- **吸收单元(模式)**:
  - **ordering 铁律=先杀后拉·防双 holder**:①原体整理落档 → ②kill 交互 pid(三方核实 who+presence+tasklist 一致再杀·防误杀)→ ③加 fleet.json 条目 → ④supervise 拉起 → ⑤实证验在岗。**顺序不能反**:交互体和 daemon 同 role 并存=两个 holder 抢同一 inbox 竞态。
  - **拉起前自核干净**(不只信 kill 方转述·数据真实性):目标 pid `NoSuchProcess` + presence.claude_pid=null + 全扫无残留 claude/wait/daemon 持有者。
- **验收**:每角色 selfwake ✓在岗 daemon 自醒 + worker_daemon 真进程(psutil 实测·非 supervise 自报)。
- **需适配 / 实例面**:具体角色清单 = 本舰队实例,不吸收(会话考古 cwd=agent_fleet·非业务组)。

## 改动 2:轨B 复制体 —— 原体保留 + 建 daemon 复制体 ★模式可吸收
- **改了什么/为什么**:对"不确定能否 daemon 化 / 需保留交互原体"的角色,不 kill 原体,而是**另建一个 daemon 复制体**(仿已有 daemon 复制体模式)并行。原体交互态留、复制体 daemon 态跑,互不干扰。
- **可吸收性**:✅ 模式可吸收(replica 而非 convert·零停机·可回退=删复制体即回原状)。
- **吸收单元(模式)**:
  - **登记归 onboarding 角色**(caps 授权 + 角色注册),**拉起归 fleet 角色**——两 lane 协作,先注册后拉起(未注册就加 fleet=90s supervisor 拉起"裸 daemon"无 desc/caps)。
  - 复制体 caps/desc 仿原体;**记忆继承走洞见3 的 desc 指针块**。
- **验收**:两复制体 daemon ✓在岗;desc 定稿后 worker_daemon **每任务 `_load_agents()` 重读**=自动吃新 desc(无需重拉·实证:desc 后改仍即时生效)。
- **需适配 / 实例面**:复制体角色名 + 各自 caps/继承记忆清单 = 实例面。

## 改动 3:修 supervise GBK 解码 bug ★平台面(Windows bus 通用)
- **改了什么/为什么**:supervise 批量拉起时读 `start_worker.ps1`(PowerShell)输出,裸 `subprocess.run(text=True)` 在 `PYTHONUTF8=1` 下按 utf-8 解码 PowerShell 的 GBK 字节(0xcc…)→ `UnicodeDecodeError` 崩读取线程。daemon 因 detached 先起不受影响,但 supervise 的 `[已拉起]` 自报被吞、且异常噪音。
- **可吸收性**:✅ 平台面(任何在 Windows 上用 subprocess 读 PowerShell/GBK 程序输出的 bus 通用)。
- **吸收单元**:`agent_bus/bus/health.py` cmd_supervise 的 subprocess.run 加 `encoding="utf-8", errors="replace"`。"STARTED" 是 ASCII·replace 不损关键信号。
- **验收**:修后会话考古拉起 supervise 输出干净(`[已拉起]` + `[活着跳过]` 全正常自报·无异常)。
- **通用教训**:又一例「Windows 显式 UTF-8·别信默认」(数据 I/O 一律显式 encoding+errors);且**别信工具自报、用 psutil/selfwake 实测**(本次正因 supervise 自报被吞,全靠实测才确认全部真拉起)。

## 改动 4:「实证优于自证」daemon 化验证 harness ★平台面(设计·待建)
- **改了什么/为什么**(用户令 2026-08-27「实证优于自证」):daemon 复制体/已退出会话的"不失能"**不能只凭自评**,要用【实践对比】坐实——给角色一个已知结果的过往任务→ daemon 形态重做→ 与原处理比对→ 差异报告。一致=真继承(实证不失能)·差异=失能点现形。
- **可吸收性**:✅ 平台面(任何要验证"daemon/复制体是否真继承能力"的 bus 通用)。**当前=设计稿·待 Master数分 对口径后建**。
- **吸收单元(设计)**:
  - **三种基线取法**:B1·done 记录(省)/ B2·jsonl 历史提取 / **B3·--resume 重放**(已退出会话·复用 per-role-effort 篇改动3 的 `--resume-session` 层C)。
  - **实测约束**(坑):done 记录 + metrics.jsonl **均无 session_id** → B3 的 session_id 只能从 `projects/<cwd库>/<session_id>.jsonl` 文件名反查。
  - **候选重做**:子进程 claude -p fresh 注入角色 desc 重做同一 task;写文件任务走 worktree 隔离。
  - **机制/口径分离**:机制层只产 diff(text_diff + meta_diff)+ verdict 留空;判据(什么算一致/差异归因)=口径方(Master数分)定,不硬编进机制。
- **验收(待建)**:bus_smoke + 对复制体角色先跑一轮 validate-then-trust。

## 全量变更清单(含 minor/cosmetic·锚到 commit·零遗漏)
> 约定(2026-08-27 用户令):对比基版每一条改动都进 fork。重大机制上面四层详述,此表兜住全部。

| commit | 文件 | 一句话 | 可吸收性 |
|---|---|---|---|
| `73ad214` | agent_bus/fleet.json | 轨A pilot:单个执行角色转 daemon(先杀后拉验证跑通) | ◐ 模式(角色实例面) |
| `024c0d2` | agent_bus/fleet.json + bus/health.py | 轨A 批量执行角色+会话考古 转 daemon **+ 修 supervise GBK 解码 bug**(本文改动3) | health修=✅平台面·转角色=◐实例面 |
| `174a90b` | agent_bus/fleet.json | 轨A 收尾:剩余执行角色转 daemon(执行组全 daemon 化) | ◐ 实例面 |
| `a67e621` | agent_bus/fleet.json | 轨B 复制体拉起(原体保留·本文改动2) | ◐ 模式(复制体实例面) |
| `f283ba4` | agents.json(培训师) | 登记 daemon 复制体角色+caps 授权(轨B phase1·onboarding lane) | ◐ 模式(caps/记忆实例面) |
| `99a67f2` | agents.json(培训师) | 复制体 desc 记忆继承口径显式化(desc 嵌必读记忆指针块·本文洞见3) | ✅ 平台面(desc注入继承法通用) |

> 分工记录(可吸收·多 lane 协作):**daemon 化=三方协作**——Master数分(定候选+失能自评+kill 交互 pid+验证口径)· 培训师(register 角色+caps 授权+记忆继承方案·onboarding lane)· MasterAgent(fleet.json+supervise 拉起+机制+commit)。**caps 登记归培训师**(caps-registration-owned-by-trainer)、MasterAgent 不自揽=route 给培训师。
> harness 设计稿(改动4)本地留档,commit 待 Master数分 对口径后随实现落。

---
**整体验收**:fleet.json daemon 全 selfwake ✓在岗(psutil+selfwake 实测)·原体保留正确·supervise 修后自报干净。
**平台三问**:①归属=daemon 化机制/断点补法归平台,具体角色/caps/继承记忆归业务口径;②清除=移 fleet 条目+清 agents.json 复制体即空载;③换域=换角色名即用(cwd/caps/记忆清单实例面填)。
**不可吸收(实例面·跳过)**:全部具体角色、复制体名、各角色 cwd/caps/继承记忆清单(本舰队+业务库)。
**待续**:剩余角色按候选逐步转 · harness 实现(待口径) · 复制体记忆继承清单补齐。
