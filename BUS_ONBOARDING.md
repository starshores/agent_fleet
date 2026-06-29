# Agent Bus 上手包 / 新人 onboarding(能力池版)

> 给新加入 **agent_fleet 项目基座** 的 agent。**培训师**(role=`培训师`)按本文带新人;新人也可自读。
> **最小授权**:每个 role 在 `agent_bus/agents.json` 里有 `caps: [...]`,培训师**只带它被分配的能力模块**(下面 `## cap:<name>` 小节)。
> **基座特性**:只覆盖通用**总线协议层**(总线 / 自醒 / 能力授权 / onboarding),不绑定业务域;**业务角色 / 业务口径 / 业务数据**属可插拔业务负载,由业务侧自管,不写进基座。

---

## 一、培训师流程(收到「带一下 X」任务时做)

**前提**:X 已 `hello --role X` 上线(hello 输出自带自醒说明)。

1. **握手验证 X 的自醒通信**(新人卡点几乎都在这一步):
   ```
   ab send --to X --from 培训师 --title "自醒握手" --task "[握手]收到请 ab send --to 培训师 --from X 回我一条 ACK,确认你的自醒通信通了"
   ```
   挂 `wait --role 培训师` / `replies --role 培训师` 等 ACK。
   - **收到 ACK** = X 自醒通信已建立 → 进第 2 步。
   - **超时无 ACK** = X 没挂自醒 → 让 X 照 `hello` 输出把 `wait --role X --timeout 3600` 当后台任务挂上、每次唤起后重挂,然后重试握手。
   - (注:`self-wake` 不在 X 的 caps 里就跳过握手——纯 daemon 由 worker_daemon 自动自醒,不需手动。)
2. **查 X 的 caps**:读 `agent_bus/agents.json` 里 `X.caps`,**只把对应的 `## cap:<name>` 模块**发给 X,逐项让它复述/确认。多余能力不教(最小授权)。
3. **必传【专一性纪律】(人人有,不论 caps)**:告诉 X——「只做你的专长(你的 caps/desc),**跨领域的活别自接**,用 `ab roster --grep <关键词>` 找对口角色转给它,不确定问 Master/培训师 路由」。原因:一肩挑全科→上下文从源头杂→撑爆窗口→失能退役(**业务侧某 agent**的真实教训:无分工时代被迫全科,积重难返)。**做减法=保专一=防失能。**
4. **回报派发方**:X 自醒是否已通、教了哪几个 cap、专一性纪律是否已传、还有哪些要业务老 agent 接着带。

---

## 二、能力模块(按 caps 取用)

### cap:bus-comms — 总线收发(底座,人人有)
- 身份:`hello --role X` 声明身份(每会话一次);`who`/`selfwake` 看在岗。
- 收派活:靠自醒 wait 领到 / 或 `serve`。
- 发消息 / 派子任务:`send --to Y --from X`;要**阻塞等应答**用 `request`。
- 回填:`done --id <id> --role X --result "..." [--status ok|error]`。
- **notice**:收到 `kind="notice"` 是别人给你的**完成通知**——读它 `notice_of` 指向的 `<X>/done/<id>.json` 取完整结果,然后 `done` 掉该 notice(读完即弃,**勿当任务执行**)。
- **要用户确认/拍板的事**:别只在对话里说(会滚走漏掉)——用 `ab ask-user --from X --title "<问题>" --detail "<上下文/选项>"` 投进 UI 待办池,用户在那儿统一处理。

### cap:self-wake — 事件自醒(交互会话/协调位)
- 挂后台 `wait --role X --timeout 3600`:有人发消息/派活 → 它完成 → 你被唤起处理 → **处理完必须【再挂一个】**。不重挂 = 掉线。
- 这样既能对话又不漏消息,不必 `/loop` 锁死会话。
- (纯 daemon 形态不用学这条:worker_daemon 已自动具备自醒。)
- **执行体有两种形态、可【双向迁移】(必讲,定调新人怎么上岗)**:
  - ① **前台 cli tab**(`hello` + 挂后台 `wait` 自醒,或 `/loop serve`)——人看得见、可观测、可纠正。**这是主流/方向。**
  - ② **daemon**(进 `fleet.json`,常驻 WebUI 每 90s `supervise` 自动拉起 + 自愈)——过渡遗留形态。
  - **迁移**:前台→daemon = 把 role 加进 `fleet.json`(supervise 探到无执行体自动拉成 daemon);daemon→前台 = 先从 `fleet.json` 移除 + 停 daemon,再开前台 tab `hello` + 挂 `wait`。
  - **铁律**:同一 role 的 mailbox(`agent_bus/<role>/`)cwd 无关、文件持久,**迁移不丢 inbox**;但**任一时刻只能有一个执行体**(daemon 或前台,不并存,否则争抢同一 inbox)。**顺序**:daemon→前台必须【先移 fleet+停 daemon,再开前台 tab】。
  - **趋势 = 前台化:新 agent 优先前台 cli tab 挂自醒,别默认起 daemon。** 详见 `AGENTS.md` 执行模型段。

### cap:confidentiality — 安全与隐私最佳实践(全员适用)
- **基座与业务解耦**:基座只承载通用协调机制,不包含业务数据与业务口径;业务内容由业务侧管理。
- **最小权限与数据最小化**:个人数据与组织数据分开处理;只处理任务所需的最少数据,不收集、不记录与任务无关的敏感信息。
- **凭据与披露**:密钥 / 令牌 / 凭据 / 绝对路径不进入版本库、日志、记忆或对话;对外分享前先脱敏,安全问题按 responsible disclosure(负责任披露)流程处理。

### cap:sql-fetch — 取数(要数据的业务 agent)
- 取数走 **sql_agent**(inbox/shared 协议),**绝不直连 psycopg2**。
- 跨库同口径反证等口径见项目记忆。

### cap:external-tool-edge — 操作云文档(仅对外 agent)
- **业务 agent 没有这条**:要外发就把内容交给【对外 agent】,自己不碰 Edge。
- 持有者:碰 Edge/云文档前 `edge-acquire --who X` 抢锁,用完 `edge-release --who X`(防多 agent 抢焦点串台)。

### cap:high-risk — 高风险闸(做写库/外发的)
- 写库/外发类任务带 `requires_approval`:自动监听者(serve/daemon/wait)不会领,需人工 `wait --include-approval` 显式领取。

---

## 三、更多(自查)
- 协议细节:`agent_fleet/AGENTS.md` · 通道用法:`CAPABILITIES.md` · 深知识:项目记忆 `MEMORY.md`
