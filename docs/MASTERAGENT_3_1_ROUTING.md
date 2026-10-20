# MasterAgent 3.1 路由与本机持久回执

日期：2026-10-19。适用开发版：**`3.1.0-dev.3`，核心 schema 5**。本页说明已实现的公开接口；完整不变量见 [路由与回执契约](MASTERAGENT_3_1_ROUTING_RECEIPT_CONTRACT.md)，完成证据见 [进度](MASTERAGENT_3_1_PROGRESS.md)。

本批面向全新、独立、受控的文本来源。旧 fleet 的 send/request、通知、工作组、待办、WebUI、信使、daemon 和恢复器均未改接、未切流；完整清单见 [入口核对表](MASTERAGENT_3_1_ROUTING_ENTRYPOINTS.md)。下面的 `legacy` lane 是新核心对受控来源作出的归属裁决，不表示旧 bus 已调用此接口。

仅使用合成文本和本机独立目录验证，没有执行真实 Codex/Claude 模型，没有外发数据、读取或复制认证、登记 bus 身份或迁移真实运行库。工具与原生审批仍是原来的纯文本限制，本批不扩大工具权限。

## 1. 事实与存储边界

| 记录 | 表示什么 | 不表示什么 |
| --- | --- | --- |
| source claim | 某连接器临时拥有一个来源对象的搬运租约 | 获得执行任务的权利、已从外部邮箱删除消息 |
| admission | 来源身份和不可变内容已在核心事务接纳；V3 lane 同事务创建唯一 Task | 源端已确认、模型已开始、目的端已收到 |
| `source_confirmed=true` | 连接器明确回填源端确认事实 | 本方法实际操作了外部邮箱、模型已完成 |
| Attempt / Result | 既有平台执行归属与持久结果 | 结果已经送到另一存储域 |
| delivery `received` | 固定目的端的完整文本与持久回执通过核对 | 目的端 agent 已处理、用户已读 |
| 原 `outbox.delivered` | 原平台通知记录的状态 | 新协议持久回执 |

核心 `state.sqlite3` 是来源归属、Task/Attempt、投递和核对证据的权威库；目的端 `receipts.sqlite3` 是另一存储域。源确认、目的端调用都在核心事务之外。当前目的端只保存完整文本，不启动模型、不创建下游 Task、不解释正文中的派活或批准指令。`agent_processed`、`user_read` 保持未知。

## 2. 版本与显式迁移

`routing-command`、`routing-query`、工作台和投递命令只打开已经初始化且结构匹配的 schema 5；不会自动建库或升级。新合成部署可通过现有显式 `Platform` / `install` 流程创建。目录必须是本机独立绝对路径，位于源码、旧 bus、个人运行时 home 之外，不使用符号链接、junction 或网络路径。

旧 schema 1/2/3/4 只能执行明确的迁移命令，例如：

```powershell
python -B -m masteragent --state-root C:/ma31-synthetic/state migrate
```

这只是独立合成目录的命令示例，不是要求对现用状态执行。迁移在写锁中核对源 schema 和在飞状态，先保存 `backups/schema<原版本>-<随机值>.sqlite3` 一致备份，再提交新表、索引、迁移日志及 `user_version=5`；成功时撤销旧 Actor token。schema 2–4 的 running Task/Attempt、schema 3–4 的 pending/sending/held 执行补充、schema 4 的 ready/waiting/busy/stopping 成员会阻断迁移；租约过期不作为进程退出证明。schema 1 的旧 running task 转为 needs_reconcile。

迁移失败回滚结构、数据和版本，保留已经生成的备份。旧 outbox 的 delivered 原样保留，不能升级成 received；新路由与投递表初始为空。重复对完整 schema 5 调用 `migrate` 返回 `migrated=false`。没有 schema 4 只读兼容，也没有原地降级：恢复旧版须在独立目录使用迁移前备份，新库与其未决任务继续保留。

## 3. 来源与路由公开 Python API

以下方法位于 `Platform`，参数名同时是 `routing-command` JSON 字段名。管理 API 由可信本机控制方调用，不面向模型正文或未经验证的网络客户端。

```python
configure_route(
    route_id, source_namespace, owner_lane,
    installation_id, role_id, generation,
    destination_namespace, destination_id,
    *, expected_epoch=0, admission_open=True,
    claim_open=True, delivery_open=True,
    destination_limit=512000, lease_seconds=60, retry_seconds=5,
)
set_route_controls(
    route_id, expected_epoch,
    *, admission_open=None, claim_open=None, delivery_open=None,
)

claim_source(route_id, source_id, owner_id)
renew_source(route_id, source_id, owner_id, claim_epoch, claim_token)
admit_source(
    route_id, route_epoch, source_id, text, owner_id,
    source_claim_epoch, source_claim_token, kind="task",
)
confirm_source(admission_id, owner_id, source_claim_epoch, source_claim_token)

routes()
admissions(route_id=None)
admission(admission_id)
```

新 route 使用 `expected_epoch=0`，返回 `route_epoch=1`。更新必须提供当前 epoch；成功增加一代，过时写者得到 `stale_route`。同一 source namespace 只能有一个 route，既有 route 不能更换 namespace；业务安装必须活跃，角色和 generation 必须与安装一致。`owner_lane` 只能是 `v3` 或 `legacy`，当前 `kind` 仅支持 `task`。

`source_id` 只接受字符串或整数，`1` 与 `"1"` 是不同来源；布尔值拒绝。来源 key 使用带类型的规范 JSON，最多 200 UTF-8 字节。namespace、目标标识和 owner 等标识均为非空文本，不含控制字符、最多 200 UTF-8 字节。正文最多 64,000 UTF-8 字节，保持原换行、组合字符、首尾空白和完整尾段。

来源 claim 一次签发令牌，核心只保存摘要。租约默认 60 秒，可在 15–300 秒内配置；续租保留 claim epoch 和签发时的策略，更新 route TTL 不追溯改变旧租约。租约到期后可以为同一来源取得更大的 claim epoch；旧令牌不能接纳、续租或确认。

接纳用 `(source_namespace, 带类型 source_id)` 去重。映射、唯一 Task、接纳事件和待确认事件同事务保存。完全相同的重放返回原 admission/Task；改正文、种类或目标得到冲突。旧 route epoch 只可查回它原先接纳的同一对象，不能接纳新消息。新 epoch 不移动旧对象，已接纳的角色、owner lane 和目的端保持固定。明确跨代续接沿用原 Task/admission，执行仍经过当前安装代次的既有守卫。

连接器实际回填源端以后才调用 `confirm_source`。它只持久记录确认，不访问源邮箱。核心已提交但源确认丢失时，应续租或重新取得同一来源租约，再使用原来源身份和原信封重放；不得重新 `submit` 或生成另一个 source ID。

受控 `legacy` lane 另有以下排他接口：

```python
claim_legacy(admission_id, owner_id)
renew_legacy(admission_id, owner_id, claim_epoch, claim_token)
complete_legacy(admission_id, owner_id, claim_epoch, claim_token, result_text)
reconcile_legacy(admission_id, reason, *, safe_to_retry=False)
```

此 lane 不产生 V3 Task；`claim_legacy` 拒绝 V3 admission。旧执行租约失效后不自动重跑，保持 held/待核对；只有明确 `safe_to_retry=True` 才允许新的执行 claim。相同 owner、epoch、token 和结果摘要的完成重放返回原完成状态，异内容拒绝。这里没有旧 bus 文件读取或回填适配器。

## 4. 来源 CLI 与租约文件

```text
routing-command --action <action> --payload-file <UTF-8 JSON>
                [--lease-file <已签发租约文件>]
                [--lease-output <全新租约文件>]
```

必须同时提供全局 `--state-root`，不接受 `--runtime-config`。JSON 字段按上节方法严格匹配，多余、缺少、重复字段和非 JSON 数值拒绝。命令 JSON 文件最多 512,000 字节；它与正文/传输信封限额分别检查，不能以正文合规推断 JSON 文件也合规。

| action | 参数与本地文件 |
| --- | --- |
| `configure` / `controls` | 对应 configure_route / set_route_controls 字段；不使用租约文件 |
| `claim-source` | `route_id, source_id, owner_id`；必须提供全新 `--lease-output` |
| `renew-source` | `route_id, source_id, owner_id`；必须提供 `--lease-file` |
| `admit-source` | `route_id, route_epoch, source_id, text, owner_id`，可选 `kind`；必须提供 `--lease-file` |
| `confirm-source` | `admission_id, owner_id`；必须提供 `--lease-file` |
| `claim-legacy` | `admission_id, owner_id`；必须提供全新 `--lease-output` |
| `renew-legacy` / `complete-legacy` | `admission_id, owner_id`，complete 还需 `result_text`；必须提供 `--lease-file` |
| `reconcile-legacy` | `admission_id, reason, safe_to_retry`；不使用租约文件 |

领取命令先独占新建租约文件，再领取并写入；已有文件绝不覆盖。标准输出只显示公开领取字段和 `lease_file` 路径，不打印 claim token，legacy claim 也不在标准输出打印任务正文。后续命令只从所选租约文件注入 epoch/token，禁止在 payload 里另传这些字段。该文件是此次本机搬运授权，不能放入网页、通用日志或外部服务。

示例假定已经存在一个独立合成安装 `demo-a`，其当前 generation 为 1、角色为 `worker`。在本机准备 `configure.json`：

```json
{
  "route_id": "synthetic-route",
  "source_namespace": "synthetic.source.v1",
  "owner_lane": "v3",
  "installation_id": "demo-a",
  "role_id": "worker",
  "generation": 1,
  "destination_namespace": "synthetic.destination.v1",
  "destination_id": "receiver",
  "expected_epoch": 0
}
```

`claim.json` 内容为：

```json
{"route_id":"synthetic-route","source_id":42,"owner_id":"synthetic-connector"}
```

`admit.json` 使用相同来源和返回的 route epoch：

```json
{"route_id":"synthetic-route","route_epoch":1,"source_id":42,"text":"仅合成文本，尾段完整。","owner_id":"synthetic-connector"}
```

执行这些独立目录示例：

```powershell
python -B -m masteragent --state-root C:/ma31-synthetic/state routing-command --action configure --payload-file C:/ma31-synthetic/control/configure.json
python -B -m masteragent --state-root C:/ma31-synthetic/state routing-command --action claim-source --payload-file C:/ma31-synthetic/control/claim.json --lease-output C:/ma31-synthetic/control/source-42-lease.json
python -B -m masteragent --state-root C:/ma31-synthetic/state routing-command --action admit-source --payload-file C:/ma31-synthetic/control/admit.json --lease-file C:/ma31-synthetic/control/source-42-lease.json
```

这些命令只保存 Task，不运行模型，也不确认外部源端。源端确认后另以 `{"admission_id":"<返回的 admission_id>","owner_id":"synthetic-connector"}` 调用 `confirm-source`。

只读查询可使用：

```text
routing-query routes
routing-query admissions [--route-id <id>]
routing-query admission --admission-id <id>
routing-query deliveries [--installation-id <id>]
routing-query delivery --delivery-id <id>
```

每项均需全局 `--state-root`，只接受所列筛选条件。查询不领取、不维护、不确认。管理 API 的 admission 可包含完整来源正文；delivery 查询可显示固定目的端绑定，均不返回租约 token/hash。普通工作台使用更窄的公开字段白名单。

## 5. 结果投递与固定目的端

既有 Task 正常完成时，共同 `_finish` 事务同时保存 Result 与 delivery 意图。普通直接 submit 的任务没有外部 admission，因此不会凭空生成投递。成功输出与明确错误结果都可形成文本 payload；结果正文超出路由声明上限或实际信封上限时，完整 Result 保留，投递 rejected，不截断也不重跑。

本机目的端需先显式初始化；该命令只接受自己的 receipt root，**不带 `--state-root`**：

```powershell
python -B -m masteragent receipt-init --receipt-root C:/ma31-synthetic/receiver --destination-namespace synthetic.destination.v1 --destination-id receiver
```

初始化返回 descriptor。目的端内保存随机且持久的 `destination_instance`；默认文本上限 512,000 字节，完整信封 1,048,576 字节。Python `ReceiptStore(..., initialize=True, max_text_bytes=..., max_envelope_bytes=...)` 可以显式采用更小限额；当前 CLI 的 receipt-init 和后续打开使用默认目的端限额。

核心公开投递 API：

```python
deliveries(installation_id=None)
delivery_status(delivery_id)
delivery_envelope(delivery_id)
claim_delivery(delivery_id, sender_id, receiver)
renew_delivery(delivery_id, sender_id, claim_epoch, claim_token)
begin_delivery(delivery_id, sender_id, claim_epoch, claim_token)
complete_delivery(delivery_id, sender_id, claim_epoch, claim_token, receipt)
hold_delivery(delivery_id, sender_id, claim_epoch, claim_token,
              reason="receive_unconfirmed")
send_delivery_once(delivery_id, sender_id, receiver)
reconcile_delivery(delivery_id, receiver, *, retry_absent=False)
```

`receiver` 必须提供 `descriptor()`、`receive(envelope)`、`query(envelope)`；当前随包实现仅为本机 `ReceiptStore`，未实现网络目标或旧 bus 目标。descriptor 在核心事务外读取，首次 claim 在同一事务内固定规范绝对 root、namespace/id、instance、协议、实际限额。旧对象后续换目标根目录、重建同名库、改变实例或限额都会拒绝；不能把新空库的 absent 当成原库没收到。

目的端 `receive` 以 delivery ID 幂等：同 ID 同完整信封返回原 receipt，异内容拒绝。完整正文与回执在目的端自己的事务中提交。回执核对目的端 instance、namespace/id、delivery ID、正文大小/SHA256、完整信封 SHA256、receipt ID 和正整数存储修订；非法或额外 schema 对象、无法打开或实例变化不能提供权威 absent。

## 6. 单次发送、held 与明确重试

```powershell
python -B -m masteragent --state-root C:/ma31-synthetic/state delivery-send <delivery_id> --sender-id synthetic-sender --receipt-root C:/ma31-synthetic/receiver
python -B -m masteragent --state-root C:/ma31-synthetic/state receipt-query <delivery_id> --receipt-root C:/ma31-synthetic/receiver
python -B -m masteragent --state-root C:/ma31-synthetic/state delivery-reconcile <delivery_id> --receipt-root C:/ma31-synthetic/receiver
```

`delivery-send` 只进行一次 claim → 持久 sending → 事务外 receive → 校验回执。不能从 CLI 注入“已接收”回执。发生接收/回执提交异常时保存 held；若发送者已过期，保留当前账本事实，后续显式核对将过期 sending fence 为 held。没有自动循环重发或常驻投递进程。

`receipt-query` 仅查询**所选目的端自身**的事实，不修改核心账本，也不放行重发。它返回 received、absent、unavailable 或 conflict；查询另一个同名空库即使得到 absent，也没有原投递的恢复权限。只有 `delivery-reconcile` 将证据与核心固定 root/instance/完整信封及当前 revision 共同核对后，才可更新平台状态。

| 核对结果 | 平台处理 |
| --- | --- |
| 匹配的持久 receipt | 收敛为 received；不再次调用 receive、不重新执行 Task |
| 匹配的权威 absent | 默认继续 held；只有显式 `--retry-absent` 才允许原 delivery 重新成为 pending |
| unavailable / conflict / 缺少或不匹配权威字段 | 保持 held，不猜测效果 |
| root 或 instance 不匹配 | 拒绝采用这个目的端，原记录保持；应检查绑定并恢复正确目的端 |
| 发送上限耗尽 | 保持 held 和持久证据，等待本机后续处理 |

明确恢复示例：

```powershell
python -B -m masteragent --state-root C:/ma31-synthetic/state delivery-reconcile <delivery_id> --receipt-root C:/ma31-synthetic/receiver --retry-absent
```

该命令只在匹配权威 absent 时保存重试许可，不执行发送。之后仍使用**原 delivery ID、原字节、原目的端**单独调用 delivery-send；默认至少间隔 5 秒，策略可在 5–60 秒配置，每条 delivery 最多进入 sending 三次。received 表示持久保存，目的端处理与用户已读继续未知。

三个 route 开关相互独立：`admission_open=false` 冻结新来源接纳，旧 admission 可重放；`claim_open=false` 停止新执行领取，在飞拥有者仍可按原租约完成；`delivery_open=false` 阻止新的结果发送，但不阻止已 sending/held 的回执核对。更新开关也使用 expected epoch 并生成新版本。它们不等同 member stop，不证明进程已退出。

## 7. 工作台与待验范围

HTTP API 保持 `3.1-dev1` 与 `/api/v1`。当前配置声明 `contract_revision=2026-10-19-routing-receipts-1`、`capabilities.result_delivery=true`。任务详情读取 `deliveries`，跨代任务链的 results 项读取可空 `delivery`；页面不获得 source/发送令牌、内部信封、策略或目的端本机 root，也不执行发送/核对。

当前交付证明的是合成状态下核心事务、去重、冻结、租约 fence、完整文本、本机目的端持久回执、迁移和故障恢复。旧 fleet 全入口适配、常驻来源搬运/心跳、自动用户待办、真实单角色/预编排双角色试点以及 Claude/混合团队都继续单列。模型在线、消息可投递、任务完成、目的端保存分别验收，不能由其中一个状态推断其余状态。
