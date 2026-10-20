# MasterAgent 3.1 受控本机来源与常驻搬运

更新：2026-10-20。本文记录 dev.4 实施批的使用与恢复方法；正式构建版本、SHA256 和整批验收结果以 [3.1 进度](MASTERAGENT_3_1_PROGRESS.md) 为准。本文不代表发版、真实模型验收或旧 fleet 切换授权。

本批增加一个全新的本机文本来源 `SourceStore`，把它的消息可靠接纳为平台任务，并把平台已经完成的结果送入独立 `ReceiptStore`。来源、核心状态、目的端分别提交，各自保存确认事实。**transport 不执行模型，也不读取旧 bus、私人会话或登录环境。**

首批只接受 `ma-source-v1` 的显式 `task`，走 V3 lane；旧 send/request、组分片、TODO、WebUI、Claude daemon、Codex 信使及恢复器全部留在原线。完整范围见 [入口责任矩阵](MASTERAGENT_3_1_ROUTING_ENTRYPOINTS.md)。核心支持的 `legacy` lane 不等于这些生产入口已接入。

## 1. 目录、身份和配置

每次部署必须显式给出相互分离的核心 state、来源 source、目的端 receipt 及业务 workspace；端点也不能与已经安装的 workspace 重叠。不要使用源码目录、`releases/`、个人 `.codex`/`.claude`、旧 bus checkout、网络路径、symlink/junction 或数据库硬链接。路径校验是本程序访问边界，不是操作系统沙箱。

来源库保存固定 namespace 和随机持久 instance；同一个源 ID 重启后仍指向原消息。整数 `1` 与字符串 `"1"` 是两条不同消息，布尔值不接受。任务正文保持原 UTF-8 字节，不裁剪首尾、不转换换行、不截断尾段。正文最多 64,000 字节，完整信封最多 1,048,576 字节。

`transport-config.json` 必须恰好包含以下七个文本字段；不接受未知键、重复键、模型配置或非标准 JSON 数值：

```json
{
  "route_id": "synthetic-route",
  "owner_id": "synthetic-transport",
  "source_root": "C:/ma31-transport-demo-20261020/source",
  "source_namespace": "synthetic.input",
  "receipt_root": "C:/ma31-transport-demo-20261020/receipt",
  "destination_namespace": "synthetic.output",
  "destination_id": "results"
}
```

`owner_id` 是搬运租约拥有者标识，不是 bus 角色；不得用它推断当前会话身份。接入多个进程时给每个进程明确的 owner，实际领取仍由核心 lease/epoch/token 决定。配置文件不能改变已经绑定的端点实例。

初始化必须分别显式执行 `source-init` 和 `receipt-init`。`transport-bind`、`transport-once`、`transport-serve` 只打开已有兼容库，不初始化端点、不迁移核心库。当前 CLI 使用 ReceiptStore 默认上限：结果正文 512,000 字节、完整信封 1,048,576 字节；更小的自定义目的端限额不能靠这份七字段 CLI 配置隐式覆盖。

绑定时核心核对当前 route epoch、namespace、协议、字节上限和 root/instance，然后永久保存到 `route_endpoints`。route 已经有 admission 时不能事后补造端点来源证明；应为新的受控来源建立新 namespace 和路由。同样的绑定可以重放，不能换根、换实例、换目标角色或目的端。绑定后每次接纳和投递仍复核原端点；预检通过不代表已经收到结果。

## 2. 可重复的合成操作

以下流程只创建虚构输入并使用已有的确定性 echo 适配器，不登录、不调用真实模型。请从项目根运行，并选一个全新的项目外目录；若示例目录已经存在，改用新的专用目录，不删除或复用其中内容。PowerShell 示例显式写入 UTF-8。

```powershell
Set-Location -LiteralPath 'C:/apphome/agent_fleet'
$maPython = 'C:/path/to/python.exe'
$maDemo = 'C:/ma31-transport-demo-20261020'
if (Test-Path -LiteralPath $maDemo) { throw 'Select a new dedicated demo directory.' }
New-Item -ItemType Directory -Path "$maDemo/control", "$maDemo/workspace" | Out-Null
$maUtf8 = [System.Text.UTF8Encoding]::new($false)
function Write-MaDemoJson($name, $value) {
    [System.IO.File]::WriteAllText("$maDemo/control/$name", ($value | ConvertTo-Json -Depth 12), $maUtf8)
}
function Invoke-Ma {
    & $maPython -B -m masteragent @args
    if ($LASTEXITCODE -ne 0) { throw 'MasterAgent command failed; inspect its JSON error before continuing.' }
}

Invoke-Ma --state-root "$maDemo/state" install demo --package examples/v3/business-a.json --workspace "$maDemo/workspace"
Invoke-Ma --state-root "$maDemo/state" activate demo
Invoke-Ma source-init --source-root "$maDemo/source" --source-namespace synthetic.input
Invoke-Ma receipt-init --receipt-root "$maDemo/receipt" --destination-namespace synthetic.output --destination-id results

Write-MaDemoJson 'route.json' @{
    route_id='synthetic-route'; source_namespace='synthetic.input'; owner_lane='v3'
    installation_id='demo'; role_id='reviewer'; generation=1
    destination_namespace='synthetic.output'; destination_id='results'
}
Invoke-Ma --state-root "$maDemo/state" routing-command --action configure --payload-file "$maDemo/control/route.json"

Write-MaDemoJson 'transport-config.json' @{
    route_id='synthetic-route'; owner_id='synthetic-transport'
    source_root="$maDemo/source"; source_namespace='synthetic.input'
    receipt_root="$maDemo/receipt"; destination_namespace='synthetic.output'; destination_id='results'
}
Invoke-Ma --state-root "$maDemo/state" transport-bind --transport-config "$maDemo/control/transport-config.json" --expected-epoch 1
```

上例中的 epoch 1 仅适用于刚刚新建的路由。已有路由先用 `routing-query routes` 读取当前 epoch，不能用猜测值覆盖。绑定不递增 route epoch；以后修改控制开关会递增。

发布输入文件只接受 `{source_id, text, kind?}`，kind 缺省为 `task`。目标由绑定路由决定，不能放在正文或自造 JSON 键中。来源查询不确认、不领取、不迁移；默认列出前 100 个未结束搬运的对象，`--limit` 范围为 1～1000；指定 `--source-id-file` 可精确查询已结束或未结束的原对象，该文件内容是 JSON 标量，例如 `"sample-001"`，不是带 source_id 属性的对象。

```powershell
Write-MaDemoJson 'message.json' @{
    source_id='sample-001'; kind='task'; text="这是虚构的合成输入 🙂`r`n尾段 SENTINEL"
}
Write-MaDemoJson 'source-id.json' 'sample-001'
Invoke-Ma source-publish --source-root "$maDemo/source" --source-namespace synthetic.input --input-file "$maDemo/control/message.json"
Invoke-Ma source-query --source-root "$maDemo/source" --source-namespace synthetic.input
Invoke-Ma --state-root "$maDemo/state" transport-once --transport-config "$maDemo/control/transport-config.json"
Invoke-Ma source-query --source-root "$maDemo/source" --source-namespace synthetic.input --source-id-file "$maDemo/control/source-id.json"
```

`transport-once` 各检查一批来源和结果，返回 `sources`、`deliveries`、`errors`；应同时检查 errors 及每条 delivery 的 state。命令成功退出不等于所有来源已经完成，更不等于所有结果 received。来源批次每次最多 100 条；较大队列交给常驻模式连续搬运，或逐次运行并检查 pending。

此时应只有一个 admission 和一个 queued Task，没有 Attempt。搬运结束只是确认平台已接纳，随后由独立执行器处理。下面只运行本项目已有的 `catalogue-echo` 合成适配器，再发送结果：

```powershell
$maAdmissions = @(Invoke-Ma --state-root "$maDemo/state" routing-query admissions --route-id synthetic-route | ConvertFrom-Json)
if ($maAdmissions.Count -ne 1) { throw 'This fresh demo must contain exactly one admission.' }
$maTask = $maAdmissions[0].task_id
Invoke-Ma --state-root "$maDemo/state" run demo $maTask --tool catalogue-echo
Invoke-Ma --state-root "$maDemo/state" transport-once --transport-config "$maDemo/control/transport-config.json"
Invoke-Ma --state-root "$maDemo/state" routing-query deliveries --installation-id demo
Invoke-Ma --state-root "$maDemo/state" workbench-query task --installation-id demo --task-id $maTask --generation 1
```

最终核对 admission 与 source 的同一来源 ID、Task/Attempt、结果、delivery、receipt 链。delivery 的 `received` 只表示目的端完整持久保存；`agent_processed` 和 `user_read` 保持未知。对同一 input 文件重新 publish 只返回原来源记录，不创建新工作；改同 ID 的正文或类型会冲突，不能拿这种改写修复投递。

需要持续搬运时，保持独立执行器按自己的授权运行，再启动：

```powershell
Invoke-Ma --state-root "$maDemo/state" transport-serve --transport-config "$maDemo/control/transport-config.json" --max-seconds 60
```

省略 `--max-seconds` 则前台持续运行，Ctrl-C 请求停止。常驻模式使用独立的来源泵和结果泵；启动先检查 backlog，空闲先设置通知再查库再等待。Windows 目录事件只表示需要重新查询，不是领取或送达证据。两个泵各用自己的租约续期；一个来源 IO 停滞不应使结果泵停止服务。源/结果搬运和模型执行是三个不同拥有者，不共用一个 lease。工作台启动不会自动启动任何泵。

常驻退出摘要的 `sources` / `deliveries` 是已处理步骤计数，后者可能包含转入 held 的记录，不代表收到的数量；送达事实始终查询原 delivery 的 `state` 与 receipt。摘要只保留最近 100 项错误，完整核对责任以三库原记录为准。

## 3. 三个存储域如何恢复

来源域是 `sources.sqlite3`，平台域是 `state.sqlite3`，目的端域是 `receipts.sqlite3`。以下顺序不能合并成一个“跨库原子事务”：

1. 平台 `claim_source` 后，以来源原 ID 和信封调用 `admit_source`，同事务保存 admission 与唯一 Task。
2. SourceStore `confirm` 把原信封绑定到该 admission；它仍在 pending 恢复集合里。
3. 平台 `confirm_source` 记录源端已确认的事实。
4. SourceStore `complete` 结束搬运责任，之后普通 pending 查询不再列出；原对象仍可按 typed ID 查询。

| 中断位置 | 重启后的正确动作 |
| --- | --- |
| 接纳前 | 同一来源仍待搬运，重新取得 source lease 后接纳，不换 ID |
| 核心接纳后、源确认前 | 查回原 admission 和 Task，继续源端确认；不能再次 submit |
| 源确认后、平台确认前 | 源对象仍可枚举，核对同一 admission 后补平台确认 |
| 平台确认后、源 complete 前 | 继续核对同一对象并结束搬运；不能把 source_confirmed 当整个流程已经结束 |
| 已 complete | 核对原 admission 已确认；不能仅凭来源一侧标记编造平台事实 |
| namespace、root、instance、内容摘要冲突 | 停止该对象，检查原实例和原记录；不建立新 ID、改映射或丢弃冲突 |

正常结束会释放 source lease 并保留递增的 claim epoch。进程强杀留下的 source lease 到期前，另一个搬运者可能得到 busy；到期允许续接搬运，不意味着模型执行可以重跑。运行期间切换 route epoch 只影响尚未接纳的消息；原 admission 保留原 lane、Task 和目标。首批 transport 不替 legacy 消费者接单，切到 legacy 不会自动启动旧消费者。

结果流程独立：平台完成事务同时写 Result 和 delivery 意图，发送者领取后保存 sending，随后在核心事务外调用目的端 receive，最后校验并保存 receipt。来源 complete 不等于执行完成；执行完成不等于目的端收到。不能因为投递失败、transport 重启或来源确认失败重新运行已经完成的模型。

## 4. 停止、冻结与 held

Ctrl-C 或 `max-seconds` 到期只请求当前 transport 停止新动作并唤醒等待线程。返回前等待两个泵退出；已经开始的本机 IO 不能由 stop 回滚，可能延长退出时间。不要把“已发停止请求”写成进程已经退出，也不要用同名 PID 猜测退出。遇到强杀或中断，重新打开原三库核对；不清空账本。

系统停止通知失败时仍尝试唤醒另一通道，并等两个线程关闭资源；最终明确报告 `transport_stop_failed`。原生等待每段最多 30 秒，只用于备用检查停止标志，不触发数据库查询或文件扫描；正常情况由目录/停止事件立即唤醒。

长期暂停还需显式修改路由的独立开关。先读取当前 epoch，再将需要改变的布尔字段写入 JSON：

```powershell
Invoke-Ma --state-root "$maDemo/state" routing-query routes
# expected_epoch 必须替换为刚读到的当前值。
Write-MaDemoJson 'freeze.json' @{
    route_id='synthetic-route'; expected_epoch=1
    admission_open=$false; claim_open=$false; delivery_open=$false
}
Invoke-Ma --state-root "$maDemo/state" routing-command --action controls --payload-file "$maDemo/control/freeze.json"
```

`admission_open=false` 冻结新来源接纳；`claim_open=false` 禁止新执行领取；`delivery_open=false` 禁止新结果发送。已接纳来源仍可核对确认，合法在飞执行仍可按原拥有权完成，已 sending/held 的原回执仍可核对。开关不是 member stop，也不是 OS 进程退出证明。

transport 不自动核对 held，不自动 `retry_absent`。结果发送中断后，可能已经在目的端保存而平台还不知道，此时需要同一原目的端的权威证据：

```powershell
Invoke-Ma --state-root "$maDemo/state" routing-query deliveries --installation-id demo
# 选取实际记录的 delivery_id；下面占位符必须替换。
Invoke-Ma --state-root "$maDemo/state" routing-query delivery --delivery-id '<delivery_id>'
Invoke-Ma --state-root "$maDemo/state" receipt-query '<delivery_id>' --receipt-root "$maDemo/receipt"
Invoke-Ma --state-root "$maDemo/state" delivery-reconcile '<delivery_id>' --receipt-root "$maDemo/receipt"
```

`receipt-query` 是只读查询所选目的端；它的结果不更新核心，也不授权重发。`delivery-reconcile` 会写核心核对证据：查得匹配 receipt 可收敛为 received；查得原端权威 absent 默认继续 held；不可用、冲突或替换实例均不能认作未收到。sending 且租约仍有效时应让原拥有者完成，CLI 会拒绝抢先核对。

只有管理员明确选择原 ID 重试时才使用：

```powershell
Invoke-Ma --state-root "$maDemo/state" delivery-reconcile '<delivery_id>' --receipt-root "$maDemo/receipt" --retry-absent
```

该操作仍会重新核对原端的权威 absent 和当前策略，满足后仅使原 delivery 返回 pending，不立即发送。先检查返回 state；若路由允许，后续 transport 或显式 delivery-send 继续发送原字节。默认发送间隔至少 5 秒，最多进入 sending 三次。超限或证据不足仍 held；不能换根、重建同名空库、改 delivery ID 或重跑 Task。

## 5. B-07 管理能力评估：仍未完成

现有只读与 CLI 能支持受控来源的小规模人工核对，但不满足 B-07 的完整管理闭环。依据是当前 `routing_commands.py`、`workbench_query.py`、`workbench_commands.py` 和 `deliveries.py` 的实际接口：

| 已有能力 | 具体范围与缺口 |
| --- | --- |
| `routing-query deliveries --installation-id` / `delivery --delivery-id` | 只读显示状态、原因、revision、尝试次数、原任务/来源/目的端绑定与回执；隐藏租约摘要和正文信封。没有 server-side 的 held 专用筛选、稳定分页、核对动作列表或逐项核对事件投影；不能把本地命令过滤输出称为已实现共享管理端点 |
| `source-query` | 只读查未结束的来源或精确 typed ID；可看到源确认、admission 和搬运结束。CLI pending 最多 1000 条，没有可供用户遍历全量的游标参数；不承担全 fleet 巡检 |
| 工作台 task / task-history 的结果页 | 按 installation/task/generation 展示有限 delivery 卡片，包括 state/reason/send_count 和回执摘要；只读、隐藏端点 root 与令牌。没有独立的受控来源恢复视图、全局 held 队列、核对可用操作或 delivery revision 写前条件 |
| `receipt-query` / `delivery-reconcile` | 可在显式本机原目的端核对收到、absent、冲突及不可用；内部用 revision 防止查回执期间的并发覆盖；不增加 Attempt，原 delivery ID 保留 |
| 核对证据 | `delivery_evidence` / 路由事件记录 lookup 和 retry 授权事实。CLI 没有共享的操作者、幂等请求 ID、用户理由和客户端 expected revision 决定协议；反复相同命令可能追加核对事件或在状态改变后拒绝，不能声称是同一决定的幂等重放 |
| 工作台写入口 | 当前只有 submit/input/answer/stop；尚无与 CLI 共用的 delivery 核对/重试领域命令，也没有相应 HTTP/浏览器行为证据 |

**B-07 状态应保留“未完成”或“进行中”，不能因有一条 reconcile CLI 就标已完成。** 下一最小交付应先固定有作用域、可分页的待核对投影与允许动作，再设计带幂等请求 ID、expected revision、操作者与理由的公共核对命令，并接到 CLI/工作台同一领域层。HTTP/浏览器需覆盖重复决定、旧 revision、迟到响应和 GET 不写；可暂不开放网页写入口，但不能删去这些后续验收责任。

## 6. schema 6 升级与回退

本批核心新增持久 `route_endpoints`，schema 从 5 升到 6。SourceStore 自身是独立 schema 1，ReceiptStore 仍是独立 schema 1；它们不是核心库中的另两个表。新建合成 state 使用当前源码结构；已有核心 schema 1～5 只经显式 `migrate` 升级，打开页面、查询或启动 transport 不会升级。

旧版本应先停止新接纳/领取，核对并停止执行与成员。迁移前守卫包括：running Task/Attempt、未决运行中补充、未停止成员；schema 5 还阻止有效 source lease、running/held legacy 执行，以及 claimed/sending/held 投递。租约过期不能替代 legacy 执行或投递的核对；不通过手改状态绕过迁移守卫。

```powershell
# 仅在选定的独立部署已通过上述停止和核对后执行。
Invoke-Ma --state-root "$maDemo/state" migrate
```

迁移验证源版本精确结构，在写锁保护下保存一致备份，再事务提交 schema、迁移记录及必要凭据失效；返回 backup_path。Actor token 会撤销，之后需要按该独立部署重新取得平台 actor，不能据此注册旧 bus 或复用私人原生登录。schema 5 的旧 admission、结果与目的端绑定原样保留，不补造来源端点绑定；有历史 admission 的旧路由不能直接用于本批 transport。

迁移失败应保留原库和备份证据；不要覆盖新库或修改 user_version。回退使用原版本程序和迁移前备份，在另一个经过路径守卫的专用恢复根验证。旧程序不能打开 schema 6；归档内数据库不能在项目 `releases/` 下运行。若备份带有固定端点 root/instance，恢复本身不授权复制端点或开启双实例；在任何重新接单之前核对原 source/receipt 和旧新进程退出状态，防止两个平台同时消费同一来源。

## 7. 验证边界与后续入口

本批测试全部限于合成业务、临时根、确定性适配器或进程替身。本文编写时，CLI 集成测试 `test_transport_commands.py` 12 项和既有 `test_routing_cli.py` 5 项已实际通过；其余核心、迁移、常驻及故障测试的最终运行结果由主集成记录到进度，不根据测试文件存在推断通过。

真实 Codex 文本试点、Claude/混合团队、旧 fleet 全入口接管、原生工具与审批、系统服务托管均未由本批 CLI 或本文验证。B-09 必须先确认本次独立专用运行范围和认证方式，3.0 验收目录间认证复用的旧授权不能沿用。下一会话从 [执行工单](MASTERAGENT_EXECUTION_BACKLOG.md) 读取当前未完成项；遇到 held 先核对原对象，不用新任务掩盖旧的不确定结果。

### 限定 V3 入口签收依据

下表只覆盖本批 SourceStore → V3 → ReceiptStore；旧 fleet 入口全部留旧线。测试最终结果以本版 `evidence/python-tests-final.log` 为准。代码审查与独立测试互补，不能将表中 Python 子进程当作真实 Codex/Claude 推理。

| 参与入口 | 共同守卫与证据 |
| --- | --- |
| 管理/Actor 同步执行、独立领取 | `run/run_as → _execute → _claim`、`claim → _claim`；`test_routing.test_freeze_claim_blocks_all_direct_execution_entries_but_not_plain_tasks` 验证冻结时不能增加 Attempt |
| 常驻成员领取 | `claim_member_task → _claim`，Attempt 与 member 归属同事务；`test_routing.test_member_skips_frozen_routed_task_and_claims_plain_task`、`test_members.test_claim_transaction_rollback_cannot_leave_unattached_running_attempt` |
| 执行器派发 | `MemberWorker → CodexRunner.run_claimed → renew/_owned_attempt → _run`；`test_worker_cli.test_claimed_runner_rejects_invalid_lease_before_probe_or_launch`、`test_codex_exec.test_initial_lease_loss_and_cancellation_do_not_launch`。协议子进程与替身不等于真实模型 |
| 两个独立进程竞争同一来源任务 | `test_transport.test_two_executor_processes_start_only_one_synthetic_child_for_same_source`：双方各开核心库、就绪后争同一 Task；只有 claim 胜者启动真实 Python 合成子进程，败者为 `task_not_queued`，启动记录/Attempt/Result/Delivery 各 1。使用公共 claim/complete，不是两套真实 CodexRunner |
| 完成发布 | `complete → _owned_attempt → _finish → _create_result_delivery`；`test_deliveries.test_result_intent_same_transaction_rolls_back_even_after_delivery_insert`、`test_routing.test_frozen_claim_does_not_cancel_inflight_attempt_or_republish_it` 覆盖原子性和幂等 |
| 停止、失联、恢复与接续 | member fence、`expire_attempts/fence_uncertain` 后原执行进入核对；`test_worker_process.test_terminated_member_process_expires_without_automatic_task_retry`、`test_attempts.test_late_old_completion_cannot_overwrite_new_attempt_or_result`，迟到拥有者不能覆盖结果 |
| 投递恢复 | 固定 receiver root+instance，`test_deliveries.test_endpoint_commit_lost_response_recovers_without_second_receive_or_model` 与 `test_transport_adversarial.test_result_commit_response_loss_recovers_without_second_receive_or_execution`；这里的执行次数是持久 Attempt/合成启动证据 |

本批没有发现参与的 V3 入口绕过共同领取/完成守卫。完整 held 管理仍由 B-07 承担；真实版本、原生输入投递与模型调用计数分别留给 B-09～B-11，不能以本表替代。
