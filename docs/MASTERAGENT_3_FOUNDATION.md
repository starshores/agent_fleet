# MasterAgent 3.0：业务可替换基础切片

历史说明：本页记录 dev.3 的业务分离基础及当时的验证边界。当前本机正式版为 `3.0.0`、状态 schema 4；真实验收和现行限制以 [发行说明](MASTERAGENT_3_RELEASE.md) 为准。现有旧 bus 尚未切换。Python 3.11+，只依赖标准库。执行租约、持久决策、父子任务、通知队列见 [公共协作核心](MASTERAGENT_3_COORDINATION.md)；外部 Codex 单次文本执行见 [运行时接入](MASTERAGENT_3_RUNTIME.md)。

## 已实现的边界

| 区域 | 内容与约束 |
|---|---|
| 平台发行物 | `masteragent/` 中通用代码；不包含旧配置、业务角色表、原生会话或认证文件 |
| 业务包 | 显式选择的UTF-8 JSON；角色、知识、工具声明、工作流描述；严格版本/类型/重复键/引用校验，不合并旧默认值 |
| 部署绑定 | 安装ID、固定业务包快照与摘要、显式工作目录；保存在独立状态数据库的安装记录中 |
| 运行态 | 安装、激活代次、注册、身份令牌、任务、执行租约、决策、子任务关联、结果、通知；不得回写业务包或基座代码 |
| 观察入口 | status/view/context/results/notifications/history/attempts/decisions/outbox/child-replies；只读打开，不建目录、初始化表或启维护任务 |

运行状态必须显式指定 `--state-root`。代码根、旧总线目录、宿主 `.claude/.codex`、驱动器根、用户根、网络路径以及链接别名不能作为部署状态根；新状态目录须为空，已有数据库须具备本平台标识。状态、业务工作目录及各安装工作目录互不重叠，已有硬链接数据库被拒绝。此切片仅接受SQLite回滚日志模式，WAL数据库明确报错，防止只读查询创建附属文件。

旧 `AGENTBUS_*` 变量不参与3.0配置。未安装业务可正常查看空载状态；指定包不存在、JSON损坏、版本不兼容、定义冲突或所需工具适配器不可用时明确失败，不退回旧业务。

## 试跑两个虚构业务

从仓库根运行以下PowerShell命令。例子只在新建临时目录写状态，A/B分别为虚构目录编辑与工作坊业务；都包含 `reviewer`，但知识、工具和流程声明不同。没有Master或培训师。

```powershell
$Ma3Demo = Join-Path ([System.IO.Path]::GetTempPath()) ('ma3-demo-' + [guid]::NewGuid().ToString('N'))
$Ma3State = Join-Path $Ma3Demo 'state'
$Ma3WorkA = Join-Path $Ma3Demo 'workspace-a'
$Ma3WorkB = Join-Path $Ma3Demo 'workspace-b'
New-Item -ItemType Directory -Path $Ma3WorkA, $Ma3WorkB | Out-Null

# 空载观察；这一步不会创建state目录。
python -B -m masteragent --state-root $Ma3State status
python -B -m masteragent validate-package .\examples\v3\business-a.json

python -B -m masteragent --state-root $Ma3State install a --package .\examples\v3\business-a.json --workspace $Ma3WorkA
python -B -m masteragent --state-root $Ma3State activate a
python -B -m masteragent --state-root $Ma3State context a reviewer
$Ma3TaskA = python -B -m masteragent --state-root $Ma3State submit a reviewer --text 'alpha sample' --request-id a-first | ConvertFrom-Json
python -B -m masteragent --state-root $Ma3State run a $Ma3TaskA.task_id --tool catalogue-echo
python -B -m masteragent --state-root $Ma3State notifications a

python -B -m masteragent --state-root $Ma3State deactivate a
python -B -m masteragent --state-root $Ma3State install b --package .\examples\v3\business-b.json --workspace $Ma3WorkB
python -B -m masteragent --state-root $Ma3State activate b
python -B -m masteragent --state-root $Ma3State context b reviewer
$Ma3TaskB = python -B -m masteragent --state-root $Ma3State submit b reviewer --text 'beta sample' --request-id b-first | ConvertFrom-Json
python -B -m masteragent --state-root $Ma3State run b $Ma3TaskB.task_id --tool workshop-uppercase
python -B -m masteragent --state-root $Ma3State results b

python -B -m masteragent --state-root $Ma3State deactivate b
python -B -m masteragent --state-root $Ma3State activate a
python -B -m masteragent --state-root $Ma3State history a
```

`run`目前只运行确定性的文本测试适配器，不启动AI模型。通知是带业务归属的持久记录，不向旧bus、桌面或外部系统发送消息。注册返回 `online:false`，不会把存在一条注册记录包装成在线成员。

## 安装、停用与恢复

- 同时只允许一个活跃安装。先停用A才能激活B，每次重新激活增加代次，旧身份令牌失效。
- 业务包安装为不可变快照；修改原JSON不会悄悄改变已安装业务。不同版本需要新安装ID，当前不提供原地升级或复用已卸载ID。
- 活跃视图只返回当前业务/代次的上下文、结果与通知；`history <id>`和`view <id>`是可信本机管理员明确选定的历史查询。
- 待执行任务在停用后保留。恢复A时旧代次任务不能自动执行，返回 `stale_task`。`resume-task`必须明确给出原因；保留原任务ID和来源代次，记录接续到当前代次。未完成任务的旧审批需要重新提出和回答；终态任务不会重跑。执行中或待核对任务会阻止停用。
- `uninstall`要求先停用，只改变安装状态，保留任务、结果、注册历史与包快照。工作目录也不被删除。
- 重复请求ID且内容相同返回原任务；内容不同报冲突。同任务重复执行返回已有终态，失败任务不会自动重跑。

## 业务扩展和权限边界

业务manifest中 `schema_version` 与 `platform_api` 都为整数1。包名、角色ID、知识键、工具ID与工作流ID使用安全小写标识；显示名和正文可自由使用中文。例子展示完整格式。未知字段被拒绝，所以本机路径、认证值、原生session与运行态应留在部署/运行时边界。校验器不假装能从任意正文自动识别所有秘密。

工具声明中的adapter名称是公开扩展点。基座默认提供 `echo`、`uppercase` 两个纯文本测试适配器；通过Python API的 `Platform(..., adapters={"reverse": lambda text: text[::-1]})` 可注册另一种无副作用文本适配器，无需修改基座。业务包只引用名称；安装与激活都校验可用性，不自动导入脚本或执行shell。当前扩展是可信本机代码，仅适用于短时、无外部副作用的文本函数；真实工具、连接授权和外部副作用必须等执行器边界就绪后接入。

管理员CLI拥有显式查看历史、安装和派发的权限。worker只拿控制器签发的 `Actor`，通过 `context_for`、`task_for`、`claim/renew/complete`访问所属安装和角色；纯文本测试也可用`run_as`。伪造安装/角色/代次、使用撤销令牌、索取别人的任务或未授予工具都会被拒绝。不要把可调用管理员Python API或可直接读数据库的同机进程当成不可信隔离进程。

这证明的是平台接口作用域及受控文件路径边界，不是Codex或操作系统沙箱。dev.3外部执行器可记录原生thread ID并在专用目录保留Codex历史，但尚不支持原生恢复，真实模型调用也待专用登录验证。当前未开放业务文件工具或任意命令执行，没有声称验证过真实模型的跨业务访问拒绝。工作流字段目前为可替换业务声明，未实现通用工作流引擎。

## 验证、打包与回退

```powershell
python -B -m unittest discover -s tests_v3 -v
python -B scripts/build_v3.py --output C:\your-output\masteragent-3.0-dev.3.pyz
python -B C:\your-output\masteragent-3.0-dev.3.pyz --state-root C:\your-data\ma3 status
```

构建脚本仅打包 `masteragent/*.py`、入口和文件摘要清单，同一源码产生相同字节。它不扫描旧配置、业务资料或历史；`.pyz`可以在独立目录运行。业务包和部署状态不进入发行物。

新旧入口独立。本切片回退为停止使用3.0入口，保留其独立状态与历史；无需恢复旧bus数据或清理任何宿主目录。dev.2使用schema 2，第一批dev入口不能直接打开；升级备份和回退约束见公共协作核心说明。不可将整个源码仓库复制包误称为干净的3.0发行物。
