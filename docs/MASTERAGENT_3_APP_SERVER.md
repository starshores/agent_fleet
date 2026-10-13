# MasterAgent 3.0：角色部署与 App Server 文本执行

dev.4 引入逐角色部署绑定和运行期补充信息，接入 V3 的 Task/Attempt/Lease/outbox。当前本机正式版为 `3.0.0`、状态 schema 4；业务包仍为 `schema_version: 1` / `platform_api: 1`。本页保留开发阶段的验证叙述，真实验收和现行限制见 [发行说明](MASTERAGENT_3_RELEASE.md)。旧 `codex-exec` 模式继续可用，持续调度入口及迁移规则见 [成员生命周期](MASTERAGENT_3_MEMBERS.md)。

当前 App Server 模式每个 attempt 拥有一个独立的 stdio 子进程和新的 native thread，通过 RPC 执行文本任务、接收 `turn/steer`、处理中断并回填结果。它不连接桌面私人 App Server，不恢复旧线程，不注册旧 bus 角色，也不是常驻团队调度服务。

## 业务定义和机器部署

角色名称、指令、知识和通用能力仍由业务包声明。工作目录、权限上限和运行时配置属于部署，不能写入业务包，也不会进入模型提示词。

`install` 保留必需的 `--workspace` 默认根，并增加 `--role-deployments <JSON>`。文件内容示例：

```json
{
  "reviewer": {"workspace": "C:/YOUR-DEPLOYMENT/review", "access": "read-only"},
  "editor": {"workspace": "C:/YOUR-DEPLOYMENT/edit", "access": "read-write"}
}
```

映射只能命名该业务包的角色，每项必须恰好含 `workspace` 和 `access`。未覆盖角色使用默认根、`access: "none"`。目录必须预先存在；拒绝相对路径、链接、平台状态/源码/宿主运行时目录，以及跨安装的相同或嵌套目录。同一安装可显式共享相同根，角色根彼此不能嵌套。默认根可作为所有显式角色目录的容器，但它本身不授予访问权。

绑定随安装保存且不可原地修改。更换路径须建立新的 installation，保留原历史，避免旧任务的工作归属被重新解释。`context` 仅返回所选角色的 `workspace` 和 `workspace_access`；`view` 提供管理员部署映射。激活和使用时重新检查路径；运行时 home 必须避开所有安装的默认根和角色根，包括停用或卸载后保留的绑定。

`access` 是未来文件适配器的授权上限。当前两种执行器均为纯文本，`business_file_tools: false`；填写 `read-write` 不会启用 shell、文件修改或自动将业务目录设为模型 cwd。每次执行仍使用独立空目录。

## 选择 App Server

沿用运行时 JSON 的全部显式字段，仅将 `runtime` 改为 `codex-app-server`。可执行文件、完整版本、模型、installation、role、generation、专用 `codex_home`、超时和租约仍必须指定。配置 schema 仍为 1，不接受任意命令或环境扩展。

`runtime-check` 检查固定 native 程序的版本和 `app-server --help`，不会启动服务、检查认证或调用模型。开发时本机静态检查的版本为 `codex-cli 0.162.0-alpha.2`；协议形状来自同一固定版本的本机生成 schema，不能据此声称将来版本兼容。

该版本 App Server 没有 exec 的 `--ignore-user-config`，因此专用 home 中出现 `config.toml`、`hooks.json`、`AGENTS.md`、`.agents` 或 `skills` 时会拒绝运行，检查只读元数据。私有执行目录继续拒绝项目配置发现，关闭宿主技能、hooks、插件、MCP、浏览器及文件工具。原生认证仅由用户在明确的专用目录中管理；平台不读取或复制认证内容。配置边界检查不等于 OS 沙箱实证。

仍使用 `runtime-prepare` 绑定专用目录和 `execute-task` 执行任务。模型执行先提交 claim，再在数据库事务之外启动 owned process；租约续期、其他任务提交、Decision 答复和纠偏写入可以并行进行。

## 执行期间补充信息

在另一个本地调用中对当前 attempt 提交文字：

```powershell
python -B -m masteragent --state-root C:/YOUR-STATE steer-attempt a CURRENT-ATTEMPT-ID --text '使用更正后的标签 SAMPLE-002' --request-id correction-001
python -B -m masteragent --state-root C:/YOUR-STATE runtime-inputs a --attempt-id CURRENT-ATTEMPT-ID
```

这些是对所选状态根的本地管理命令，不能把目录访问当作远程鉴权机制。正文持久保存在 `attempt_inputs`；outbox 和运行事件只保存引用、顺序号与状态。只有明确声明 `supports_runtime_inputs: true` 的当前执行器可接收新输入。

同一 request ID、相同 attempt 和正文可幂等重试；换正文或目标会冲突。输入按顺序从 `pending` 原子领取为 `sending`，再带精确 `threadId` 和 `expectedTurnId` 发送。只有匹配的 native 回执可标为 `accepted`；它表示执行器确认接收，不表示权限批准或模型已经正确采纳。

发送结果不明、失去租约、所有者撤销或执行结束时的未确认输入保留为 `held`，不能自动重新领取，也不能跳过它投递后面的输入。普通 outbox ack 不会改变输入接收状态。完成事务检查所有输入；若完成前刚收到新的补充信息，尚未确认的输入会阻止发布成功结果，执行进入 `needs_reconcile`。

本批不把原生工具请求接入可批准操作：已识别的审批、交互和工具请求会明确拒绝，未知请求返回“不支持该方法”的协议错误。作用域不符、损坏消息或实际工具执行事件会停止当前执行。已有 V3 Decision 继续处理原有排队/等待任务的 approve/reject；自由文本不能代替 Decision 批准。运行中的交互求助和原生工具审批桥接仍是后续工作。

## 完成、取消与恢复

App Server 的 `turn/completed` 与进程退出是两件事。只有本次 RPC 关联的精确 thread/turn、合法最终文本、无未确认纠偏，且 owned process tree 已确认停止，才可提交成功。受控关闭的实际退出码保留；非零成功必须证明控制器实际终止了进程，不能将自然非零退出算作成功，也不把退出码改写为零。

`cancel-attempt` 继续提交停止请求，由持有进程的监督器发送精确 `turn/interrupt` 并在界限内关闭自己拥有的进程树。超时、断线、协议异常和不明终态进入 `needs_reconcile`，不会重连后自动重做任务；迟到事件不能越过租约或代次保护。原生历史与旧执行目录保留。

## 显式升级

旧状态必须显式运行 `migrate`；普通读写不会自动升级。dev.5支持schema 1/2/3先备份再事务升级到4。schema 2/3存在 running task 或 attempt 时拒绝迁移，须用前一版本先停止并核对执行。

schema 1/2迁移为已有包的每个角色建立旧默认目录绑定，`access: "none"`；不移动或读取业务目录内容。schema 3已有部署与输入状态保留。结果、Decision、outbox 和协调历史保留，Actor token 撤销，需重新登记。schema 1 保留原先“未知旧执行待核对、旧通知 held”的迁移规则。成员会话表初始为空，不导入私人会话或旧 bus。

升级后不能用 dev.4 打开 schema 4；若新版本已产生工作，不能直接覆盖回旧数据库备份。回退应先停止接纳和核对在飞结果，保留新状态与原生历史。

## 验证边界

本批使用合成业务、临时状态、假 RPC 事件与本机模拟子进程验证，包括相同构建的 A→B→A。没有启动真实 Codex App Server、读取私人会话、调用真实模型、迁移实际状态或重启现有服务。实际模型、连续团队在线、native resume、文件能力、运行期人工审批和生产迁移不能计作完成。
