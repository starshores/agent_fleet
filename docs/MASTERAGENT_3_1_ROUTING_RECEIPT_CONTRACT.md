# MasterAgent 3.1 持久路由、回执与 API 兼容契约

日期：2026-10-19。用户已采纳 [审阅取舍](ASTRA_3_1_REVIEW_DECISIONS_20261019.md) 的 A→B→C→D 顺序。**`3.1.0-dev.3` 已实现阶段 B 的 schema 5 核心：来源映射、路由代次、租约与冻结、结果投递账本、本机持久目的端和显式核对。** 公开 API/CLI 见 [使用说明](MASTERAGENT_3_1_ROUTING.md)，验收结果见 [进度](MASTERAGENT_3_1_PROGRESS.md)。旧 fleet 的发送、消费和恢复入口尚未适配，也未切流；真实单角色、双角色和混合运行时试点尚未执行。本文仍保留这些后续放行要求，不把核心交付等同完整接管。

本批只操作合成业务与独立本机状态，不外发、不读取或复制认证、不注册 bus 身份，不迁移真实运行库。3.0 验收目录间认证复用授权不延伸至本批。

现有 `outbox.delivered` 不是本文的目的端回执，不证明目的端已保存、agent 已处理或用户已读。工具 `command` / `file_change` 继续全拒绝；原生审批见 [独立契约](MASTERAGENT_3_1_NATIVE_APPROVAL_CONTRACT.md)，使用后续实际分配的 schema 版本，不混入本次 schema 5。

## 1. 边界、权威来源与不变量

每个独立 state-root 内的核心 SQLite 是路由归属、来源映射、Task/Attempt、发送领取和回执的唯一权威库。业务适配器只调用公开领域接口，不另建可独立决定任务所有权的路由文件或任务库。源邮箱和目的端各自仍是另一存储域；文件写入、源确认与核心事务不能伪装成同一原子事务。

必须保持：

1. 同一来源身份只产生一个原始平台任务；来源映射与新 Task 必须在同一个核心事务提交。续接沿用已有任务链，执行重试生成新 Attempt，不生成新的来源身份。
2. 一条来源消息只授权旧线或 V3 一方领取。归属一经持久接纳即固定；路由代次变更只作用于尚未接纳的新消息。
3. 执行与结果投递分开。结果投递失败不能重跑模型；重复目的端接收只能返回原回执，不能重新执行接收方任务。
4. 不明执行进入 `needs_reconcile`；不明跨域接收进入 `held`。缺少回执不是“没有接收”的证据，租约过期不是“进程已经退出”的证据。
5. 同业务、代次、角色、任务、Attempt、目的端及路由代次均精确绑定。旧 bus role、平台 Actor/member 与原生 session/thread 不因同名、目录或 PID 被视为同一身份。
6. 正文作为不可信任务数据传递。接口不解析其中的“批准”“发给某角色”等文字为授权，不允许模型自行选择目标、创建任意任务或扩张工具权限。

## 2. 来源身份、路由代次与最小模型

以下逻辑模型已落入 schema 5：`routes` / `route_versions`、`source_claims`、`source_admissions`、`legacy_claims`、`routing_events`、`result_deliveries` / `delivery_evidence`。最终 DDL 分别在 [routing_schema.py](../masteragent/routing_schema.py) 和 [delivery_schema.py](../masteragent/delivery_schema.py)，CLI 字段与公开 Python 方法见 [使用说明](MASTERAGENT_3_1_ROUTING.md)。目的端使用独立 `receipts.sqlite3`，有自己的 schema 1 与实例身份，不是核心库的另一个写者。

| 逻辑对象 | 必要字段和约束 |
| --- | --- |
| 路由策略 | `route_id`、单调增加的 `route_epoch`、来源 namespace、允许的消息种类和消费方、旧线/V3 目标、安装/角色/代次、接纳开关、新 claim 开关、策略摘要与修订；同一生效范围只有一个当前 epoch |
| 来源映射 | 核心生成的 `admission_id`；`source_namespace`、`source_id` 唯一；正文 UTF-8 字节数和 SHA256、不可变消息种类/目标/信封摘要；接纳时 route/epoch、固定 owner lane；V3 路径绑定唯一 task_id；源确认状态与时间 |
| 领取记录 | 精确对象、领取者身份、单调 `claim_epoch`、随机令牌摘要、期限和策略快照；源领取、执行 Attempt 和结果投递各有独立领取，不借用同一个 lease。源租约续租使用签发时策略，route 后续修改 TTL 不影响旧租约 |
| 结果投递 | `delivery_id`、来源 admission、installation/generation/task/attempt/result、固定目的端 namespace/id、route_epoch、正文大小/摘要、payload 版本、状态/revision、发送领取及回执证据；同一 result 与目的端只有一条逻辑投递。首次发送领取还绑定规范 root、持久 instance、协议和实际限额 |
| 目的端回执引用 | 目的端 namespace、不可变 receipt_id、delivery_id、正文大小/摘要、接收存储修订和接收时间；原回执只追加核对证据，不覆盖为另一个目的端的回执 |

`source_namespace` 由可信连接器配置和源实例标识组成，必须跨适配器重启稳定，不能由正文指定。源实例恢复或 ID 命名规则变化须显式建立新的 namespace，不能静默重用旧 ID。`source_id` 仅接受源契约允许的字符串或整数，拒绝布尔值；整数 `1` 与字符串 `"1"` 以带类型规范表示区分，不能直接 `str()` 合并。namespace、字符串 ID 和目的端标识均为非空有效文本，不含控制字符；namespace、ID 的规范表示和目的端标识各最多 200 个 UTF-8 字节。遇到更长原生 ID 使用适配器的版本化无碰撞映射并持久保存可逆对应，不截断 ID。

正文摘要为原文 UTF-8 字节的 SHA256；不 trim、不归一换行、不 Unicode 归一化。信封摘要采用冻结的规范化 JSON 编码，包含消息种类、精确来源、目标和正文摘要，排除时间戳与重试计数等可变传输字段。相同来源身份且不可变字段完全相同返回原映射/Task；同 ID 异正文、异类型或异目标明确冲突，不更新原任务。旧 epoch 的重试可返回其原接纳结果，但不能用旧 epoch 接纳新消息。

V3 接纳事务必须原子地完成：验证来源与策略 → 比较 epoch/接纳开关 → 核对或建立来源映射 → 建立新 Task → 写公开接纳事件和源确认待办。若任一步失败，映射和 Task 均不存在；禁止先提交 Task 再补映射，也禁止以 HTTP 先后调用两个现有公共写操作冒充该事务。核心事务外再回填源确认；确认丢失只查回原 admission，不再次提交新任务。

## 3. 所有入口和消费者共用排他规则

切换清单必须枚举实际入口和领取路径：普通 send/request、通知、工作组、待办、WebUI、自动重试/恢复器、直接 inbox 消费及其他定时适配。每项标为“纳入同一裁决”“明确留旧线”“拒绝该类型”之一；未知入口不自动接入。平台 Task 不直接等同旧 TODO，通知或回填也不自动翻译成新的执行任务。

本批具体清单见 [入口核对表](MASTERAGENT_3_1_ROUTING_ENTRYPOINTS.md)。V3 的 `run`、`run_as`、`claim`、原生运行器和 `serve` 经过共同 `_claim` 守卫；成员队列跳过被禁止新 claim 的路由任务。旧 bus 各入口全部明确留旧线。新增 `claim_legacy` / `complete_legacy` 只为全新受控来源提供另一 lane 的排他接口，未被旧消费者调用，不是“旧 bus 已接通”的证据。当前接纳消息类型仅为明确的 `task`，其他类型拒绝。

排他裁决位于核心事务。旧消费者和新执行器领取前均须核对来源映射的固定 owner lane 与 epoch，取得该对象唯一的有效 claim；发布结果前再次校验领取令牌和 fence。只修改 `ab send`、只改发送去向或依赖约定不再消费，均不能通过验收。若某个旧消费者无法执行裁决或存在绕过路径，必须先停止该路径并确认退出，或将其来源范围完全留旧线；不能对这部分打开 V3 路由。

首次裁决为旧线的消息也保留来源归属记录，防止之后误当新消息接纳；这类记录没有 V3 Task。首次裁决为 V3 时，来源映射与 Task 按上一节同事务提交。源端临时领取只用于连接器搬运和源确认，不获得另一份执行权；源领取落盘后核心事务前崩溃，恢复只按原来源身份继续同一次裁决。

claim 通过事务 compare-and-set 取得，令牌只交给精确执行者，不进入网页、通知或普通日志。心跳仅延长当前 claim_epoch；被撤权的旧令牌不能续租、完成或确认回执。新 claim 必须取得更大的 claim_epoch。重新领取结果投递的资格不意味着可盲目再次发送，须先按第 5 节核对已有 sending/held 的目的端状态。任务执行失联仍沿用现有 `needs_reconcile` 和明确新 Attempt 规则，不把路由 lease 当作自动重跑许可。

迁移、激活新 epoch 与冻结均与 claim 使用同一权威锁/事务边界。不得持有 SQLite 事务等待源邮箱、目的端网络/文件响应、模型或用户答复。

## 4. 目的端持久接收协议

阶段 B 的目的端必须提供两个有版本的语义操作：**按 delivery_id 幂等持久接收**，以及**按同一 delivery_id 查询持久回执**。协议可由本机适配器实现，不要求 HTTP；但持久性、鉴权和隔离不能只存在于进程内缓存。

接收键为 `(destination_namespace, delivery_id)`。目的端先验证固定目标、payload 版本、正文大小和摘要，再在其自己的事务/等价原子提交中同时保存完整正文和接收记录，最后返回回执。相同键同内容返回原 receipt；同键异内容返回冲突并保留原记录。进程收到字节、把文字放入内存队列、写了一段引言或完成发送方本地 flush 均不能返回持久接收成功。

回执必须带 purpose/协议版本、目的端 namespace、delivery_id、receipt_id、正文 UTF-8 大小与 SHA256，以及可查回的持久存储修订；平台校验与固定投递记录完全一致后才记 `received`。目的端时间只作证据，不用跨进程时钟顺序决定唯一性。来源确认、平台完成、目的端保存、目的端 agent 处理、用户已读是独立事实；后两者若未实现，应保持未知。

本批 `ReceiptStore` 使用 `text.v1` / `ma-receipt-v1`，回执另带 destination id、持久随机 `destination_instance` 和完整信封 SHA256。`claim_delivery` 在事务外读取已初始化目的端 descriptor，并在领取事务中首次固定规范绝对 root、namespace/id、instance、协议和限额。后续领取与核对须精确匹配；回执与权威 absent 还要匹配 instance。相同 namespace/id 的另一个空目录、同 root 的新数据库实例均不能充当原目的端的“未接收”证据。已选择的 `ReceiptStore` 对象在每次操作时复核实例；未知触发器、视图和显式索引使目的端失去受支持的结构资格。

查询必须区分：`received`（返回原回执）、`absent`（在该目的端权威存储中明确未找到）、`unavailable`（无法确认）和 `conflict`（ID/内容不符）。仅有发送超时、网络错误或空响应不能当作 `absent`。目的端不支持幂等接收和权威查回执时，该目标不得参加真实试点，投递保留 `held`；不得通过改 delivery_id 或改源 ID“修复”。

跨域采用同 ID 重试和查回执，不承诺 exactly-once 外部效果。目的端持久接收去重不证明 agent 处理或模型副作用恰好一次；若未来接收后要创建目的端 Task，该 Task 与接收记录还需目的端自己的事务幂等设计和独立验收。

## 5. 投递状态、恢复与 fencing

| 状态 | 事实、允许的后续和禁止推断 |
| --- | --- |
| `pending` | 平台结果与投递意图已在同一核心事务保存；尚未授权发送，不代表目的端已有结果 |
| `claimed` | 一个发送者已持久取得 claim；发送前复核归属、lease、冻结策略及内容摘要；不代表字节已写出 |
| `sending` | 发送意图已持久记录，事务外开始接收调用；此后崩溃或失联可能已经在目的端落盘 |
| `received` | 合法目的端持久回执已校验并保存；不表示 agent 处理或用户已读 |
| `held` | 发送效果、归属或内容存在不明，停止自动发送；保留原 ID、结果和原因，不恢复成可自由重发的新消息 |
| `rejected` | 在发送前确定目标/协议/大小不支持，或目的端明确且可验证地拒绝接收；保留结果，修改目标或内容需新的显式业务决定，不原地改 payload |

结果成功提交与 `pending` 意图须同一核心事务；发送资格在独立事务领取。领取者提交 `sending` 后，在事务外调用目的端，最后以匹配 claim_epoch 保存回执。已被 fence 的发送者不能直接改账本；迟到回执可作为待核对证据，由当前核对者验证原 delivery_id、固定目的端、完整摘要后收敛，不能据其创建新授权。

`claimed` 在明确尚未进入 `sending` 时可到期后重新领取；`sending` 遗留必须先转 `held` 并查目的端。查得匹配原回执则收敛到 `received`；权威 `absent` 且目的端幂等协议有效、当前策略仍允许时，可显式记录核对结果后用**原 delivery_id 和相同字节**重试。目的端不可用、摘要冲突或非权威 absent 一律继续 held。任何恢复都不得重新运行已完成的模型或生成替代结果。

阶段 B 新增的搬运/投递 claim 默认 TTL 为 60 秒，可在 15–300 秒内配置，并保存不可变策略快照。这不修改现有 member/Attempt 租约。当前提供 `renew_source`、`renew_legacy`、`renew_delivery`，没有启动自动信使或心跳进程；未来常驻适配器的默认心跳应为 20 秒且不大于所选 TTL 的三分之一，须另行验证。单一投递最多 3 次发送调用，合法重试间隔默认 5 秒、可在 5–60 秒内配置；只有明确未开始发送或按上一段查得权威 absent 才可重试，未知响应不消耗几次试发来“碰运气”。超限保存 held 和本机证据事件；自动转成用户待办尚未实现。事件/watcher 只唤醒工作，不构成领取或送达证据。界面读取和查询回执不能暗中领取、发送或确认。

## 6. 长文本完整性与明确上限

第一阶段只传输完整内联 UTF-8 文本，不进行静默截断、分片拼接、摘要替代或自动上传。以下是阶段 B 的上限，必须在代码与边界测试中一致执行，不宣称当前旧通道已经支持：

| 对象 | 阶段 B 上限与依据 |
| --- | --- |
| 初始任务正文、单条运行中补充 | 各最多 64,000 个 UTF-8 字节，与当前公共命令及补充接口边界一致 |
| 单个最终结果正文 | 最多 512,000 个 UTF-8 字节，与当前 Codex App Server 最终文本边界一致；不把 commentary 当 final |
| 传输信封 | 序列化后最多 1,048,576 个 UTF-8 字节，包含元数据和 JSON 转义开销；正文未超限但信封超限也明确拒绝 |
| 实际连接器/目的端 | `configure_route.destination_limit` 作为接纳策略快照；首次投递领取读取真实目的端 descriptor，再验证实际限额能够容纳完整正文和信封。常驻适配器真实放行前还需完成接纳前目标验证，未声明或未实测的旧目标不可启用 |

数字均为字节，不是字符数。先验证 Unicode 能无损编码，再以实际 UTF-8 字节计数；文本原样保存，JSON 编码过程不得改变解码后正文。若完整原生请求上下文或协议行有更小上限，还须在执行前验证序列化后的实际包络，不能仅根据用户正文合规就声称执行可接受。

初始任务/补充超限在保存前拒绝并返回明确上限。已产生结果超出目的端边界，保留平台完整结果与摘要，将投递记 rejected 或 held（取决于是否已经发起），不得截掉尾段后报告成功，也不得自动重跑模型。阶段 C 若增加本机制品引用，必须作为目的端明确支持的新 payload 版本验收，不能偷偷把正文换成路径或外链。

验收逐层保存“原生最终文本 → 平台结果 → 目的端保存文本”的字节数和 SHA256，核对尾段哨兵并保留可逆 source/admission/task/attempt/result/delivery/receipt ID 链。至少覆盖中文、emoji、组合字符、CRLF/LF、长行、多段 commentary 后 final、恰好上限与超过一字节、序列化膨胀、目的端比平台上限小及回执乱序。正文不写入通用日志；验收材料只使用虚构内容。

## 7. 冻结、停止新接纳与回退

三个控制必须分别定义：

- **停止新接纳**：关闭指定来源/目标的新 admission。已持久接纳消息仍保持原 owner lane；重试可查回原接纳结果，不迁回旧 inbox。
- **禁止新 claim**：`claim_open=false` 阻止新的执行领取，允许当前在飞拥有者按既有权限自然结束；这项 drain 开关已实现，不能用 member stop 冒充，也不提供进程退出证明。
- **停止现有 member/Attempt**：沿用现有停止和 fence 语义，会撤销活跃 Attempt 发布资格，可能进入需核对；不保证自然排空，也不证明 OS 进程已退出。

冻结新接纳不自动停止目的端回执核对；是否继续发送已有 pending 结果由独立投递开关和记录的冻结策略控制。已 sending 的效果不因开关改变而被抹去，继续查回执。暂停后的重新启用须记录新策略修订，不追溯改变已接纳对象的目的端或 owner lane。

回退顺序：冻结并记录来源边界 → 禁止新 claim → 选择自然排空或明确停止 → 核对在飞与 held → 发布新的路由 epoch 供之后未接纳消息走旧线。旧消息保持原账本归属，不能同时搬回旧 inbox、覆盖新库或重放旧成果。切换清单逐项核对源确认、task/attempt/member、未确认补充、最终结果、投递回执和拥有者退出证据。未决项继续留在原账本，回退完成不能将其隐去。

## 8. schema 迁移与版本边界

阶段 A 使用 schema 4。阶段 B 核心已在 **`3.1.0-dev.3` 使用 schema 5**；支持源 schema 1/2/3/4 经显式 `migrate` 升级。新程序打开旧 schema 时明确拒绝，没有旧 schema 只读兼容分支。审批使用此后的实际版本，不挤入本次路由版本，也不为编号捆绑上线。

1. 冻结全部受支持源版本结构及 SQL fixture。新映射、索引、账本与约束不能偷偷加到 schema 4；旧程序遇到新版本或不符结构须明确拒绝。
2. 迁移前停止新接纳、禁止新 claim，使用原版本核对与停止执行。schema 2/3/4 的 running task/Attempt、schema 3/4 的 pending/sending/held 运行中补充、schema 4 的 ready/waiting/busy/stopping member 均拒绝升级，不根据过期时间猜进程退出。schema 1 没有可验证的 Attempt 所有者，旧 running task 转 needs_reconcile；不会自动执行。将来迁出 schema 5 时，有效来源/投递 claim 与未核对 sending 同样必须阻断；本批没有这种后续迁移。
3. 显式 `migrate` 在写锁内验证源版本、精确结构和状态，取得一致备份，再在事务中完成表/索引、migration_log、必要凭据失效与 user_version 更新；完整结构和外键检查后提交。GET、首次命令、网页或执行服务启动不得迁移。
4. 任一步失败回滚结构、数据、版本及凭据状态，保留备份；重试必须识别是否实际完成。旧 schema 4 outbox 的 delivered 不能迁成目的端 received，只能保留旧事实并标明没有此类证据。
5. schema 5 写入后不能只换回 3.0/既有 schema 4 二进制回退。先冻结与核对，在独立目录使用匹配旧版本打开迁移前备份；新接纳和未决投递仍保留于原库，不能覆盖原库或原地降 user_version。
6. 当前 GET、公共查询和命令都不迁移；schema 4 只读兼容未实现。`migrate` 遇到完整匹配的 schema 5 只返回 `migrated=false`，不构成新的迁移或在飞清空证明。成功升级撤销旧 Actor token；恢复执行需要重新明确绑定部署身份。

数据库版本、公开 API 契约版本和发行包版本分别管理。schema 5 不等于 3.1 正式发行，开发包交付也不等于旧 fleet 已切换。

## 9. 阶段 A 公开 API 兼容契约

当前公开 `api_version` 保持 **`3.1-dev1`**，HTTP 路径仍为 `/api/v1`。新增可选观察字段和独立只读入口不改变已有字段含义，不跟随包号改名。客户端须忽略不认识的响应附加字段；服务端继续严格验证请求，不能因响应可扩展而接受未知命令字段。

阶段 A 曾增加 `task_history` 能力；阶段 B 当前配置响应 `data` 为以下附加声明：

```json
{
  "contract_revision": "2026-10-19-routing-receipts-1",
  "capabilities": {"task_history": true, "result_delivery": true}
}
```

`contract_revision` 标识已冻结的附加契约；能力键只声明对应入口可用，不代表写入授权、真实运行验证或所有 3.1 能力完成。新客户端遇到缺少 capability 的旧服务时不得请求任务链入口，可继续使用已有查询；旧客户端可忽略新字段。未知修订号不等于 API 不兼容，客户端按其认识的能力使用接口；未知 API 版本则明确拒绝相关功能，不猜测响应语义。现有 UI 对 `api_version` 的检查继续保留。

新增只读接口：

`GET /api/v1/installations/{installation_id}/task-history/{task_id}`

CLI 对应 `workbench-query task-history --installation-id ... --task-id ...`。参数：`generation` 是 UI 选中的观察上下文；`section` 默认 `generations`；`limit` 为 1–100、默认 20；`cursor` 可选。目录 section 禁止 `record_generation`；其余 section 必须指定属于该任务链的 `record_generation`，不存在时返回 `history_generation_not_found`。

原有响应 envelope 保留，`source.generation` 始终表示 UI 观察上下文，不被历史记录的代次替换。`data` 结构冻结为：

```text
scope: {kind: "task_history", task_id}
task: 当前稳定任务摘要与 text，含当前持久 state / dispatch_generation
section: generations | continuations | attempts | decisions | results | inputs | events | notifications
record_generation: 目录为 null，其余为被读取的关联代次
readonly: true
items: 当前 section 的公开记录页
next_cursor: 后续游标或 null
scope_note: 当前上下文、历史只读及非历史时点快照说明
```

`generations` 目录包含原始代次、当前 dispatch 代次、continuations 两端以及关联记录涉及的代次；按代次倒序，每项为 `{generation, is_original, is_current_dispatch, is_installation_current}`。`continuations` 返回触及所选记录代次的 from/to 边。其他 section 复用既有公开白名单；attempt 可含 runtime，不含 operations。历史只读不能放宽旧代写入、批准、领取或停止守卫。

游标绑定 state-root、installation、task、UI generation、record_generation 和 section，不能跨范围复用。首页固定记录加入上界用于分页，不构成历史时点快照；可变记录仍显示查询时事实。原任务详情接口继续只返回 dispatch 属于所选代次的任务，本次不改变旧接口语义。

本节保留阶段 A 实现契约；阶段 B 在当前任务详情增加 `deliveries` 公开投影，在任务链 results 项增加可空 `delivery`。`result_delivery` 只声明这些读取能力，不开放网页发送、核对或路由管理；本批路由写操作仅由显式本机 CLI / Python API 提供。完成与验收状态以 [进度](MASTERAGENT_3_1_PROGRESS.md) 为准。以后若改动已有 source 含义、必填字段、状态/枚举语义、请求格式或错误恢复语义，必须发布新 API 版本及明确的版本选择/协商，不复用 `3.1-dev1` 改义。独立附加端点可继续 v1，但需独立验证器、能力键和旧客户端样本回归。

最小兼容矩阵：旧客户端读取新服务仍通过原样本；新客户端读取无能力字段的旧服务不调用新入口；新能力开启后分页和历史只读正确；未知附加字段可忽略；错误 API 版本、跨范围游标和错误参数明确拒绝。能力声明不代替服务端每次领域校验。

## 10. 阶段 B 验收、试点和未实施边界

先合成故障验证，再真实单角色，再平台预编排双角色。任何一层失败保留前一层证据，不能把替身、协议 schema 或 UI 截图写成真实模型通过。

本批已实现并以合成状态验证核心路由、目的端持久保存、显式恢复、schema 迁移和公开投影；完整旧来源适配、常驻搬运、自动待办以及下面的真实单/双角色试点继续待验。验收表是完整放行要求，不是本批每行均通过的声明；具体通过项和计数只以进度记录为准。

| 验收点 | 必须证明的结果 |
| --- | --- |
| 来源幂等和事务 | 并发同 ID 同内容只有一映射/Task；异内容/目标拒绝；映射或 Task 写入后事务内失败均无半条记录 |
| 全入口排他 | 每条实际源入口、旧/新消费者竞争和恢复器都只能一方获权；未知类型留旧线或拒绝；冻结/epoch 变更不搬移已接纳对象 |
| 源确认故障 | 源领取持久化后核心提交前、核心提交后源确认前崩溃，恢复均返回同一 admission/Task；不会因重新投递重新执行 |
| 结果发送故障 | 结果与意图提交后发送前、claim 后、sending 后、目的端落盘后回执前、平台保存回执前故障，均按同 ID 查询与核对收敛 |
| 幂等回执 | 重复/乱序/迟到回执不会串目标或覆盖内容；同 ID 异摘要冲突；目的端 unavailable 保持 held；无法查回执的目的端不可放行 |
| lease/fence 与停止 | 旧领取者续租/完成/回执写入被拒；停接纳不等同 member stop；drain 不取消在飞，明确 stop 仍 fence；退出与租约分别举证 |
| 文本完整性 | 第 6 节边界样本逐层大小/摘要一致、尾段完整；超限有明确状态且不重跑模型、不伪造收到 |
| 迁移与回退 | 各支持源版本显式升级、一致备份、live 阻断、晚期失败回滚、旧程序拒新库、恢复备份不覆盖新写入；GET 零迁移 |
| 业务与来源隔离 | 同构建空载→合成 A→合成 B→恢复 A；同名角色、相同源 ID 不同 namespace、跨安装/代次记录不混用 |
| 单角色真实试点 | 专用虚构文本的保存、领取、执行中补充、停止/断线、不明恢复、结果与目的端回执完整；模型实际交互与租约/可投递分别验证 |
| 双角色真实试点 | 平台控制方预建父子任务，子结果归属、父恢复、重复回执正确；不宣称模型自主派活、原生协作桥或跨运行时 resume |

真实试点使用独立业务包、状态根、明确角色部署、固定原生版本与专用 home。只处理显式提供的虚构文本，不读业务文件或私人会话历史，不扫描认证，不迁入需要工具的现任角色。认证范围在准备好可审阅实现和合成证据后单独明确；此前仅限 3.0 验收目录的授权不延伸到本试点。Claude 当前不可用，契约设计继续，真实 Claude 和混合团队另列。

进入灰度前由运行负责人明确首个消费方、新任务范围、所有源入口、停止/回退操作和未决清单，并向对应消费方说明状态含义；本文不代发通知、不注册 bus 身份。`dev.3` 核心交付不触发旧 fleet 切流、真实库迁移或模型试点。后续接入严格遵守 [入口核对表](MASTERAGENT_3_1_ROUTING_ENTRYPOINTS.md)，最终实现和通过项单独写入 [3.1 进度](MASTERAGENT_3_1_PROGRESS.md)。
