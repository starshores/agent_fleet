# MasterAgent 3.0：持久协调切片使用说明

本页记录 dev.2 引入的协作能力；当前本机正式版 `3.0.0` 继续兼容其 API，运行态为 schema 4，旧状态需显式迁移。业务包仍使用 manifest `schema_version: 1` / `platform_api: 1`，两者不能混为一谈。真实验收和现行边界见 [发行说明](MASTERAGENT_3_RELEASE.md)；Codex 执行与运行期输入见 [运行时接入](MASTERAGENT_3_RUNTIME.md)、[App Server接入](MASTERAGENT_3_APP_SERVER.md) 和 [常驻成员](MASTERAGENT_3_MEMBERS.md)。

本切片在独立的 3.0 入口上增加执行所有权、租约、显式恢复、决策、父子任务和待投递事件。所有记录均归属具体业务安装与激活代次，业务角色名称不进入基座逻辑。后续正式版已完成隔离环境的真实模型纯文本验收；原生 resume、旧 fleet 通知投递与生产迁移仍未交付。

## 1. 执行所有权与故障恢复

一次任务可以有多次执行尝试，但同一时间只允许一个有效尝试。任务的 `generation` 保留创建代次，`dispatch_generation` 表示当前允许执行的代次；结果记录实际完成执行的代次。恢复任务不会改写其来源或生成一个冒充原任务的新身份。

| 操作 | 持久化行为 | 约束 |
|---|---|---|
| `claim` | 将 queued 任务变为 running，创建 attempt、序号、所有者、租约截止时间和审计事件 | 当前 Actor、角色、代次、工具授权必须匹配；事务成功提交后才返回租约 |
| `renew` | 延长该尝试的租约并追加审计事件 | 仅原所有者持原租约且未到期时可续期；新注册不会继承旧所有权 |
| `complete` | 在同一事务内写结果、任务终态、尝试终态、通知、outbox，并处理父任务回报 | 仅当前有效所有者和尝试可完成；同一结果精确重试返回原结果，内容变化报冲突 |
| `expire-attempts` | 将逾期 running 尝试记为 expired，将任务记为 needs_reconcile | 必须显式调用；不推断外部效果、不自动重跑 |
| `reconcile` | 记录原因；选择 retry 回到 queued，或 cancel 进入 cancelled | retry 必须同时声明 `--safe-to-retry`；这个声明是操作者已核实可安全重复的结论 |

Actor 和租约都是能力凭据。数据库保存令牌摘要，角色注册和 claim 返回的 JSON 包含原始凭据，需要由调用方保存。再次注册同一角色会撤销旧 Actor；角色重新绑定后不能拿新 Actor 接管旧尝试。查询 attempts/history 不返回这些原始凭据。

迟到完成不能覆盖新尝试、撤销后的身份、其他业务或其他代次的结果。能证明原始所有权的被拒回填会追加 `completion_rejected` 诊断，保留拒绝原因；不会把迟到输出当作正式结果。租约到期只是协调授权失效，不代表旧进程已经停止，也不代表外部操作没有发生。

运行中的任务或待核实任务会阻止停用、业务切换和卸载。先核实执行情况，再决定是否取消或安全重试。只读查询不会帮用户过期、续约或重试任务。

### 短文本演示与未来真实执行器

`run` / Python `run_as` 只适用于短时、无外部副作用的合成文本适配器。目前示例为 `echo` / `uppercase`。它们在一个数据库事务内调用函数并保存结果，因此不能据此声称已经实现真实模型的崩溃恢复，更不应把长时间模型调用、网络请求或 shell 进程塞入此入口。

未来真实执行器应使用已经分开的协调接口：`claim` 提交所有权 → 事务外执行 → 必要时 `renew` → `complete` 提交结果。当前 CLI 可以手工模拟这一协议，但它本身不会启动工具或模型。异常退出、回填丢失以及外部效果是否发生，仍需要执行器与恢复逻辑共同确认。

## 2. 决策：精确答复与新代次重新审批

Actor 使用 `ask-decision` 为自己的 queued、waiting 或 awaiting_decision 任务提出问题。问题按安装、请求 ID、激活代次去重，重复请求必须具有完全相同的任务、标题和截止时间。已有 pending 问题时，新请求不能绕过它。

`answer-decision` 是可信本机管理员入口，只接受 `approve` / `reject`，并要求问题当前 `revision` 与唯一 `answer-id`。并发答复只能有一个成功。相同问题、原 revision、相同选择与相同 answer-id 的重试返回原答复；复用 answer-id 提交不同内容、错版本或旧代次答复会失败。

批准只解除当前决策阻塞；存在未完成子任务时父任务仍 waiting。拒绝使目标任务 cancelled。拒绝父任务不自动取消其独立子任务；子任务可继续回报，但不能把已取消父任务恢复为 queued。

截止时间使用 Unix 秒。读 decisions 不改变状态；截止后答复会被拒绝。显式 `expire-decisions` 才将问题标为 expired，任务仍等待决策，不会自动批准。需要继续时，使用新的 request-id 和明确的新截止时间重新提问，旧 expired 问题保留为 superseded。

跨激活代次执行 `resume-task` 时，相关的 pending、expired 和已 approved 决策门槛会以新问题 ID 重新提出。旧批准保留为历史证据，不自动授权新代次。绝对截止时间不会被偷偷延长：恢复时已过期的新问题仍为 expired，需要显式重新提问。

当前尚未实现“执行到一半暂停等待审批”的运行时协议。running 任务不能直接 ask-decision；先以明确的任务拆分方式在领取执行前完成审批。

## 3. 父子任务：等待是记录，不是阻塞线程

`request-child` 由父任务所属 Actor 调用。父任务必须为 queued 或 waiting，不能处于执行中或等待决策；目标角色必须属于同一安装。新子任务和关联请求在同一事务内创建，父任务进入 waiting，不占用一个线程反复等待。

逻辑请求 ID 按安装和父任务去重，跨激活代次仍返回同一个子任务。内容或目标角色不同报冲突；确实要创建另一份工作时必须使用新的 request-id。每次创建全新的子任务身份，不引用已有任务构成循环。

所有子任务 completed、failed 或 cancelled 后，父任务才能解除子任务等待；存在未决问题时仍保持 awaiting_decision。`child-replies` 返回请求、子任务身份、终态及已有结果，不消费或删除结果。取消的子任务没有虚构的成功结果，其回报明确为 cancelled、result 为 null。

恢复父任务不会批量恢复或重跑子任务。尚未完成的旧代次子任务需逐个显式 resume。已经完成的子任务保持原结果及代次，父任务恢复只在新代次 outbox 中重建结果引用。

## 4. Outbox 与只读观察

outbox 与相应状态变化共同提交，为后续投递器提供持久事件。当前支持查看和确认，没有桌面、旧 bus、邮箱或其他外部传输实现。看到 pending 不等于已经通知对方；`ack-outbox` 只是调用方显式登记送达，不验证外部服务实际收件。

事件在安装、代次和去重键内唯一。默认只查看当前激活代次的 pending 事件；`--include-delivered` 可查看该代次的全部状态，包括迁移保留的 held。确认同一事件可安全重复，确认只记录 delivered 和时间，保留事件、任务、结果与历史。跨业务或旧代次确认会被拒绝，held 事件也不能直接确认或悄悄重放。

`status`、`view`、`context`、`results`、`notifications`、`history`、`attempts`、`decisions`、`outbox`、`child-replies` 都是只读入口。管理员显式指定的 history/attempts 可以查看该安装历史；当前业务视图不混入另一业务。Actor 接口有安装、角色、代次检查，但拥有本机数据库读取权限或管理员 Python API 的进程仍属于可信部署边界。

## 5. PowerShell：在新隔离目录模拟协议

从源码仓库根运行。下面创建新的临时演示目录，不指定旧总线、真实业务目录或宿主会话目录。每次执行都会产生不同目录。示例使用仓库内的虚构业务 A；请保存 `$Ma3DemoRoot`，以便后续观察同一演示。独立发行包也支持相同参数，将函数中的 `-m masteragent` 替换为该 `.pyz` 的完整路径即可。

```powershell
$Ma3DemoRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ma3-dev2-' + [guid]::NewGuid().ToString('N'))
$Ma3State = Join-Path $Ma3DemoRoot 'state'
$Ma3Workspace = Join-Path $Ma3DemoRoot 'workspace-a'
$Ma3ActorFile = Join-Path $Ma3DemoRoot 'reviewer-actor.json'
$Ma3LeaseFile = Join-Path $Ma3DemoRoot 'reviewer-lease.json'
New-Item -ItemType Directory -Path $Ma3Workspace | Out-Null

# 数组传入实际 CLI 参数；失败时立即停止，不把错误当成 JSON 结果继续执行。
function Invoke-Ma3 {
    param([string[]]$Ma3Arguments)
    $Ma3Output = & python -B -m masteragent --state-root $Ma3State @Ma3Arguments
    if ($LASTEXITCODE -ne 0) { throw "MasterAgent command failed: $LASTEXITCODE" }
    ($Ma3Output -join [Environment]::NewLine) | ConvertFrom-Json
}

Invoke-Ma3 @('install', 'a', '--package', '.\examples\v3\business-a.json', '--workspace', $Ma3Workspace)
Invoke-Ma3 @('activate', 'a')
$Ma3Actor = Invoke-Ma3 @('register', 'a', 'reviewer')
$Ma3Actor | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Ma3ActorFile -Encoding utf8

$Ma3Task = Invoke-Ma3 @('submit', 'a', 'reviewer', '--text', 'synthetic protocol sample', '--request-id', 'protocol-1')
$Ma3Lease = Invoke-Ma3 @('claim', '--actor-file', $Ma3ActorFile, $Ma3Task.task_id, '--tool', 'catalogue-echo', '--ttl', '300')
$Ma3Lease | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Ma3LeaseFile -Encoding utf8

# claim 成功后模拟事务外工作；这里没有调用模型或外部系统。
Invoke-Ma3 @('renew', '--actor-file', $Ma3ActorFile, $Ma3Lease.attempt_id, '--lease-file', $Ma3LeaseFile, '--ttl', '300')
Invoke-Ma3 @('complete', '--actor-file', $Ma3ActorFile, $Ma3Lease.attempt_id, '--lease-file', $Ma3LeaseFile, '--output', 'synthetic protocol sample')
Invoke-Ma3 @('attempts', 'a', '--task-id', $Ma3Task.task_id)
Invoke-Ma3 @('outbox', 'a')
```

Actor / lease 文件必须是 UTF-8 JSON。上面明确指定 `-Encoding utf8`，避免 Windows PowerShell 直接 `>` 默认写出 UTF-16。CLI 同时接受带 BOM 的 UTF-8。renew 返回更新后的尝试视图，不重新返回原 lease_token，因此保留最初 claim 的 lease 文件，不用 renew 输出覆盖它。令牌文件不应放进业务包或提交到源码仓库。

### 提问、答复与持久回报

在上述演示环境中继续：

```powershell
$Ma3ApprovalTask = Invoke-Ma3 @('submit', 'a', 'reviewer', '--text', 'review after explicit approval', '--request-id', 'approval-task-1')
$Ma3Decision = Invoke-Ma3 @('ask-decision', '--actor-file', $Ma3ActorFile, $Ma3ApprovalTask.task_id, '--title', 'Approve this synthetic review?', '--request-id', 'approval-question-1')
Invoke-Ma3 @('answer-decision', 'a', $Ma3Decision.decision_id, '--revision', [string]$Ma3Decision.revision, '--choice', 'approve', '--answer-id', 'approval-answer-1')

# 使用无副作用的演示适配器完成已批准任务。
Invoke-Ma3 @('run', 'a', $Ma3ApprovalTask.task_id, '--tool', 'catalogue-echo')
$Ma3Events = @(Invoke-Ma3 @('outbox', 'a'))

# 这里只模拟本地消费确认；它没有把消息发送给外部收件人。
foreach ($Ma3Event in $Ma3Events) {
    Invoke-Ma3 @('ack-outbox', 'a', $Ma3Event.event_id)
}
Invoke-Ma3 @('outbox', 'a', '--include-delivered')
Invoke-Ma3 @('results', 'a')
```

### 业务角色之间的父子任务

虚构业务 A 的 editor 没有工具授权，但可以管理自己的父任务并向 reviewer 请求工作。该例完成子任务后父任务恢复 queued，只展示协调，不给 editor 增加未声明的工具权限。

```powershell
$Ma3EditorFile = Join-Path $Ma3DemoRoot 'editor-actor.json'
$Ma3Editor = Invoke-Ma3 @('register', 'a', 'editor')
$Ma3Editor | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Ma3EditorFile -Encoding utf8
$Ma3Parent = Invoke-Ma3 @('submit', 'a', 'editor', '--text', 'prepare a synthetic review request', '--request-id', 'parent-1')
$Ma3Request = Invoke-Ma3 @('request-child', '--actor-file', $Ma3EditorFile, $Ma3Parent.task_id, '--to-role', 'reviewer', '--text', 'review the synthetic entry', '--request-id', 'review-child-1')
Invoke-Ma3 @('run', 'a', $Ma3Request.child_task_id, '--tool', 'catalogue-echo')
Invoke-Ma3 @('child-replies', '--actor-file', $Ma3EditorFile, $Ma3Parent.task_id)
```

### 显式恢复旧代次任务

不需要通过修改数据库恢复任务。下面为一个尚未运行的 reviewer 任务切换代次并显式接续；已有 completed 任务不会重新执行。

```powershell
$Ma3Retained = Invoke-Ma3 @('submit', 'a', 'reviewer', '--text', 'retained task', '--request-id', 'retained-1')
Invoke-Ma3 @('deactivate', 'a')
Invoke-Ma3 @('activate', 'a')
$Ma3Actor = Invoke-Ma3 @('register', 'a', 'reviewer')
$Ma3Actor | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Ma3ActorFile -Encoding utf8
Invoke-Ma3 @('resume-task', 'a', $Ma3Retained.task_id, '--reason', 'Continue this retained synthetic task in the new activation')
Invoke-Ma3 @('run', 'a', $Ma3Retained.task_id, '--tool', 'catalogue-echo')
Invoke-Ma3 @('history', 'a')
```

同一任务、目标代次和相同原因的 resume 重试返回现有状态；更换原因重新提交会报冲突。若恢复任务含决策门槛，先查看 `decisions a`，对新的 decision_id / revision 重新答复，再领取执行。若恢复父任务还有旧代次子任务，应逐个核实后对对应 child_task_id 执行 resume。

### 不确定结果的处理模板

以下是操作模板，`TASK_ID_FROM_HISTORY` 必须替换为已核实的任务 ID；不是普通成功任务的后续必做步骤。只有实际到期的尝试才会被 expire-attempts 处理，查询不会制造到期。

```powershell
python -B -m masteragent --state-root $Ma3State expire-attempts a
python -B -m masteragent --state-root $Ma3State attempts a --task-id TASK_ID_FROM_HISTORY

# 二选一：已确认可以安全重复，或明确取消；保留实际核实依据。
python -B -m masteragent --state-root $Ma3State reconcile a TASK_ID_FROM_HISTORY --resolution retry --safe-to-retry --reason 'Verified synthetic work can safely repeat'
# 或：
python -B -m masteragent --state-root $Ma3State reconcile a TASK_ID_FROM_HISTORY --resolution cancel --reason 'Do not repeat this uncertain work'
```

`--safe-to-retry` 不会自动校验外部系统，也不会停止旧进程。操作者尚不能证明可安全重复时，不应选择 retry。retry 只返回 queued，之后需要新的 claim / run，旧 attempt 与 lease 仍不能回填。

## 6. 从较早的 3.0 数据库显式迁移

dev.5支持新基座产生的 schema 1/2/3 数据库升级为 schema 4，**不是旧 agent_bus、Claude 会话或真实业务资料的迁移工具**。旧 bus 数据格式不能作为本命令输入。逐角色部署及运行期输入规则见 [App Server接入](MASTERAGENT_3_APP_SERVER.md)，成员升级见 [常驻成员](MASTERAGENT_3_MEMBERS.md)。

普通查询和写操作发现 schema 1/2/3 会返回 `migration_required`，不会自动改库。对已选定的独立 3.0 状态目录执行：

```powershell
# 替换为第一批 3.0 的独立状态目录，不要指向旧 agent_bus。
$Ma3OldState = 'C:\your-isolated-ma3-state'
python -B -m masteragent --state-root $Ma3OldState migrate
python -B -m masteragent --state-root $Ma3OldState status
python -B -m masteragent --state-root $Ma3OldState history a
```

迁移前先安排该部署停止接受新工作，并确认操作的是目标数据库。schema 2/3存在running任务或Attempt时拒绝迁移，应使用原版本先停止并核对。工具取得写入保留锁，在 `<state-root>/backups/schema<原版本>-<随机ID>.sqlite3` 创建一致性备份，然后在一个事务内变更结构与数据，验证引用和 schema 后提交。返回值给出实际 backup_path。schema 4 再次执行 migrate 返回 `migrated:false`，不会反复生成备份。

迁移规则如下：

- 原任务 ID、来源代次、结果、通知和安装快照保留；schema 1的 `dispatch_generation` 初始等于原 generation，schema 2已有历史保持不变。
- schema 1 的 running 记录缺少可验证的尝试所有权，转为 needs_reconcile 并记录 `migration_uncertain`。不伪造一个仍然有效的 lease；需显式核实后 retry / cancel。
- schema 1历史通知的实际送达情况未知，转换为 held outbox。不会默认改成 delivered，也不会重新广播。schema 2 outbox保持原状态。当前没有 held 的自动释放策略；保留历史供核实。
- 原 Actor 令牌全部撤销，必须重新 register。迁移前的 Actor 文件不能继续授权执行。
- schema 1已有结果不虚构 attempt_id；旧记录与新尝试记录的证据能力不同。schema 2已有Attempt及结果关联保留。
- schema 1/2为每个角色创建旧默认工作区绑定，访问上限none，不读取业务目录内容；新运行期输入表为空。schema 3保留原部署和输入历史，新成员表为空。

迁移失败会回滚数据库变更；已经生成的备份可能保留。回退需要明确停止该独立部署，保留当前数据库和新增历史，再选定可恢复的版本。**一旦 schema 4 已接收新任务或产生新结果，不要直接用旧版本备份覆盖当前数据库**，否则会丢失迁移后的工作与去重证据。尚未实现跨版本新旧数据合并或降级迁移，不能把“备份存在”等同于“任意时刻无损降级”。

## 7. 验证范围与下一条接入边界

隔离测试覆盖独占领取、续约、逾期阻断、迟到回填、并发结果、决策争用、重复答复、跨代次重新审批、父子关联、业务作用域、只读查询和显式迁移。测试使用临时目录与合成业务，不读取旧环境来证明新基座行为。

```powershell
python -B -m unittest tests_v3.test_coordination -v
python -B -m unittest discover -s tests_v3 -v
```

真实执行器的下一步接入必须同时满足：事务外执行、可核实的进程/会话状态、续约与终态回填、权限和业务上下文隔离，以及对未知外部效果的恢复方案。现有注册仍返回 `online:false`，不会将有注册、有历史或有租约解释为一个真实 Codex 成员已在线。
