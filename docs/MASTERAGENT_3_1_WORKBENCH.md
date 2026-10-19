# MasterAgent 3.1 dev.3 本机工作台

版本：`3.1.0-dev.3`，更新于 2026-10-19。本批增加阶段 B 的结果投递与目的端持久回执只读卡片，状态库使用 schema 5，公共 API 保留 `3.1-dev1`；3.0.0 正式发行包保持独立。既有公共操作、跨代任务链、兼容导航和运行事实继续保留，各批验收结果见 [3.1 进度](MASTERAGENT_3_1_PROGRESS.md)。

## 本批能力

工作台、任务、团队、资料、运行设置五个入口共用只读查询服务。页面可以选择业务与代次，分页查看任务，按需读取任务正文、结果、公开事件、补充信息和待确认记录。团队显示角色及成员租约事实；Codex 显示已有适配能力，登录与模型连通性为未验证；Claude 显示暂不可用、未完成真实接入。

默认仍为只读观察入口。显式开启后可保存任务、补充信息、答复平台决定和请求停止。每个主页面提供原功能目录与操作说明，逐项展示旧 UI 的 21 个入口、迁移范围及可选兼容链接；目录齐全不代表功能全部迁入。Codex 服务端模型报告、token 累计用量及原生请求拒绝记录继续可见；成果页与跨代成果新增目的端回执事实。业务工具、人工原生审批、登录、原生恢复和旧业务数据迁移仍待后续交付。schema 迁移仅在独立 CLI 显式执行，查看页面不会迁移状态。

## 本机启动

在项目根目录使用 Python 3.11 或更新版本。显式选择状态根，工作台不会自动创建、初始化或迁移该目录。

```powershell
python -B -m masteragent --state-root C:\ma-data\selected-state workbench --port 0
```

启动输出包含本次 `http://127.0.0.1:<port>` 地址。`--port 0` 选择空闲端口；省略端口时使用 8801。进程前台运行，关闭该进程即可停止工作台，不会停止成员执行器。不存在的状态根呈现未初始化空态且保持不存在。

也可以使用独立模块入口：

```powershell
python -B -m masteragent.workbench --state-root C:\ma-data\selected-state --port 0
```

工作台只绑定 `127.0.0.1`，所有页面资源随包提供。它不加载旧 `ui.server`、旧总线、个人原生历史或认证，不启动自愈、维护、worker 或模型。它面向本机可信操作者，不是多用户远程服务。

显式开启公共操作，并为旧控制台配置导航链接：

```powershell
python -B -m masteragent --state-root C:\ma-data\selected-state workbench --port 8801 --enable-commands --legacy-console-url http://127.0.0.1:8799
```

`--enable-commands` 仅对本次进程有效。HTTP 写操作要求同源、进程独立的 CSRF 凭据和有效的当前业务代次；不会初始化缺失状态根。`--legacy-console-url` 仅接受带显式端口的 `http://127.0.0.1` 或 `http://localhost` 根地址，不允许凭据、路径、查询或片段。服务不探测该地址，也不合并两边数据。未配置时兼容按钮禁用。

旧 UI 源码已支持 21 个已知页签的片段深链；运行中的旧服务未在本批重启。若旧服务仍加载旧页面，链接可能停在上次页签，需手动切换。不得将链接已配置显示为旧服务在线或迁移完成。

## 合成样例

如需独立演示，显式创建一个全新目录。创建器拒绝已存在的目标，避免覆盖任何数据。

```powershell
python -B scripts/create_workbench_demo.py --directory C:\ma-demos\workbench-dev2
python -B -m masteragent --state-root C:\ma-demos\workbench-dev2\state workbench --port 0 --enable-commands
```

样例通过公共领域接口走 A→B→A，含相同角色名、不同业务资料、历史代次、分页、待确认、成果、失败和待发送补充。所有内容及执行记录均为合成数据，没有调用 Codex 或 Claude，也没有原生会话。样例的进行中状态只用于展示平台记录；时间推进后租约可能过期，页面不能把它当作模型在线证明。

## 公共查询契约

HTTP 与 `workbench-query` CLI 共用 `WorkbenchQueries`。每个响应携带：

- `api_version: "3.1-dev1"`。
- `source`：明确的状态根、安装和所选代次；不推断业务或角色身份。
- `observed_at`：本次读取时间，UTC ISO 8601；不是底层记录修改时间。
- `revision: null`：schema 5 仍没有统一对象修订号；路由 epoch、目的端存储修订属于各自对象，不伪造全局 revision。
- `data`：资源投影；各条记录保留原有时间和状态。

| 查询 | HTTP GET | CLI resource |
| --- | --- | --- |
| 工作台模式及兼容导航配置 | `/api/v1/workbench` | 仅 HTTP |
| 部署及安装列表 | `/api/v1/deployment` | `deployment` |
| 摘要与待确认 | `/api/v1/installations/{id}/overview` | `overview` |
| 任务摘要分页 | `/api/v1/installations/{id}/tasks` | `tasks` |
| 指定任务详情 | `/api/v1/installations/{id}/tasks/{task_id}` | `task` |
| 稳定任务的跨代只读记录 | `/api/v1/installations/{id}/task-history/{task_id}` | `task-history` |
| 角色与成员 | `/api/v1/installations/{id}/team` | `team` |
| 已安装业务资料 | `/api/v1/installations/{id}/resources` | `resources` |
| 原生运行时能力 | `/api/v1/installations/{id}/connections` | `connections` |

```powershell
python -B -m masteragent --state-root C:\ma-demos\workbench-dev2\state workbench-query tasks --installation-id demo-a --generation 2 --limit 20
```

除部署列表外，查询明确指定安装。`generation` 省略时取该安装的当前代次，显式值不得超过已激活代次。任务列表支持 `state`、`role_id`、`limit`（1–100）和不透明 `cursor`；游标绑定部署、安装、代次与筛选条件。HTTP 重复或未知参数会报错，不能传入任意文件路径或切换后端状态根。

任务列表不包含任务正文、成果或补充文本。详情按需读取；Attempt、公开事件、输入、决定和通知各最多返回 100 条，并通过 `truncated` 标明截断。成员列表也有明确上限。UI 不读取通用原始协议载荷、秘密字段或私有推理内容。

详情的 `attempts[].runtime` 独立提取已持久化的运行时、请求模型、服务端报告模型、实际模型、用量、thread/turn、启动、停止请求和退出证据，不依赖时间线最后 100 条。`attempts[].operations` 给出当前补充和停止能力及阻断原因。通知只公开投递元数据，不提供载荷或回执秘密，`user_read` 始终未知。连接页的 `latest_attempt` 只代表所选代次最近记录的 Codex 执行，不证明当前登录或模型连接可用。

### 跨代任务链与能力协商

工作台配置的 `data.contract_revision` 为 `2026-10-19-routing-receipts-1`，`data.capabilities.task_history=true` 表示提供任务链端点，`data.capabilities.result_delivery=true` 表示提供结果投递投影。它们是兼容附加字段，不替代写操作守卫；旧服务缺少任务链能力声明时，新界面不请求新端点，已有页面仍可使用。历史查询保留独立对象，不改变原有 `task` 详情的代次过滤语义。

任务详情新增 `data.deliveries={items,truncated}`；跨代成果每项增加 `delivery`。查询按业务、任务、记录代次和 Attempt 关联投递，使用同一只读事务，不联机查询目的端。公开字段仅含投递标识、目的端名称、状态、原因、次数、时间及回执的标识/正文大小/摘要/存储修订/时间；不暴露租约令牌、目的端物理路径、绑定实例或完整信封。`agent_processed` 和 `user_read` 保持 null。

字段缺失表示服务未提供该能力，不能解释成没有投递；空 items 只表示当前作用域没有记录。received 只证明目的端已持久保存；held 展示核对原记录提示，不提供重跑或直接重发按钮。超限时平台成果完整保留，投递明确 rejected。管理命令与接入边界见 [持久文本通道说明](MASTERAGENT_3_1_ROUTING.md)。

`task-history` 以 installation + task 稳定定位任务，即使该任务已经续接到新代次，也能读取此前记录。`source.generation` 仍表示 UI 选定上下文，`data.scope={kind:"task_history",task_id}` 明确读取的是整条任务链。`data.task` 的状态和 `dispatch_generation` 是当前保存的任务事实，不是所查看旧代次的状态快照；整个返回为 `readonly=true`。

- `section=generations` 是默认目录，返回关联代次、是否原始代次、是否任务当前归属、是否安装当前代次；纯排队任务的续接边同样在目录中。
- 其余 `section` 为 `continuations`、`attempts`、`decisions`、`results`、`inputs`、`events`、`notifications`，必须指定 `record_generation`。续接节列出触及该代次的边，其余节列该代记录。历史 Attempt 不携带可写操作；读取历史不能批准旧决定、补发输入或停止旧执行。
- 各节支持 `limit`（1–100）与不透明 `cursor`。游标绑定状态根、安装、任务、UI 代次、记录代次和节；不能跨目标复用。分页只固定新增记录上界，不提供历史时点快照。
- `data.items`、`next_cursor`、`section`、`record_generation` 和 `scope_note` 供 UI 导航。事件和通知沿用公开字段白名单，不返回原始协议或秘密载荷。

```powershell
python -B -m masteragent --state-root C:\ma-demos\workbench-dev2\state workbench-query task-history --installation-id demo-a --task-id TASK_ID --generation 2
python -B -m masteragent --state-root C:\ma-demos\workbench-dev2\state workbench-query task-history --installation-id demo-a --task-id TASK_ID --generation 2 --section decisions --record-generation 1 --limit 20
```

### 模型、用量和原生请求事实

dev.2.1 在现有公开执行事件中增加观测，不修改状态 schema，不导入私人会话。字段证据核对本机 `codex-cli 0.163.0` 生成的 App Server schema；原生请求语义另参考 [OpenAI App Server 审批说明](https://learn.chatgpt.com/docs/app-server#approvals)。本机导出位置与后续审批设计见 [原生工具审批契约](MASTERAGENT_3_1_NATIVE_APPROVAL_CONTRACT.md)。协议字段核对不等于真实模型或工具验收。

| 字段或记录 | 当前含义与边界 |
| --- | --- |
| `model_requested` | 部署请求的模型，不是实际推理证据 |
| `model_reported`、`model_reported_source` | 服务端在 `thread/start` 报告的配置，或 `model/rerouted` 报告的改道目标；UI 同时显示来源，不将其填入实际模型 |
| `model_observed` | 实际推理模型的独立证据；当前采集器不能证明这一事实，继续为 `null` |
| `usage` | 按已收到的计数展示 `input_tokens`、`cached_input_tokens`、`cache_write_input_tokens`、`output_tokens`、`reasoning_output_tokens`、`total_tokens`；可选字段缺失显示未采集，不补 0，不用分项相加伪造总数 |
| `usage_source`、`usage_scope` | App Server 计数来自 `thread/tokenUsage/updated`；`fresh_thread_total` 表示本次新建原生会话的累计量，不是单轮增量或账户总额度 |
| `last_observation_at` | 最近一条已保存运行事实的时间，不是即时连接探测时间 |
| `runtime_observation` | 时间线中的公开模型/用量事实，使用白名单字段，不保存原始协议包 |
| `runtime_native_request_denied` | 当前纯文本模式自动拒绝原生请求的公开事件；只记录请求方法与拒绝原因，不记录命令、补丁或私有内容 |
| `native_requests_denied` | 当前执行的已保存拒绝事件数量、最近请求方法与时间；计数 0 只表示没有此类已保存事件，旧响应缺少字段时显示未记录，均不据此推断曾批准或执行工具 |

任务详情与运行设置均使用人类可读的模型和 token 字段，不输出原始 JSON 用量块。旧记录的已知字段继续可见，缺失的新字段保持未采集。当前未开放人工原生批准，平台 `answer` 不能用于批准原生工具。

[原生工具审批契约](MASTERAGENT_3_1_NATIVE_APPROVAL_CONTRACT.md) 是后续设计，审批表、原生答复和审批按钮均未实现。schema 5 已用于持久文本通道，审批须使用届时下一实际版本。业务文件工具、沙箱与授权边界需要独立实现和验证后再开放。

## 公共操作契约

HTTP 与 `workbench-command` CLI 共用 `WorkbenchCommands`，在同一领域事务内检查当前启用业务、代次和目标归属，返回与查询相同的作用域信封。API 版本保持 `3.1-dev1`，避免把产品开发版编号当作破坏性 API 变更。

HTTP 路径为 `POST /api/v1/installations/{id}/commands/{action}`，请求体只接受下列精确字段：

| action | 字段 | 成功的含义 |
| --- | --- | --- |
| `submit` | `generation, role_id, text, request_id` | 任务保存到平台队列，等待独立执行服务领取 |
| `input` | `generation, task_id, attempt_id, text, request_id` | 补充保存，不代表运行时或模型已经接收 |
| `answer` | `generation, task_id, decision_id, expected_revision, choice, answer_id` | 平台公共决定已记录；`choice` 必须为 `approve` 或 `reject` |
| `stop` | `generation, task_id, attempt_id, reason` | 停止意图已记录，由原执行服务处理，仍需退出证据 |

JSON 请求上限 512000 字节，任务/补充的 `text` 上限 64000 UTF-8 字节，停止原因上限 2000 字符，请求/答复标识上限 200 字符；拒绝重复字段、未知字段、非有限数和错误类型。CLI 从显式的 UTF-8 JSON 文件读取相同请求：

```powershell
python -B -m masteragent --state-root C:\ma-data\selected-state workbench-command --installation-id demo-a --action submit --payload-file C:\ma-data\submit.json
```

其中提交示例为 `{"generation":2,"role_id":"editor","text":"整理示例资料","request_id":"one-unique-request-id"}`。角色必须来自该业务包。相同标识与内容可安全重试；标识冲突或错误作用域会拒绝。停止请求在同一当前代次下以目标执行及原因保持精确重试回执，包括运行已退出后；不允许借重试跨代次操作。

页面草稿仅在内存中按状态根、业务、代次和目标隔离，刷新快照保留填写内容；关闭或重新加载页面不会持久保存草稿。提交期间锁定内容；结果不明时保留原标识与内容，只允许精确重试，并提示先核对已有记录。未选择决定不会默认拒绝。平台公共决定不等于 Codex/Claude 的原生工具审批。

任务保存后可打开已保存任务核对领取；角色提示使用同业务、代次的成员快照，标明更新时间。没有成员、成员忙碌或快照尚未确认都不阻止合法排队。领取证据来自 Attempt，成员关联只在 active_attempt_id 精确匹配时展示；成员可接单和模型可交互继续分开，不能把“网页可保存任务”称作执行服务已就绪。

## 隔离与状态语义

每个查询使用独立的只读 SQLite 事务，不自动回填回执、过期决定、修复成员或消费通知。过期状态可以在返回投影中体现，`stored_state` 保留持久化值；底层文件保持不变。读取继续遵守现有目录、schema 和 DELETE journal 边界，不开启 WAL。

原任务列表和详情按 `dispatch_generation` 选择当前归属。过去代次查询是对当前保存记录的过滤，不是历史时点快照。显式接续到新代次的任务归新代次；详情相关记录也限定在所选代次。`history_available` 包含续接记录；跨代链通过上面的独立只读接口访问，即使旧代详情不在当前筛选范围，也保留执行链入口。

`lease_valid`、`accepting_tasks`、执行归属、模型连通是不同事实。未知实际模型和用量保留 `null`，页面显示未采集；不以请求模型或服务端模型报告代替实际推理证据，不以零代替缺失，也不混入旧 BI 数据。

前端切换安装或代次时清除旧详情与分页，取消旧请求并校验响应作用域；任务链另校验任务、节和记录代次，迟到响应不能串入新选择。刷新同一业务时保留选择和滚动；失败时明确显示旧数据及其时效。详情展示自己的快照时间，独立于任务列表。当前通过手动刷新、网络恢复及页面重新可见时重取快照；尚未提供持久事件游标或实时推送，不能保证后台即时更新。

资料页只展示安装快照里的知识、流程和工具描述，不扫描个人文件树。本机持久结果投递已提供显式单次操作，尚无常驻自动搬运器或旧 fleet 接入；通用制品索引、完整备份恢复管理、原生工具/审批桥及 Claude 真实执行尚未交付。旧功能逐项范围见 [UI 迁移清单](MASTERAGENT_3_1_UI_COVERAGE.md)。

## 构建与验证

```powershell
python -B scripts/build_v3.py --output C:\ma-builds\masteragent-3.1.0-dev.3.pyz
python -B C:\ma-builds\masteragent-3.1.0-dev.3.pyz --state-root C:\ma-demos\workbench-dev3\state workbench --port 0
python -B -m unittest discover -s tests_v3 -v
node --test tests_v3/*.test.mjs
```

发行构建只读取平台 Python 模块和十个固定前端资源，包含每个文件的摘要。资源可以直接从 `.pyz` 读取，不需展开到部署数据目录。样例创建器是开发辅助脚本，不会因打开发行包自动运行。

自动检查使用临时合成根，覆盖读取零写入、业务与代次隔离、分页、未知值、HTTP 同源边界、写操作/精确重试、运行事实投影及独立发行包。当前批次的测试数量、浏览器记录和构建摘要统一见 [实施进度](MASTERAGENT_3_1_PROGRESS.md)，不沿用第一批结果冒充本批验收。

本批没有新增真实 Codex 模型验收，Claude 与混合团队仍未真实验收。此前仅限 3.0 验收目录的认证复用授权不适用于本批或后续 3.1 部署，不读取或复制那些凭据。后续真实验收应使用明确授权的独立环境，并与协议替身、合成数据和浏览器验收分别记载。
