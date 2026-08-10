# wait 注册表 —— 设计方案(用户定方向 2026-08-10)

> 起因:用户先报 bug"后台 wait 接了任务移到 processing 但没回报前台会话",MasterAgent 诊断根因=
> yield-not-kill 让位给跨会话孤儿(P0 已止血 commit 9435b90);用户随即提出系统方案:
> **wait 注册表**——把 wait 从"匿名后台进程、靠 psutil 扫+父链猜"升级为"注册在册、有身份、
> 能自检、能通信、能仲裁的受管实体"。本文=设计,分阶段实施(慎动区 core,渐进)。

## 一、为什么要注册表(现状之痛)
现在 wait 是**匿名后台进程**,谁在服务某 role 全靠 `psutil.process_iter` 扫 + 父链上溯推断。脆弱点:
- **父链偶发断裂**(harness 背景 shell 够不到 claude.exe)→ 同会话 wait 被误判跨会话(P0 实测踩过)。
- **孤儿识别滞后**:上次会话没退净的 wait 仍在抢 inbox,claim 走任务却不唤醒任何活会话 → 任务卡死,靠 reap 拖 ttl 兜。
- **无单一真相**:who/selfwake/compute_fleet/yield 各自扫进程各自判,口径分散。
- **无通信**:wait 之间不能协调,只能靠"杀"或"让",无第三态。

注册表 = 给每个 role 的 wait 一个**登记在册的身份 + 生命周期档**,竞争/让位/自检/通信都基于它,不再靠猜。

## 二、注册表要素(用户列的 8 点 → 落地)
数据结构 `<role>/wait.reg.json`(每 role 一个·单例):
```json
{
  "role": "MasterAgent",
  "wait_pid": 37496,
  "host_claude_pid": 21648,              // 启动宿主会话 pid(要素5:检查宿主)
  "host_session_id": "4cbf19d4-...",     // 宿主会话 id(env·同会话判据单一源)
  "started": "2026-08-10T...",
  "timeout_s": 604800,                   // 要素:循环最大时间(block 语义)
  "expected_wake": "2026-08-17T...",     // started+timeout,预期下次空返
  "last_heartbeat": "2026-08-10T...",    // 要素:心跳(整合现 presence 心跳)
  "status": "listening",                 // listening / claimed / yielding / retiring
  "generation": 3                        // 接管代数(每次 takeover +1,便于观测)
}
```
| 用户要素 | 注册表落地 |
|---|---|
| ① 一 agent 一 wait | 注册表单例:抢注册权(O_EXCL)成功才 listening;已有在册则走竞争/让位 |
| ② 循环时间/最大时间 | `timeout_s` + `expected_wake`(block=7天) |
| ③ 心跳信息 | `last_heartbeat`(wait 心跳线程刷,整合 presence) |
| ④ wait 检查自己 | wait 自检线程:我还在册吗(reg.wait_pid==自己)?被接管了就退出 |
| ⑤ wait 检查启动宿主 | 自检:`host_claude_pid` 进程还活且 name=claude?**宿主死→wait 自杀**(根治孤儿) |
| ⑥ wait 竞争 | 抢注册权=O_EXCL 写 reg;冲突时按规则裁决(下节) |
| ⑦ wait 通信 | reg 本身即通信媒介:新 wait 写 `status=retiring`+新 pid,旧 wait 自检读到即优雅退 |
| ⑧ wait 自动让位 | 竞争裁决驱动(注册表判,不靠 psutil 猜) |

## 三、核心机制

### 3.1 竞争裁决(要素 ⑥⑧·取代 psutil 猜)
新 wait 启动 → 读 reg:
- **无 reg / reg 宿主已死**(孤儿)→ 抢注册权(O_EXCL),接管,generation+1。
- **reg 宿主活着且 == 我的宿主**(同会话重复挂)→ **让位**(治 kill-churn),打 already_armed 退。
- **reg 宿主活着但 != 我的宿主**(另一活会话/抢占)→ 写 `status=retiring`+我的 pid 通知旧 wait 优雅退,我接管(一 role 一执行体)。

### 3.2 宿主自检(要素 ⑤·根治孤儿的正道)
wait 心跳线程每 N 秒:①`host_claude_pid` 还活着吗?死了→**自己退出**(不再做孤儿、不再 claim 无人处理的任务);②reg.wait_pid 还是我吗?被接管→退出。
> 这是比 P0"新 wait 去杀孤儿"更干净的方向:**孤儿自己消失**,而非等别人来杀或等 reap 兜。P0 的"判不了保守让位"边缘,由此彻底闭合。

### 3.3 与现有机制的整合/退役
- **presence.json**:已记 role/claude_pid/session_id/last_seen/mode;wait.reg 是其"wait 实体"细化。可合并进 presence 的 sub-object,或独立文件交叉引用(倾向独立:职责清晰、写频不同)。
- **wait_trace.json**(尸检)→ 并入 reg(status + started/exit)。
- **_prior_wait_procs(psutil 扫)/ yield 的 session 判据** → 退役,改读 reg。
- **selfwake-guard / who / compute_fleet** → 基于 reg 判(更准、更快,免全进程扫)。
- **reap**:reg 让"孤儿判定"精确到"claim 者宿主死没死"(现 _owner_live 是 role 级、致孤儿卡 ttl 4000s)。

## 四、分阶段实施(慎动区·金标准守)
- **P0 已交付**(commit 9435b90):yield 精确化(session_id 判据),止血"让位给跨会话孤儿"。过渡态。
- **P1 注册表核心**:建 `<role>/wait.reg.json` + O_EXCL 抢注册权 + 宿主自检(宿主死自杀)。这一步直接根治孤儿(要素 ①⑤⑥)。
- **P2 整合**:竞争/让位/通信全走 reg(要素 ⑦⑧),退役 psutil 猜;selfwake-guard/who/reap 改读 reg。
- **P3 观测**:WebUI 展示 wait 注册表(每 role 的 wait 生命周期/代数/宿主/预期唤醒一目了然)。
每阶段:bus_smoke 全绿 + 实测竞争(N 争 1 恰 1 胜)+ 宿主死自杀实测 + Master数分验收。

## 五、待用户拍板
①P1 是否启动(注册表是较大架构件,值得先评审本设计再动)?②reg 独立文件 vs 并入 presence?③宿主自检心跳间隔(建议复用 _WAIT_HB_INTERVAL=120s)?
> 相关:P0 见 [[wait-rearm-yield-not-kill]];自醒 block 语义 [[wait-sentinel-block-no-idle-churn]];事件自醒不降级 [[masteragent-event-selfwake-no-downgrade]]。
