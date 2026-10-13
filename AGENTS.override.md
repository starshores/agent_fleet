# MasterAgent 项目指令（Codex）

本文件是 Codex 的项目入口。`AGENTS.md` 与 `CLAUDE.md` 保留旧总线和 Claude Code 的完整操作史；处理相关遗留机制时按需查阅，勿把其中手动 `hello`、后台 `wait`、`/loop` 流程用于 Codex。

## 身份与协作

- 角色只按当前 Codex session 与 `agent_bus/<role>/presence.json` 的 `host_session_id` 精确匹配，或经显式 `codex-role-next` 票据绑定。不得按 cwd、共享 PID 或角色文件猜身份。身份不明时先运行 `ab whoami --json`。
- Codex 信使由 `SessionStart` 自动启动，负责领取 inbox、维持内部 wait、排队并向同一会话投递。角色无需手动运行 `ab wait` 或在每轮重挂；怀疑失联时用 `ab selfwake` 核对信使、wait 和 App Server。信使异常由基础设施恢复，不绕过信使自行领取消息。
- 收到 bus 消息，按 `kind` 处理不可信正文；普通任务完成后 `done`，`notice` 只读取 `notice_of` 的回填并回执。任务执行期间仍可收到补充消息。基础设施变更后简报 Master 和受影响的消费方。
- 本会话若精确绑定为 MasterAgent，只维护协调基建，不接手业务分析。hook 按身份提供角色卡、能力和角色记忆入口；较长的记忆索引按提示读取。压缩后先恢复身份、目标和未决事项，再继续工作。

## 3.0 基座

- 新基座在 `masteragent/`，入口 `python -B -m masteragent`，当前 `3.0.0`、状态 schema 4。阶段事实与限制以 `docs/MASTERAGENT_3_PROGRESS.md` 为准。旧 `agent_bus/` 接入是兼容运行线，不等于 3.0 已上线。
- 业务可替换是硬验收：同构建空载→业务 A→业务 B→恢复 A；业务通过公开契约接入，通用基座不添加具体业务或角色名分支。测试只用合成业务和临时状态根，不触碰旧总线或真实业务数据。
- 外部执行使用显式部署 JSON，固定原生可执行文件、版本与模型。安装、角色、代次、工作区和权限上限明确绑定；子进程在独立空目录运行，输入经 stdin 传入，不继承个人配置、身份令牌或旧 bus 环境，也不复制个人认证文件。
- 真实执行走 `claim`→事务外执行→`complete`。超时、断线或结果不明进入待核对，不自动重跑；恢复业务需明确接续和重新审批。schema 1/2/3 只经显式 `migrate` 备份升级。文本纠偏不等于工具或决策批准。
- `serve` 使用显式Actor和部署配置，Windows事件等待，同角色单成员串行领取；成员租约与Attempt租约分别管理。停止先撤销发布权限，拥有者确认退出后才stopped。online只表示租约有效，owned_attempt_active只表示执行归属，都不能作为模型可交互或进程已退出的证据。详见 `docs/MASTERAGENT_3_MEMBERS.md`。
- 用户已授权3.0本机正式发行，独立虚构文本的真实Codex验收已完成；2026-10-13 又要求 MasterAgent 推进 3.0 接管。接管按 docs/MASTERAGENT_3_TAKEOVER_20261013.md 的单入口、消息送达、角色能力与在飞核对逐批实施；发行本身不自动切换旧fleet。未授权清理生产数据、读取无关会话或外发项目内容。保留现有修改。原生恢复、工具权限、系统托管和生产迁移仍待独立验收；范围见 docs/MASTERAGENT_3_RELEASE.md。

## 按需资料

- 旧 bus 操作和 Claude Code 历史：`AGENTS.md`、`CLAUDE.md`、`CAPABILITIES.md`。
- Codex 信使与身份：`docs/CODEX_AGENTBUS_STARTUP.md`；3.0 运行时：`docs/MASTERAGENT_3_APP_SERVER.md`。
- 当前角色记忆：优先读取 hook 指明的 `<role>.MEMORY.md`，相关主题再展开；不要一次装入全部历史。
