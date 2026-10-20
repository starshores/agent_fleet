# MasterAgent 3.1 阶段 B 接入范围与入口核对表

更新：2026-10-20。范围依据：[持久路由与回执契约](MASTERAGENT_3_1_ROUTING_RECEIPT_CONTRACT.md)、[执行工单 B-00](MASTERAGENT_EXECUTION_BACKLOG.md)。本表来自源码静态审阅，不是生产运行盘点；没有读取旧 fleet 配置、邮箱、私有会话或认证，没有启动、登记或停止旧服务。实现与测试完成状态以 [3.1 进度](MASTERAGENT_3_1_PROGRESS.md) 为准。

本批只为**全新、独立、受控的来源**实现平台路由与本机目的端持久接收能力。旧 bus 的来源、消费者、回填和恢复器全部继续留旧线，尚未接管。新库中的 `legacy` 归属记录只用于明确裁决该受控来源归旧线，不证明任何旧 bus 消费者已经执行核心裁决；它不能成为把旧 inbox 接入 V3 的许可。

**B-00 结论：静态入口核对与首批实施范围已完成；生产入口责任签收未完成。** 选定下述 `ma-source-v1` 全新本机来源，允许推进 B-01～B-08 的受控来源实现和合成验收。B-04/B-05 中涉及旧生产消费者及恢复器的部分仍待实施；受控来源通过不能签收这些旧线行，也不能据此宣布旧 fleet 接管或 dev.4 的真实试点完成。

核对基线为 `3.1.0-dev.3`、schema 5、公共 API `3.1-dev1`。2026-10-20 实际读取项目内包字节并计算 SHA256，结果与接手入口一致：

| 本机归档包 | SHA256 |
| --- | --- |
| `releases/3.0.0/masteragent-3.0.0.pyz` | `69402eaad81035e121fed0d2af07b31f660ea49abc6f80b0516811831967addf` |
| `releases/3.1.0-dev.3/masteragent-3.1.0-dev.3.pyz` | `538172c6352df4ef79744e2e3582584f1fc223a564b0b65936cc491f5ebd078a` |

本次仅修改本表。以下源码行号是该次静态核对的位置，后续编辑发生位移时以函数名为准。没有在 B-00 中运行平台回归或真实模型；第 6 节所列既有测试是可复跑的源码证据，不是本次新增运行结果。

## 0. 第一实施来源与责任边界

第一来源采用拟新增的 `masteragent/source_store.py: SourceStore`，协议 `ma-source-v1`；拟新增 `masteragent/transport.py` 分别搬运来源和发送结果。命名与接口已由当前实施会话分派给开发子任务，不代表代码已经交付。

| 项目 | 冻结的首批规格 |
| --- | --- |
| 存储与发现 | 只打开调用者明确指定的新来源绝对 root；独立、可校验的存储结构和持久实例身份。禁止扫描目录猜来源，禁止读取旧 inbox、旧配置或当前会话历史。source、state、workspace、目的端及 native home 分开，不以项目内归档库作为运行根。 |
| 稳定身份 | 来源 namespace 由可信初始化参数显式设置并持久保存，实例身份初始化后不可更换；运行重启和搬运重试不能生成新 namespace。合成示例可用 `synthetic.transport.v1`，它不是生产 namespace，也不能由正文指定。 |
| 消息种类与 ID | 仅显式 `kind="task"`；带类型的整数或字符串 ID，`1` 与 `"1"` 不相同，布尔值拒绝。正文不产生目标、审批、来源身份或额外任务。未知 kind、旧 `notice`/`fyi`/`request`/TODO 均不能自动翻译成 Task。 |
| 不可变信封 | 来源协议版本、namespace、instance、typed source ID、kind、固定目标、完整 UTF-8 正文与字节数/SHA256。正文保留换行、首尾空白、组合字符和尾段；上限沿用核心的 64,000 UTF-8 字节，不截断。目标来自绑定的路由快照，重放不能改目标。 |
| 唯一公开写入面 | `SourceStore.publish/get/pending/confirm/complete`；publish 只保存显式源消息，confirm 保存源端对原 admission 的确认，complete 结束该对象的搬运恢复责任。禁止另一个“复制邮箱文件”入口。API 最终参数与结构须由 B-01 实现冻结。 |
| 三阶段确认 | 核心 `admit_source` 提交 → 源端 `confirm` → 核心 `confirm_source` → 源端 `complete`。源确认和平台确认之间崩溃时，源对象仍须可查回和补齐，不能因 source 已 confirmed 就从恢复集合消失；重用原 ID/admission，不重新 submit。 |
| 接纳前目的端验证 | `platform.bind_route_endpoints(route_id, expected_epoch, source, receiver)` 拟在无 admission 时永久固定 SourceStore 与 ReceiptStore 的 root/namespace/id/instance/协议/限额。绑定路由的 `admit_source(..., source=source, receiver=receiver)` 强制复核，缺参数或替换实例不得退回旧的无预检路径。 |
| schema 边界 | 当前已验基线为 schema 5；持久 `route_endpoints` 是 dev.4 拟实施的 schema 6 变更，须与显式迁移、精确结构校验和故障测试一并验收。只读打开、服务启动或此文档不能自动升级现有库。 |
| 执行与结果 | 搬运者持 source lease 仅获得搬运权；执行由平台 `_claim` 或核心 `claim_legacy` 单独裁决。首批常驻执行使用 V3；`legacy` 只用受控替身验证另一 lane 的排他，绝不调用旧 bus。结果发送泵不运行模型，目的端仍为 `ReceiptStore` 的 `ma-receipt-v1` / `text.v1`。 |
| 开发责任 | 当前实施会话负责 B-01～B-08 集成；被分派的 SourceStore、端点绑定、transport 子任务分别提供代码和合成证据。B-00 静态审阅由 `/root/dev4_entry_audit` 完成；这些是当前开发任务标识，不是 agent-bus 角色。 |
| 运行责任 | 真实来源写入方、消费方、恢复方及运行负责人均尚未指定；B-09 前必须明确。旧线每一项的 namespace/负责人仍未映射，不从源码角色字面值推断身份、在岗状态或授权。 |

SourceStore 的存储结构只能承载来源消息和确认事实，不能另行裁决执行归属；核心仍是 admission、lane、Task/Attempt 和结果投递的权威。端点的预检是准入证据，不是目的端已经收到结果的证据。

## 1. 平台 V3 入口

| 入口 | 源码中的共同路径 | 本批边界与守卫要求 |
| --- | --- | --- |
| 新受控来源接纳 | `RoutingMixin` 的接纳事务 | 来源身份、不可变信封、epoch、owner lane 和唯一 Task 在同一核心事务裁决。只接受已配置、已验证的来源与消息类型，不扫描旧 inbox。 |
| `submit`、工作台 `submit-task` | `service.py: Platform.submit`；`workbench_commands.py: WorkbenchCommands.execute` | 继续作为直接平台任务，没有外部来源身份；不能用“先 submit 再补映射”接入旧消息。网页不会因为保存成功自动创建来源裁决或目的端投递。 |
| `run` | `service.py: Platform.run → _execute → _claim` | 本机管理执行仍受 `_claim` 中同一来源归属与新 claim 开关约束；完成进入 `_finish`。 |
| `run_as` | `service.py: Platform.run_as → _execute → _claim` | Actor、业务代次、角色、工具及路由归属同时有效才能执行。 |
| `claim`、CLI `claim` | `service.py: Platform.claim → _claim`；`cli.py` | 外部执行前持久领取；路由冻结检查与 Attempt 插入在同一事务，不允许执行器绕过。 |
| `execute-task` 与原生运行器 | `runtime.py: CodexRunner` / App Server runner → `claim` | 继续使用明确部署和原生执行约束；本批不运行真实模型。路由权限不增加工具权限。 |
| `serve`、`MemberWorker` | `worker.py: MemberWorker.run → claim_member_task → _claim` | 成员选择器需跳过被禁止新 claim 的路由任务，避免队头阻塞；最终仍在 `_claim` 内复核。成员租约与 Attempt、投递租约分别管理。 |
| `request_child` | `coordination.py: CoordinationMixin.request_child` | 平台控制方创建的子任务继续使用既有父子契约；不将正文中的派活文字解析为任务，不自动继承或伪造外部 source identity。 |
| `complete`、同步本机执行完成 | `service.py: complete → _finish` / `_execute → _finish` | `_finish` 是结果、最终通知和新投递意图的共同事务点。投递失败不撤销已经完成的结果，更不能重新执行模型。 |
| `reconcile_task`、`resume_task` | `service.py` | 沿用原任务和来源裁决；明确重试生成新 Attempt，续接保留原 admission。新 epoch 不改变已接纳对象的归属或固定目的端。 |
| `outbox`、`ack_outbox` | `coordination.py` | 旧平台事件通知机制保持独立。`delivered` 不是目的端持久回执，读取页面不回执，也不推断用户已读。 |
| 新结果投递 / 核对 API | 核心投递账本与本机目的端接收器 | 领取、`sending` 和回执保存各有事务；目的端调用在核心事务之外。同 ID 同字节重试，未知效果保持 `held`。 |

任何未来新增执行入口都必须经过上述共同领取与完成路径，或提供等价的事务内守卫及独立测试。只在 CLI 或网页层禁止执行不足以保持排他。

## 2. 旧线发送与写入入口：全部明确留旧线

下表列出源代码已存在的入口，不表示已检查实际部署是否启用。源文件中的角色字面值是历史实现，不作为本会话身份或新基座业务规则。

| 入口类别 | 已核对源码与操作 | 本批处理 |
| --- | --- | --- |
| 普通 `send` / `request` | `agent_bus/bus/core.py: cmd_send, cmd_request, _deliver`，直接写角色 inbox | 留旧线；task/request/fyi/urgent 不自动接纳为 V3 Task。 |
| 完成回填、通知、批量 notice 确认 | `core.py: cmd_done, cmd_ack_notices` 写 sender done、archive；`notify.py: _emit_completion_notice` 写 sender inbox | 留旧线；done 与 notice 均不是新协议回执，也不自动转成新执行任务。 |
| 工作组 pull、push、分片 | `pool.py: cmd_send_group, _route_send, _route_assign, cmd_fanout` 写组 inbox、成员 inbox 或 route_queue | 留旧线；组名、分片与成员目标必须在未来有独立稳定映射，不能仅改普通 send。 |
| 工作组自动恢复与待分配队列 | `pool.py: _route_reap_group, _route_drain_group, route_on_member_done` 重排与通知 | 留旧线；现有文件锁和回收不能代替核心来源裁决。 |
| 用户待办与答复 | `tasks.py: cmd_ask_user, cmd_answer`；`ui/api.py: todo_answer, todo_dispatch`，答复写 inbox、派工复用 send | 留旧线；旧 TODO、平台 Decision、Task 和原生批准不是同一对象，不推断互相批准。 |
| WebUI 手工派活、手工完成、偏好补写 | `ui/api.py: send_task, done_task`；`ui/routes.py` 的派活/待办端点 | 留旧线；现有网页仍可产生、完成旧线任务，不能声称 V3 已成唯一入口。 |
| 一次性直接执行与组池分片 worker | `agent_bus/bus/exec.py: cmd_dispatch`；`dispatch_agent.py`；`pool_dispatcher.py: _take_shard, _spawn_worker` | 留旧线；这些路径可以直接拉起旧执行并写 done，不经过 V3 `_claim`。 |
| 机制提醒、培训与探针 | `identity.py` 的 hello 培训投递、自醒守卫投递；`probe.py: _dispatch_probe` | 留旧线；探针复用 send，另有直接 inbox 写入，均不在本批受控来源内。 |
| 孤儿/健康恢复 | `health.py: cmd_reap, _health_sweep_core`；`selfwake.py: _requeue_orphans` | 留旧线；恢复器可能把 processing/未知目录内容写回 inbox，未来接入前必须按原来源裁决，不能变更 ID 来重跑。 |
| 手工复制或其他直接文件写入 | 旧 mailbox 文件协议的外部写者不可能仅凭静态仓库枚举穷尽 | 不纳入 V3；任何未登记来源默认拒绝接入，不能靠约定“没有其他写者”放行。 |

## 3. 旧线领取、执行与完成：全部明确留旧线

| 消费路径 | 已核对源码 | 接入前缺少的条件 |
| --- | --- | --- |
| 手工/常驻 wait、serve | `core.py: _wait_core, _serve_wait, cmd_wait, cmd_serve → mailbox.py: _claim` | 旧 O_EXCL 锁只裁决文件消费；尚未核对 V3 admission 的固定 owner lane/epoch。 |
| 组共享 inbox 领取 | `pool.py: _claim_group_task → mailbox._claim(locked=True)` | 组来源与角色目标需固定，持组锁不等于拿到核心执行权。 |
| 旧 Claude daemon | `worker_daemon.py: main → _wait_core → run_task → agentbus done` | 执行前与回填前都需统一裁决；原生运行的未知副作用仍需明确核对。 |
| Codex 独立信使与 App Server 投递 | `.codex/codex_wait_bridge.py: drain_inbox, _drain_claimable, WaitPump, _dispatch_batch`；`bus/codex_recovery.py: protect_claim, validate_delivery` | 当前具有精确 session 与旧线恢复 fence，但不是新核心路由 fence。信使可以直接领取当前 backlog；必须覆盖初始 drain、执行中 wait 和恢复批次。 |
| 一次性/池分片直接执行 | `dispatch_agent.py`、`pool_dispatcher.py` | 不能遗漏不走 `mailbox._claim` 的执行面。改普通 wait 不会封住这些路径。 |
| 旧手工 done 与消费者回填 | `core.py: cmd_done`，以及 `dispatch_agent.py` 直接写 sender done | 发布前没有新协议 claim/token/epoch 校验。新目的端账本不可把这些文件视为合法 receipt。 |
| 旧 Codex 断线恢复 | `codex_recovery.py: fence_tasks, reconcile_unfinished, _preserve_completed_processing` | 当前保护旧线未决执行；不得将其 processing 复制到 V3 并开始第二次执行。 |

## 4. 进入真实试点前的适配门槛

1. **选定精确来源。** 使用全新 namespace、固定消息类型和受控唯一写者；明确每个消费者与恢复器。旧 inbox 或现任角色不因同名加入试点。对于将来要迁移的旧来源，无法统一裁决的消费路径须确认停止并退出，或整段来源继续留旧线。
2. **冻结信封与 source ID。** namespace 跨重启稳定；整数与字符串 ID 区分；不可变种类、目标、正文大小和 SHA256 都参与冲突检查。源确认丢失只能返回原 admission，不能重新 submit。
3. **证明旧新排他。** 使用同一核心事务竞争并覆盖所有领取/完成路径、冻结与 epoch 切换；源文件锁、配置开关或 UI 提示均不足。旧线未适配时只能验证新的受控来源，不能宣称旧 fleet 排他已通过。
4. **目的端有真正持久协议。** 接收和查回执都精确绑定 destination namespace/id、delivery ID、payload 版本、字节数、摘要与存储修订；未知响应保持 held。仅写入 inbox、stdout 或内存队列不能作为 received。
5. **完成合成故障证据。** 并发重复接纳、事务中途失败、发送前后各崩溃窗口、迟到/冲突回执、fence、长文本边界、业务 A→B→恢复 A、迁移回滚都可重复验证。结果已完成后，投递故障不得生成新 Attempt。
6. **独立真实执行部署。** 试点业务包、状态根、workspace、固定运行时与专用 home 均明确；不读取旧业务或私有会话，不继承 3.0 验收认证复用授权。工具请求继续按当前纯文本策略拒绝。Claude 真实接通仍单列。
7. **按事实放行。** 单角色真实执行通过后再预编排双角色；租约在线、任务已领取、模型交互、平台完成、目的端保存、agent 处理、用户已读分别举证。最后两项未实现则保持未知。
8. **回退有账本。** 先冻结新接纳和新 claim，再排空或明确停止，核对在飞与 held，最后新 epoch 只影响未接纳消息。原 admission、结果与未决回执留在原库；不复制回旧 inbox、不覆盖新库、不原地降 schema。

本表只定义源码覆盖边界与放行要求，不启动真实试点、不发送通知、不迁移运行中的旧库，也不代替实际消费方逐项签收。

## 5. B-00 逐入口责任与证据矩阵

“参与”仅表示进入本批开发及合成验收范围，不表示验收已通过。“留旧线”表示新适配器不读取、领取、改写或恢复该来源；不会通过改旧端配置来形成这一隔离。“拒绝”表示不提供新基座接入能力。下面旧线的 namespace 均为 **未映射**，来源 kind 只用于说明静态语义，不能作为自动转换规则。

| ID / 入口 | 处理、namespace / kind | 实际生产、消费、完成或恢复路径（静态位置） | 本批共同守卫与隔离理由 | 负责人 / 验证证据 |
| --- | --- | --- | --- | --- |
| E-01 普通 send/request | 留旧线；未映射；旧 task/request/fyi 等 | `agent_bus/bus/core.py:99 _deliver`、`:128 cmd_send`、`:634 cmd_request`；`:113` 直接写角色 inbox；消费见 E-07/E-08/E-09 | 没有核心 admission。新 SourceStore 仅打开显式独立 root，不枚举或导入旧 inbox；不能将 task 字段相似视为兼容 | 运行负责人待指定。静态证据 S-01；旧线适配测试未实施 |
| E-02 done/notice/批量确认 | 留旧线；未映射；完成结果/notice | `core.py:680 cmd_done`、`:740 cmd_ack_notices`；`notify.py:75 _emit_completion_notice`、`:96` 直写 sender inbox；done 在 `core.py:707` 写 sender done | 旧 done、通知和批量确认不符合目的端 receipt 协议，不传给 SourceStore.publish，不转成任务；旧完成没有新 claim fence | 运行负责人待指定。S-02；旧完成发布适配未实施 |
| E-03 工作组 push/pull/fanout | 留旧线；未映射；组 task/分片 | `pool.py:229 cmd_send_group`、`:369 _route_assign`、`:381 _route_send`、`:565 cmd_fanout`；分别写成员 inbox、组 inbox、route_queue；消费见 E-07/E-11 | 组、分片和成员 ID 的稳定映射未确定，必须整组留旧。不能只改普通 send 后把组领取算覆盖 | 运行负责人待指定。S-03；组全入口排他测试未实施 |
| E-04 组恢复与待分配 | 留旧线；未映射；原 task/恢复 notice | `pool.py:409 _route_drain_group`、`:438 _route_reap_group`、`:496 route_on_member_done`；重排队列、派活和通知均可写回旧线 | 不读 V3 原 admission；新 transport 不调用组重排，不把旧处理文件复制到来源库。未来必须连同 E-03 接入 | 运行负责人待指定。S-04；旧重排/迟到完成测试未实施 |
| E-05 用户 TODO/answer | 留旧线；未映射；TODO/答复/派工 | `tasks.py:97 cmd_answer`、`:153 cmd_ask_user`；answer 在 `:124` 写 inbox；`ui/api.py:1699 todo_answer`、`:1726 todo_dispatch` | TODO、平台 Decision、Task、RuntimeApproval 分开；答复不构成新执行或工具授权。新适配器不读取 todos | 运行负责人待指定。S-05；TODO 迁移及授权映射未实施 |
| E-06 旧 WebUI 手工派活/done | 留旧线；未映射；旧 task/result | `ui/api.py:1522 send_task`、`:1549 done_task`；`ui/routes.py:201,225,227,247` 调用派活、待办及完成入口；send 后还可补写 notify 偏好 | 网页复用旧命令、仍可写旧 inbox/done；新工作台导航不是接管。受控来源没有旧 UI 写入入口 | 运行负责人待指定。S-06；旧网页派活/完成适配未实施 |
| E-07 wait/serve/共享 inbox | 留旧线；未映射；旧队列任务 | `core.py:174 _wait_core`、`:470 _serve_wait`、`:616 cmd_wait`、`:625 cmd_serve` → `mailbox.py:17 _claim`；`pool.py:184 _claim_group_task` 在 `:218` 另持组锁领取 | O_EXCL/文件移动只裁决旧队列；没有 `RoutingMixin` 执行授权。新 transport 不调用任何旧 claim，也不给旧消费者新来源目录 | 运行负责人待指定。S-07；双线生产执行竞争未验证 |
| E-08 Claude daemon | 留旧线；未映射；旧文本任务 | `agent_bus/worker_daemon.py:98 main` 在 `:110` 调 `_wait_core`，`:129` 调 `run_task`（定义 `:70`），后经 `agentbus done` 回填 | 领取、原生执行和发布均未调用新核心；不因使用同一 Claude/Codex 品牌而共享执行权限。首批不启动旧 daemon | 运行负责人待指定。S-08；Claude 真实适配及未知效果恢复未验证 |
| E-09 Codex 初始 drain/执行中 wait | 留旧线；未映射；旧单条/批次任务 | `.codex/codex_wait_bridge.py:453 drain_inbox`、`:464 _drain_claimable`、`:500 WaitPump`、`:688 _dispatch_batch`；`:472` 直接 claim，`:702` validate，`:708` 原生 turn/start | 旧 session 身份/fence 不能代替核心 lane/epoch。初始 drain、执行中 wait 和批次 dispatch 都未适配；新 transport 不导入 bridge，不连现有 session | 运行负责人待指定。S-09；三种批次逐项核心授权测试未实施 |
| E-10 Codex 断线恢复 | 留旧线；未映射；已领取/已投递/未决任务 | `bus/codex_recovery.py:61 protect_claim`、`:83 validate_delivery`、`:138 _preserve_completed_processing`、`:181 fence_tasks`、`:222 reconcile_unfinished` | 现有保护作用于旧线，保留不修改。不能把 processing/恢复标记复制成新 SourceStore 消息，也不能以旧标记代替新的 source ID 或核心授权 | 运行负责人待指定。S-10；旧恢复器对新 admission 的识别未实施 |
| E-11 dispatch/池分片直启 | 留旧线；未映射；adhoc/pool_shard | `bus/exec.py:17 cmd_dispatch` 在 `:54` 直接启动；`dispatch_agent.py:51 main` 在 `:100` 直写 sender done；`pool_dispatcher.py:71 _take_shard`、`:109 _spawn_worker` | 是不经 `mailbox._claim` 的独立执行面；仅修改 wait 无法封住。源适配器不读 `_dispatch`、组分片或其结果 | 运行负责人待指定。S-11；直启与直接发布适配未实施 |
| E-12 自醒/培训/机制探针 | 留旧线；未映射；提醒/培训/探针 | `identity.py:255 _auto_train_request` 在 `:269` 直写 inbox；`:427 cmd_selfwake_guard` 在 `:523` 直写；`probe.py:367 _dispatch_probe` 复用 send | 存在普通 send 之外的写者；正文里的“通知”“任务”不自动变为新 Task。首批不运行培训、自醒或探针命令 | 运行负责人待指定。S-12；稳定来源映射未实施 |
| E-13 孤儿/健康回收 | 留旧线；未映射；processing、done、恢复 TODO | `health.py:27 cmd_reap`、`:325 _health_sweep_core`；`selfwake.py:260 _requeue_orphans`；分别搬回 inbox、重路由 done 或写 TODO | 恢复器操作多个旧域，不能只把 send 隔离。新来源恢复只依据自己的 durable source/admission，租约失效不等于旧执行已退出 | 运行负责人待指定。S-13；旧恢复器核心裁决未实施 |
| E-14 外部直接文件写者 | 拒绝新接入；namespace/kind 未登记 | 仓库外手工复制、定时脚本及其他直接写 mailbox 的程序不能由本次静态源码穷尽 | 未登记协议不允许 publish/admit；不扫未知 JSON，不把“没人再写了”当隔离证明。未来发现一条旁路即冻结受影响来源 | 写者/负责人待逐项登记。S-14 是边界声明；不是已穷尽外部程序的证明 |
| E-15 V3 直接提交/执行 | 参与既有核心回归；直接 Task 无外部 namespace | `service.py:332 submit`、`:374 run`、`:383 run_as`、`:443 claim`、`:815 _execute`；`runtime.py:193 _run` 在 `:211` claim；`worker.py:61 run` → `members.py:178 claim_member_task`；`workbench_commands.py:88 execute` 在 `:111` submit | 所有执行最终到 `service.py:413 _claim` → `routing.py:311 _guard_route_claim`；直接 submit 不补建 source 映射，禁止先 submit 后 admit。成员 SQL 跳过被冻结来源后仍复核 `_claim` | 当前实施会话负责核心集成；真实运行负责人未指定。R-01/R-02/R-03；不代表新 transport 已验收 |
| E-16 V3 恢复/子任务/outbox | 参与既有核心回归；保持原 admission；子任务无伪造来源 | `service.py:679 reconcile_task`、`:725 resume_task`；`coordination.py:207 request_child`、`:58 outbox`、`:66 ack_outbox`；完成在 `service.py:621 complete` → `:597 _finish` | 恢复保留原 task/admission；子任务不继承伪造 source ID；`_owned_attempt:467` 和 lease 拒绝失效发布。outbox delivered 不等于目的端 receipt，未确认补充继续阻断成功发布 | 当前实施会话负责核心集成；真实运行负责人未指定。R-04/R-05；旧线恢复仍未适配 |
| E-17 新受控本机来源/结果 | 参与 dev.4 实施；显式新 namespace；仅 task，结果 text.v1 | 拟新增 `source_store.py: SourceStore`、`transport.py`；现有 `routing.py:199 claim_source/:244 admit_source/:294 confirm_source`、`deliveries.py:311 send_delivery_once/:327 reconcile_delivery`、`receipt_store.py:223 descriptor/:249 receive` | 拟新增端点永久绑定/接纳前预检；现有 `_source_owner:222`、`_guard_route_claim:311`、`_owned_attempt:467`、`complete_delivery:293` 分别守卫搬运、执行、发布、回执。源租约不授权执行，结果泵不能重跑模型 | 当前实施会话集成，SourceStore/端点/transport 子任务实施；真实运行负责人未指定。R-01～R-05 为核心既有证据；N-01～N-08 待实现验收 |

S-01～S-13 是对该行所列函数及调用/文件写入位置的静态核对记录，不是生产行为测试，也没有调用这些旧模块。旧表中 `ui/api.py` 和 `ui/routes.py` 指项目根的 `ui/`，不是 `agent_bus/ui/`。S-14 明确了不能靠源码穷尽未知文件写者这一限制。

旧线共同缺口是 **没有新核心的执行及发布授权守卫**，不是“旧线毫无保护”：旧文件锁、Codex 精确 session 检查和恢复 fence 仍有其原作用。本批以完全独立的受控来源缩小范围，不改写或废弃这些原保护。

## 6. 可复核的测试对应与接入案例

下表 R 项引用已存在的测试定义。B-00 仅核对定义与调用路径，不声称本轮运行通过；新增实现后由集成记录实际运行命令及结果。N 项是明确待编写的验收案例 ID，不是不存在的测试函数名或通过记录。

| 证据 ID | 测试定义 / 后续必要案例 |
| --- | --- |
| R-01 来源唯一性与冲突 | `tests_v3/test_routing.py:RoutingTests.test_concurrent_duplicate_admission_creates_exactly_one_task`；`test_source_replay_after_reopen_keeps_task_and_source_confirmation_separate`；`test_same_source_with_changed_text_or_kind_never_mutates_original`；`test_source_integer_string_and_namespace_are_distinct`；`test_failure_after_task_and_admission_insert_rolls_back_both` |
| R-02 全部 V3 领取守卫 | 同文件 `test_freeze_claim_blocks_all_direct_execution_entries_but_not_plain_tasks`、`test_member_skips_frozen_routed_task_and_claims_plain_task`、`test_frozen_claim_does_not_cancel_inflight_attempt_or_republish_it` |
| R-03 受控 lane、epoch 和租约 | 同文件 `test_lane_change_only_affects_new_admissions`、`test_legacy_admission_stays_legacy_after_new_epoch_selects_v3`、`test_source_claim_lease_fences_old_owner_and_does_not_duplicate_task`、`test_expired_legacy_execution_requires_explicit_reconciliation_and_fences_old_token`、`test_business_switch_prevents_cross_installation_claim_and_old_epoch_new_admission` |
| R-04 发布与原目的端恢复 | `tests_v3/test_deliveries.py:DeliveryTests.test_result_intent_same_transaction_rolls_back_even_after_delivery_insert`；`test_endpoint_commit_lost_response_recovers_without_second_receive_or_model`；`test_empty_other_root_with_same_namespace_cannot_requeue_a_lost_receipt`；`test_same_root_replaced_instance_cannot_claim_absence_or_accept_receipt` |
| R-05 既有边界 | 同文件 `test_unrouted_task_has_no_delivery_and_old_outbox_does_not_prove_receipt`、`test_readonly_queries_never_claim_initialize_or_acknowledge`；`tests_v3/test_routing.py:RoutingTests.test_claim_reopen_preserves_original_admission_and_fixed_destination` |
| N-01 来源存储与身份 | B-01：新 root 初始化并跨重启保留 namespace/instance；同 typed ID 同内容幂等，异内容/目标/类型冲突；未知协议/旧 mailbox 结构拒绝；不遍历旁侧旧目录 |
| N-02 端点准入 | B-02：未绑定或缺 descriptor 不接纳；源/目的端 root、instance、协议、限额不符在执行前拒绝，零 Attempt；绑定后同名换库不能继续接纳/发送；显式 schema 迁移及拒绝状态证据 |
| N-03 搬运确认窗口 | B-03：接纳前、接纳后源确认前、源确认后核心确认前、核心确认后源 complete 前分别中断；重启查回同 ID/admission/task；源的 confirmed 对象不能提前从恢复集合消失 |
| N-04 执行和发布 | B-04/B-05 受控范围：两个进程竞争、lane/epoch/freeze 切换、迟到拥有者、同/异完成摘要；记录实际执行启动次数，旧源码调用次数为零；不能用这一项填旧线各行的验收 |
| N-05 常驻搬运与停止 | B-06：启动 backlog、先 arm 再查再 wait 的到达窗口、长 IO/各自续租、强杀重启、停止后不领取；来源确认和结果发送是不同泵，不启动模型，不循环 sleep 扫目录 |
| N-06 held 管理 | B-07：原端收到/权威 absent/不可用/冲突/替换实例分别核对；显式同 ID 重试；GET 无写入；原 Attempt 数不增；来源 confirmed 未完成也有明确可恢复责任 |
| N-07 文本与业务替换 | B-08：中文、emoji、组合字符、换行及尾段摘要相同；边界/超限明确拒绝；同构建 A→B→恢复 A；source/admission/task/attempt/result/delivery/receipt 全链可查 |
| N-08 隔离负例 | B-08：旧 inbox/config/session 路径、symlink/junction/硬链接、同名源实例替换、未知写者/种类及未绑定端点均拒绝；任何旧路径数据未被打开，不能只断言没有生成 Task |

接受示例：在显式新 SourceStore 的 `synthetic.transport.v1` namespace 中 publish `kind="task"`、ID 为字符串 `"sample-001"`、正文为虚构文本，且新来源实例与目的端已绑定。搬运结束重启后再次读取这条记录，只能查回原 admission 和 Task；目的端收到结果也不自动生成接收方新 Task。

拒绝示例：把旧 `agent_bus/<role>/inbox` 作为 source root；将旧 notice、用户答复或正文中的“批准执行”转成 task/批准；用同名空目的端替代原实例；核心确认丢失后生成新 ID；把来源 lease 用作原生执行许可。上述拒绝是后续实现必须具备的行为，只有 N 项实际通过后才能作为运行证据。

未映射的旧线未来接入时，运行负责人须补齐每行的真实 namespace、所有写入/消费/恢复方、部署及停切范围，并取得逐行行为证据。该盘点应在明确授权的真实接管工作中进行，不能由本次静态核对越界补填。
