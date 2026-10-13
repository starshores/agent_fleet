# MasterAgent 3.0：常驻调度成员

dev.5 引入 V3 自有的前台 `serve` 入口；本机正式版 `3.0.0` 已以真实模型验收该入口的纯文本范围，详情见 [发行说明](MASTERAGENT_3_RELEASE.md)。业务包契约仍为 schema 1 / platform_api 1；平台状态为 schema 4。逐角色目录、权限上限、模型和原生历史仍属于部署，不进入可移植业务包。

成员进程持续运行，每个 Task 仍创建独立 Attempt 和独立原生进程/thread。它没有连接桌面私人会话，不自动注册旧 bus，不恢复原生历史，也不安装 Windows 服务或自启动项。多角色分别启动自己的成员进程，共享同一明确选择的 V3 状态根。

## 启动与观察

先按已有 V3 流程安装、激活业务，显式 register 获取角色 Actor，选择该安装/角色/代次的运行时 JSON。Actor 文件和运行时 JSON 由部署方保管；`serve` 不代替 register，不自动换角色、换模型或选历史目录。

```powershell
python -B -m masteragent --state-root C:/YOUR-STATE --runtime-config C:/YOUR-DEPLOYMENT/runtime.json serve --actor-file C:/YOUR-DEPLOYMENT/actor.json --tool respond
python -B -m masteragent --state-root C:/YOUR-STATE members a
python -B -m masteragent --state-root C:/YOUR-STATE stop-member a MEMBER-ID --reason 'Stop this deployment member'
```

以上命令仅是部署示例，本批没有用真实配置执行。`serve` 可选择 `--max-tasks N` 或 `--max-seconds N` 限制本次服务；没有限制时持续等待。启动先检查角色、工作区、工具与部署匹配、专用历史目录、原生版本，以及本机事件监听是否可建立，失败不会留下一个就绪成员。版本/帮助检查不是认证或模型连接检查。

成员用部署时选定的 `--tool` 处理其角色当前代次的 queued 任务；暂停审批、等待子任务、待核对或旧代次任务不会被领取。部署需选择符合该角色任务用途的能力，当前没有自动工具选择。一次执行异常时成员停止继续接单，剩余队列保留，避免连续消耗失败任务。

`members` 是纯只读查询，不续租、不回收任务、不启动进程：

| 字段/状态 | 实际含义 |
|---|---|
| ready | 已完成启动检查，尚未等待或领取下一条 |
| waiting | 本机目录监听已建立，正在进入或处于队列等待循环 |
| busy | 成员持有一条当前执行；进度由 Attempt 事件查看 |
| stopping | 停止请求已持久化并撤销发布权限，等待拥有者确认退出 |
| stopped | 拥有者报告执行进程已终止，且没有有效在飞 Attempt |
| lost | 租约/身份失效或无法确认执行结束，需要核对 |
| online | 成员租约及角色绑定当前有效，不证明模型连接可用 |
| accepting_tasks | 当前有权从队列接收任务 |
| reception_waiting | 当前报告处于事件等待阶段 |
| owned_attempt_active | 有效执行归属存在；不用于判断 OS 进程是否还活着 |
| model_connectivity | `not_verified`，不能由登记、心跳或版本探测推断 |

收到停止请求会先在事务中将当前 Attempt 置为过期、Task 置为 needs_reconcile、未确认输入置为 held，再由拥有者关闭自己的进程树。停止中没有有效执行归属，仍不等于子进程已退出。状态不按历史 PID 猜测或杀进程。

## 身份与租约

每次 `serve` 获得新的 member_id 和成员令牌；数据库只存令牌摘要。成员绑定安装、角色、激活代次、Actor、部署摘要及工具。摘要用于关联选定配置，不是对本机文件的远程认证。

同一安装/角色同时只允许一个有效成员。选择最早 queued 任务、创建 Attempt、保存 Attempt→Member 归属和标记 busy，在一个 SQLite 写事务中完成。普通 `claim`、`run` 和 `run_as` 不能绕过现有成员的排他。每个成员仍须通过 Attempt 租约才能执行和回填。

成员失效后，Attempt 续租、运行事件、纠偏投递回执及结果发布都受成员租约约束。旧 Actor、旧 member token、旧代次或迟到结果不能取得新执行权。已过期成员可在新成员启动事务中被隔离，旧任务保留待核对；不会自动退回队列。

```powershell
python -B -m masteragent --state-root C:/YOUR-STATE expire-members a
```

`expire-members` 是显式的持久隔离操作。只读查询已能显示租约失效，但不会替操作者改变任务状态。确认可安全重复之后，才使用已有 `reconcile` 命令。激活代次改变后仍需 `resume-task` 和重新审批，不能把成员重启当作任务或原生会话恢复。

## 持久收发与工作中纠偏

任务与状态仍保存在 SQLite。Windows 使用原生目录变化通知唤醒成员：先挂监听、再检查队列、再等待，堵住“检查为空后才到任务”的间隙。通知只表示值得重查，不是任务完成、领取或模型接收回执。空闲超时只用于续租与显式服务时限，没有固定间隔扫描业务目录；非 Windows 的常驻等待当前明确报不支持，单次执行器不受影响。

执行期间不持有长数据库事务。其他调用可继续提交任务、回答 Decision、提交 `steer-attempt` 或请求停止。App Server 文本执行通过 dev.4 的精确回执队列接收纠偏；`codex-exec` 仍不支持运行中纠偏，提交会明确拒绝。

审批完成和子任务回填会更新父任务的持久状态并唤醒队列。恢复执行时，仅把该角色显式父请求已经收到的子结果放入本次提示词，不读取其他角色或业务历史。可以通过现有 `request-child`、`child-replies` 与 `answer-decision` 组织合成协作；本批模型仍是纯文本，不能自行调用这些平台命令或原生工具。

outbox 保留结果及生命周期事件，不自动把 pending 改成 delivered，也不把通知确认当作模型已读。原生纠偏的 accepted 只证明传输接收，不代表语义采纳或权限批准。

## schema 4 升级与回退

schema 1/2/3 仅经显式 `migrate` 备份升级。schema 2/3 存在 running Task/Attempt 时拒绝迁移。schema 3 的逐角色部署、输入回执、结果、Decision 和 outbox 保留；schema 1/2 按既有规则补全逐角色部署。新成员表初始为空，旧 Actor 令牌撤销，需显式重新 register。

新表为 `member_sessions` 和不可变 `attempt_members`，唯一索引约束角色活动成员。升级不扫描业务目录，不导入私人会话或旧 bus。升级后 dev.4 不能打开 schema 4；已经产生新工作时不能直接用旧备份覆盖，否则会丢失结果和去重证据。

回退先停止接纳、等待拥有者确认进程退出，并核对不明执行，保留新状态、备份及原生历史。旧兼容运行线保持独立，修改 V3 源码不会自动切换已有团队。

## 本阶段验证边界

验证使用临时 SQLite、合成业务和替身 supervisor/probe；实际执行了本机事件通知、跨进程任务提交及工作进程强制结束后的隔离测试。开发包另做同构建空载→A→B→A、两角色、任务唯一领取、持久输入和旧身份拒绝验收。

没有启动真实 Codex App Server、调用真实模型、迁移生产状态、注册旧角色或重启既有服务。本批提供持续调度入口，尚未完成真实模型下的团队在线验收、native resume、运行中人工审批桥接、业务文件工具、系统服务托管或生产切换。
