# 给 Astra 的核查简报：真实 Fleet 与 V3 dev.5 交付验收

> 此文保留 2026-10-13 14:53 的 dev.5 阶段快照。当天 15:37 已发布本机正式版 3.0.0，真实模型验收和当前评估见 [3.0.0 发布增量简报](ASTRA_FLEET_V3_RELEASE_UPDATE_20261013.md)。请以增量简报为最新结论。

日期：2026-10-13（Asia/Shanghai）。本文件只整理本机证据，不代表发布、生产迁移或外发授权。核查范围是当前 `C:\apphome\agent_fleet` 的 Codex/agent_bus 舰队，以及今天交付的 `masteragent-3.0-dev.5.pyz`。

## 结论与验收对象

项目已进入**真实运行和交付验收**阶段。昨天拉起的 Codex fleet 是实际会话、实际 agent_bus 收发和 App Server 投递，不应被称为合成试验。2026-10-13 14:53 的本机快照显示 20 个预期在岗角色全部 armed、0 待确认/掉线/未醒，另有 2 个按既定设置休眠；bus 队列无 inbox、processing、held、failed 积压，WebUI owner 正常，健康检查 `healthy=true`。

同时，必须区分两项验收：

| 对象 | 已有直接证据 | 尚不能据此宣布 |
| --- | --- | --- |
| 现有 Codex + 旧 agent_bus fleet | 20 个精确 session 绑定和完整信使状态；真实消息往返；守卫值守 | 每个角色的业务结果质量或故障恢复均已逐项验收 |
| V3 dev.5 独立基座 | 包完整、与主目录源码一致；有 `serve`、成员租约、Windows 事件接单；交付记录中的合成回归与包验收 | dev.5 已接管现有 20 个会话、真实模型团队闭环或生产状态迁移 |

**Astra 应审查的核心问题**：怎样以现有真实 fleet 为运行基线，对 V3 dev.5 做真实模型、真实收发、停止与恢复的独立部署验收，再决定是否逐步承接职责。不要把“旧 bus 真实在跑”误写成“V3 已上线”，也不要因为 V3 尚未切换而把整个项目退回合成开发阶段。

## 今日实际观察：Fleet

- `ab whoami --json` 将本会话精确绑定为 MasterAgent；身份依据是 `host_session_id`，没有按共享 PID 或 cwd 推断。
- `ab fleet-state --json`：`state=正常`、`guard=值守中`、`armed=20`、`recent=0`、`stale=0`、`off=0`、`parked=2`、`expected=20`。维修模式已于 2026-10-12 关闭。
- `ab health --json`（2026-10-13 14:53:11）：`healthy=true`、`warns=[]`；inbox/processing/held/failed 均为 0；孤儿与卡住任务均为 0；WebUI owner PID 57200、陈旧重复实例 0。
- 今天检查时 SQLAGent、示例成员2曾因 `messenger.state.json` 缺失被新自醒判据标为待确认。两者的精确 Codex session 与持久角色账本一致、宿主存活、processing 为空、turn idle，且没有同角色信使进程。基础设施按同一 session 恢复信使；随后两者分别收到真实 bus 请求并 `done(status=ok)` 回填 ACK，确认 `whoami` 精确匹配和 wait/App Server 就绪。最终快照恢复到 20/20 armed。
- 直接回填证据：`agent_bus/MasterAgent/done/1791874190766-SQLAGent-a6fdcd.json`、`agent_bus/MasterAgent/done/1791874192748-示例成员2-40322a.json`。这证明两条链真实往返；并非 20 个角色逐一完成模型任务的证据。

旧 bus 角色持续在 `agent_bus/` 下运行；其 Codex 信使和 App Server 投递链与 V3 的 `masteragent serve` 是不同运行线。旧 bus 正常不能自动证明 V3 的成员、Attempt 或迁移语义已在真实模型下成立。

## 今日交付：V3 dev.5 包核对

交付物：`C:\Users\<用户>\Documents\Codex\2026-10-08\c-apphome-agent-fleet-docs-prompts\outputs\masteragent-3.0-dev.5.pyz`，大小 66,844 字节，SHA256 `eb87d21c9529a7240ae881989df0aff012588cd6c7dc20d93938e7a40900f705`。本轮独立读取验证：ZIP 无损坏；20 个条目（18 个通用模块、入口、清单）；清单中的 19 个文件摘要全部匹配；包内 `masteragent/` 文件与当前主目录逐字节一致；`--version` 返回 `3.0.0-dev.5`，`--help` 包含 `serve`、`members`、`stop-member` 等入口。交付证据中的 30 个已同步文件与 41 个未改见证文件的当前 SHA256 均匹配。

核心增量：状态 schema 4；前台常驻 `serve` 串行接单；同安装/角色单活动成员、成员与 Attempt 双租约；Windows 原生目录事件唤醒；执行中的持久文本纠偏；显式 `migrate` 从 schema 1/2/3 升级。业务包契约仍是 schema 1 / platform_api 1，角色工作区、权限上限、模型和原生历史由部署配置绑定。

**交付者记录，非本轮重跑**：主目录 V3 回归 362 项中 361 通过、1 项因 Windows 符号链接权限跳过；三组旧兼容线定向检查 40/40；包验收 14/14，含同构建空载→A→B→A、两个角色、6 个实际 MemberWorker、Windows 目录通知和唯一领取。交付包两次构建字节一致。本轮只做包摘要、清单、源码字节、`--help`/`--version` 的只读核对，没有重跑测试套件。

交付记录明确标明：包验收的 `real_model_calls=0`、`network_calls=0`；原生版本探测、supervisor、模型侧及接收回执使用替身。没有真实 Codex App Server 模型执行、native resume 或真实迁移。2026-10-13 14:48 的本机进程检查未发现 `masteragent serve` / dev.5 pyz 常驻进程。源码同步和包完成不等于现有 20 个 bus 会话已加载 V3。

## Astra 需要给出的判断

1. **真实验收最小闭环**：在与旧 bus 隔离的状态根和明确部署 JSON 上，选择哪些代表性角色与纯文本任务，用真实 Codex 模型完成多角色接单、结果回填、执行中补充消息及持久通知的可重复验收？每项以什么原始证据判定通过？
2. **并存与切换边界**：如何让 V3 成员试运行而不与旧 bus 对同一业务任务双重领取？哪些状态只观察，哪些可以灰度承接？切换失败如何保留未知执行、通知和原生历史？
3. **身份及恢复**：成员的 Actor/安装/角色/代次绑定、真实 App Server thread/turn、stop-member 后进程退出与 `needs_reconcile`，在真实模型和故障下如何证明？`online`/`owned_attempt_active` 不得当作模型连通或 OS 进程退出证据。
4. **放行门槛**：哪些是 dev.5 本批可以验收的纯文本能力，哪些须等 native resume、人工审批桥接、业务文件工具和系统服务托管后才能承接生产职责？schema 4 迁移必须显式备份、核对在飞任务；dev.4 不能打开升级后的状态。
5. **现有 fleet 运维缺口**：今天两个角色丢失信使状态文件，守卫因缺少证据不会自动接管。这两条已恢复并真实 ACK；请评估如何在不按共享 PID 猜身份、不重放在飞消息的前提下，使此类状态缺失可观测且可恢复。

## 原始资料入口

- V3 阶段事实与边界：`docs/MASTERAGENT_3_PROGRESS.md`；成员与停止语义：`docs/MASTERAGENT_3_MEMBERS.md`；运行时与部署：`docs/MASTERAGENT_3_APP_SERVER.md`。
- 本地交付说明：`C:\Users\<用户>\Documents\Codex\2026-10-08\c-apphome-agent-fleet-docs-prompts\outputs\MA_V3_DEV5_HANDOFF_20261013.txt`。
- 本地机器可读证据：同目录 `MASTERAGENT_3_DEV5_EVIDENCE.json`、`MASTERAGENT_3_DEV5_BUNDLE_CHECK.json`、`MASTERAGENT_3_DEV5_MAIN_TESTS.json`、`MASTERAGENT_3_DEV5_COMPAT_MAIN_TESTS.json`。
- 当前 fleet 实时复核命令：`python -B agent_bus/agentbus.py fleet-state --json`、`health --json`、`selfwake`。这些是随时间变化的现场数据，不能用本文快照代替重新核对。

本简报未读取无关 agent 的私有会话正文、未调用真实模型、未启动 V3 服务、未迁移状态或外发数据。
