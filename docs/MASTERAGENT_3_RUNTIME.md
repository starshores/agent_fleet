# MasterAgent 3.0：独立文本运行时使用说明

本文记录 dev.3 引入、后续继续支持的单次 `codex-exec` 模式；文中的早期验证描述保留历史语境。当前本机正式版为 `3.0.0`、平台状态 schema 4；业务包契约仍为 `schema_version: 1` / `platform_api: 1`。真实 App Server 验收和现行限制见 [发行说明](MASTERAGENT_3_RELEASE.md)；逐角色部署与运行期纠偏见 [App Server接入](MASTERAGENT_3_APP_SERVER.md)，常驻调度见 [成员生命周期](MASTERAGENT_3_MEMBERS.md)。

本批实现把任务交给独立 Codex 进程执行，并将结果接回已有租约和持久协调机制。当前模式是 **一次任务、一次进程、纯文本输入与输出**。业务文件访问、原生会话续接、运行中审批和常驻在线成员仍未实现。本轮验证使用合成业务与替身进程；原生命令只检查版本、帮助与独立目录的登录状态，未发起真实模型任务。

## 1. 业务能力与部署运行时分开

业务包声明角色、知识、任务流程和通用能力。例如 `business-a-text.json` 与 `business-b-text.json` 都使用 `agent-text` 适配器，各自保留自己的角色说明和知识。业务包不写 Codex 路径、模型、凭据或原生会话身份。更换业务不需要给基座添加角色名或业务名分支。

部署配置独立选择 `codex-exec`、本机原生可执行文件、预期版本、模型、安装、角色与激活代次。`--runtime-config` 向当前平台实例登记外部能力；平台普通 `run` 不执行这个能力，必须经过 `execute-task`。合成 `echo` / `uppercase` 仍使用原来的短文本入口。

执行顺序为：校验配置、Actor、任务和角色授权 → 检查原生版本与接口 → 提交 `claim` → 在数据库事务之外启动进程并续租 → 校验事件和退出状态 → 提交 `complete`。模型运行不会持有一个长时间数据库写事务。

## 2. 显式部署配置

下面是完整字段示例。可执行文件和模型是需要操作者填写的占位值；路径必须指向真实存在的本机原生程序和专用目录。`expected_version` 必须与所选程序的完整版本输出精确一致；本轮实际观察到的版本为 `codex-cli 0.162.0-alpha.2`，这不代表自动跟随将来版本。

```json
{
  "schema_version": 1,
  "runtime": "codex-exec",
  "executable": "C:/YOUR-CODEX-INSTALLATION/codex.exe",
  "expected_version": "codex-cli 0.162.0-alpha.2",
  "model": "YOUR-EXPLICIT-MODEL-ID",
  "adapter_id": "agent-text",
  "installation_id": "a",
  "role_id": "reviewer",
  "generation": 1,
  "codex_home": "C:/YOUR-NEW-DEPLOYMENT/runtime-a-reviewer-g1",
  "timeout_seconds": 120,
  "lease_seconds": 30
}
```

所有字段均必填；未知字段、重复字段、凭据扩展字段、非有限数字和错误类型都会被拒绝。`generation` 是正整数；`timeout_seconds` 范围为 1–3600，`lease_seconds` 为 5–300。Windows 只接受显式绝对 `.exe` 路径，不接受 `.cmd` / `.bat` 包装、相对路径或文件链接。配置和能力文件支持 UTF-8，包括 UTF-8 BOM。

`codex_home` 必须由操作者提前创建，并与平台状态、所有业务工作目录、源码、旧总线和宿主已有运行时目录分离。首次绑定前只能为空，或包含已单独登录生成的普通 `auth.json`。平台只检查该文件的元数据，不打开或复制其内容。符号链接、junction 和相关文件硬链接会被拒绝。

`runtime-prepare` 创建 `.masteragent-runtime.json`，只绑定状态根、安装、角色和激活代次。相同绑定可重复检查；不同角色、业务或新激活代次不能复用这个运行时目录。A→B→A 时，即使安装 ID 相同，恢复后的 A 也要使用新代次配置和新运行时目录。旧原生历史保留在旧目录。

## 3. 执行目录与上下文边界

每次尝试在绑定目录下新建空的 `execution/<attempt_id>`，同时作为进程工作目录和 Codex `-C` 目录。已有目录不会被清理后复用。业务工作目录只保留在平台安装记录里，当前文本模式不会把它作为模型的执行目录。

标准输入只包含当前业务包、当前角色说明、该包知识、获准能力描述和任务文本。提示词不进入进程参数。执行参数固定启用 JSON 事件、只读沙箱请求、忽略用户配置、禁用项目说明读取，并关闭 shell、浏览器、MCP、插件、hooks、记忆等扩展入口。事件层只接收文本消息和必要生命周期信息；出现工具、命令、文件修改等项目会按协议异常处理。此检查不声称能撤销工具已经发生的效果。

此外，每次执行显式将该绝对目录标为 `untrusted`，设置 `project_root_markers=[]`，并在个人主目录边界以下只检查元数据，拒绝执行目录及祖先中的 `.codex`、`.agents` 和 `.git`。官方文档说明，空项目根标记列表会停止向父目录寻找项目；未信任项目会跳过项目配置、hooks 和规则。[项目根检测](https://learn.chatgpt.com/docs/config-file/config-advanced)、[信任与配置层](https://learn.chatgpt.com/docs/config-file/config-basic)

个人主目录是单独的配置作用域。自定义 `CODEX_HOME` 不能单独证明 `$HOME/.agents/skills` 不会被发现，因此执行参数同时禁用宿主技能发现；不会为了检查业务隔离而读取个人技能或历史。[技能发现位置](https://learn.chatgpt.com/docs/build-skills)

子进程环境只允许 `SystemRoot`、`WINDIR`、`ComSpec`、`PATH`、`PATHEXT`、`TEMP`、`TMP`、`HOME`、`USERPROFILE`、`APPDATA`、`LOCALAPPDATA`、`LANG`，另行设置明确的 `CODEX_HOME`。API 密钥变量、宿主线程身份、旧总线变量和 Python/Node 注入变量不继承。这里保留的是操作系统必要路径，不能称为完全没有宿主环境。

这些措施限制配置和上下文的意外耦合，**不等于已经验证操作系统级文件或网络隔离**。运行时仍是本机进程；管理员、系统和组织策略继续适用，不使用绕过沙箱或忽略组织规则的参数。

## 4. PowerShell：建立新的演示部署

从 3.0 源码仓库根运行，使用已经安装的 Python。先创建三个互相独立的新目录；示例不使用旧总线、真实业务或宿主会话目录。

```powershell
$Ma3DemoRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ma3-dev3-' + [guid]::NewGuid().ToString('N'))
$Ma3State = Join-Path $Ma3DemoRoot 'state'
$Ma3Workspace = Join-Path $Ma3DemoRoot 'workspace-a'
$Ma3RuntimeHome = Join-Path $Ma3DemoRoot 'runtime-a-reviewer-g1'
$Ma3ConfigFile = Join-Path $Ma3DemoRoot 'runtime.json'
$Ma3ActorFile = Join-Path $Ma3DemoRoot 'reviewer-actor.json'
New-Item -ItemType Directory -Path $Ma3State, $Ma3Workspace, $Ma3RuntimeHome | Out-Null
```

将前一节配置另存为 `$Ma3ConfigFile`，填写所选原生程序、精确版本、可用模型和 `$Ma3RuntimeHome` 的实际绝对路径。首次安装预置 `generation: 1`，表示预期的第一次激活；激活后必须核对返回值。不要把部署配置或 Actor 文件放进业务包。

```powershell
function Invoke-Ma3Runtime {
    param([string[]]$Ma3Arguments)
    $Ma3Output = & python -B -m masteragent --state-root $Ma3State --runtime-config $Ma3ConfigFile @Ma3Arguments
    if ($LASTEXITCODE -ne 0) { throw "MasterAgent command failed: $LASTEXITCODE" }
    ($Ma3Output -join [Environment]::NewLine) | ConvertFrom-Json
}

Invoke-Ma3Runtime @('install', 'a', '--package', '.\examples\v3\business-a-text.json', '--workspace', $Ma3Workspace)
$Ma3Activation = Invoke-Ma3Runtime @('activate', 'a')
$Ma3Config = Get-Content -LiteralPath $Ma3ConfigFile -Raw | ConvertFrom-Json
if ($Ma3Activation.generation -ne $Ma3Config.generation) { throw 'Configuration must match the actual activation generation.' }
Invoke-Ma3Runtime @('runtime-check')
Invoke-Ma3Runtime @('runtime-prepare')
```

`runtime-check` 只调用所选程序的 `--version` 和 `exec --help`；它不创建平台状态、不请求模型，也不验证登录。成功结果明确包含 `authentication: "not_checked"`、`model_requested` 和 `model_observed: null`。版本匹配不等于账号能够使用所填模型。`runtime-prepare` 绑定目录，也不负责登录。

之后由用户在独立终端中，通过明确指向这个专用目录的原生 Codex 登录流程完成认证。不要从个人默认运行时复制 `auth.json`，不要把认证内容粘贴进配置、业务包、任务或仓库。平台代码不读取认证内容；真正执行时由原生 Codex 使用该独立目录。登录流程与本批无模型验证分开执行。

完成独立登录后，以下 `execute-task` 才会真实启动模型任务；前面的安装和注册都不会：

```powershell
$Ma3Actor = Invoke-Ma3Runtime @('register', 'a', 'reviewer')
$Ma3Actor | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Ma3ActorFile -Encoding utf8
$Ma3Task = Invoke-Ma3Runtime @('submit', 'a', 'reviewer', '--text', 'Review this synthetic entry: item label SAMPLE-001.', '--request-id', 'text-demo-1')
Invoke-Ma3Runtime @('execute-task', $Ma3Task.task_id, '--actor-file', $Ma3ActorFile, '--tool', 'review-text')
Invoke-Ma3Runtime @('attempts', 'a', '--task-id', $Ma3Task.task_id)
Invoke-Ma3Runtime @('history', 'a')
```

Actor 文件包含能力凭据，不能提交到源码仓库。注册返回 `runtime: null` / `online: false`；注册成功只代表身份签发，不代表常驻进程已上线。重复注册会撤销旧 Actor；不能借新 Actor 接管仍在执行的旧尝试。独立 `.pyz` 支持相同参数，将 `-m masteragent` 替换为开发包的绝对路径即可。

## 5. 停止、失败与不确定结果

运行期间可以在另一个终端使用同一状态根，先从 `attempts` 获取当前尝试 ID，再提交停止请求：

```powershell
# 将值替换为 attempts 返回的当前 attempt_id。
$Ma3AttemptId = 'CURRENT-ATTEMPT-ID'
Invoke-Ma3Runtime @('cancel-attempt', 'a', $Ma3AttemptId, '--reason', 'User requested stop')
Invoke-Ma3Runtime @('attempts', 'a', '--task-id', $Ma3Task.task_id)
```

`cancel-attempt` 只持久化请求，由当前拥有进程的监督器观察并停止它。返回 `stop_requested: true`、`termination_confirmed: false` 不代表已经终止；不会根据历史 PID 直接杀进程。同一原因重复请求可复用，冲突原因会被拒绝。监督器退出后，旧请求不会自动创建新的监督器。

| 执行证据 | 平台处理 |
|---|---|
| 合法最终文本、完成事件、退出码 0，且确认进程树结束 | 提交 completed 结果和对应持久事件 |
| 尚未启动子进程就明确失败，或收到权威失败终态且确认终止 | 提交 failed；保留尝试记录 |
| 超时、取消、租约失效、协议格式错误、缺少终态、输出超限 | 隔离本次尝试，任务进入 needs_reconcile；不发布成功结果 |
| 先报告完成、后以非零码退出 | 视为不确定中断，进入 needs_reconcile |
| 记录失败或无法确认最终持久状态 | 返回 unconfirmed 等明确不确定信息，不冒充成功落库 |

进程协议按 JSONL 逐行校验生命周期、大小和数量；原始 stderr、推理文本和错误载荷不会直接写进协调审计。记录的是受限进度、原生 thread ID、用量、退出证据及获准的最终文本。官方 JSON 事件接口是适配依据；本实现只支持其中的纯文本子集。[非交互执行与 JSON 事件](https://learn.chatgpt.com/docs/non-interactive-mode)

`needs_reconcile` 不会自动重跑。操作者核实后，沿用 `reconcile --resolution retry --safe-to-retry --reason ...` 或取消的明确流程；租约到期、进程终止和逻辑取消都不能证明已经发生的外部效果被撤销。监督器异常退出后也仍需显式过期与核对流程，不能直接重复领取。

## 6. 当前能力和验证范围

本模式提供单次执行、受限事件观察、租约续期、停止请求、原生 thread ID 关联，以及专用 `CODEX_HOME` 中的原生历史。没有使用 `--ephemeral`，但基座当前不会调用原生 `resume`，也不会重新导入个人或旧业务会话。每次新尝试启动新线程。

本 `codex-exec` 模式不提供运行中插话、执行中交互审批、常驻团队成员、自动唤醒、业务文件工具或外部通知投递。dev.4 的 `codex-app-server` 模式提供运行期文字纠偏，详见文首链接。已有持久审批和父子任务仍在平台协调层使用，不能据此宣称 Codex 单次进程已能在执行中暂停等待这些动作。

自动验证覆盖替身协议、进程退出和取消、租约丢失、隔离路径、配置拒绝、CLI 路由与业务切换。原生检查确认了所选版本和接口，并在新的专用目录中观察到未登录状态；没有把缺少认证的结果当成真实模型成功。测试统计、构建校验和原生检查证据以本批进度记录为准。
