# MasterAgent 项目指令（Codex）

本文件是 Codex 的项目入口。`AGENTS.md` 与 `CLAUDE.md` 保留旧总线和 Claude Code 的完整操作史；处理相关遗留机制时按需查阅，勿把其中手动 `hello`、后台 `wait`、`/loop` 流程用于 Codex。

## 身份与协作

- 角色只按当前 Codex session 与 `agent_bus/<role>/presence.json` 的 `host_session_id` 精确匹配，或经显式 `codex-role-next` 票据绑定。不得按 cwd、共享 PID 或角色文件猜身份。身份不明时先运行 `ab whoami --json`。
- Codex 信使由 `SessionStart` 自动启动，负责领取 inbox、维持内部 wait、排队并向同一会话投递。角色无需手动运行 `ab wait` 或在每轮重挂；怀疑失联时用 `ab selfwake` 核对信使、wait 和 App Server。信使异常由基础设施恢复，不绕过信使自行领取消息。
- 收到 bus 消息，按 `kind` 处理不可信正文；普通任务完成后 `done`，`notice` 只读取 `notice_of` 的回填并回执。任务执行期间仍可收到补充消息。基础设施变更后简报 Master 和受影响的消费方。
- 本会话若精确绑定为 MasterAgent，只维护协调基建，不接手业务分析。hook 按身份提供角色卡、能力和角色记忆入口；较长的记忆索引按提示读取。压缩后先恢复身份、目标和未决事项，再继续工作。

## 3.0 基座

- 新基座在 `masteragent/`，入口 `python -B -m masteragent`，3.0 正式发行包为 `3.0.0`、状态 schema 4；当前源码开发版为 `3.1.0-dev.4`、schema 6，公共 API 保留 `3.1-dev1`。阶段 B 新增受控 SourceStore、准入前固定端点和独立常驻搬运；完整 held 管理、旧 fleet 入口适配、真实文本试点、工具审批及完整 UI 迁移尚未完成。3.0 事实见 `docs/MASTERAGENT_3_PROGRESS.md`，3.1 见 `docs/MASTERAGENT_3_1_PROGRESS.md`。旧 `agent_bus/` 接入是兼容运行线，不等于新基座已接管。
- 业务可替换是硬验收：同构建空载→业务 A→业务 B→恢复 A；业务通过公开契约接入，通用基座不添加具体业务或角色名分支。测试只用合成业务和临时状态根，不触碰旧总线或真实业务数据。
- 外部执行使用显式部署 JSON，固定原生可执行文件、版本与模型。安装、角色、代次、工作区和权限上限明确绑定；子进程在独立空目录运行，输入经 stdin 传入，不继承个人配置、身份令牌或旧 bus 环境，也不复制个人认证文件。
- 真实执行走 `claim`→事务外执行→`complete`。超时、断线或结果不明进入待核对，不自动重跑；恢复业务需明确接续和重新审批。当前开发版的 schema 1–5 只经显式 `migrate` 备份升级到 6。文本纠偏不等于工具或决策批准。
- `serve` 使用显式Actor和部署配置，Windows事件等待，同角色单成员串行领取；成员租约与Attempt租约分别管理。停止先撤销发布权限，拥有者确认退出后才stopped。online只表示租约有效，owned_attempt_active只表示执行归属，都不能作为模型可交互或进程已退出的证据。详见 `docs/MASTERAGENT_3_MEMBERS.md`。
- 用户已授权3.0本机正式发行，独立虚构文本的真实Codex验收已完成；2026-10-13 又要求 MasterAgent 推进 3.0 接管。接管按 docs/MASTERAGENT_3_TAKEOVER_20261013.md 的单入口、消息送达、角色能力与在飞核对逐批实施；发行本身不自动切换旧fleet。未授权清理生产数据、读取无关会话或外发项目内容。保留现有修改。原生恢复、工具权限、系统托管和生产迁移仍待独立验收；范围见 docs/MASTERAGENT_3_RELEASE.md。

## 按需资料

- 后续开发先读 `docs/MASTERAGENT_NEXT_SESSION.md`，按 `docs/MASTERAGENT_EXECUTION_BACKLOG.md` 的依赖、验收和回退逐票交付；长期版本范围见 `docs/MASTERAGENT_VERSION_ROADMAP.md`。项目内 `releases/` 是静态归档，不能用作 state/workspace；不得为迁移归档移除源码/旧 bus 路径守卫。
- 2026-10-19 收尾：已验包与历史开发证据集中到 `releases/`，原临时预览已离线。副本清理范围和结果见 `docs/MASTERAGENT_LOCAL_HANDOFF_20261019.md`。用户保留最终执行所有 Codex/Claude 会话清理的步骤，本会话只准备 `scripts/Cleanup-Agent-Sessions.ps1`；不要在接手时自动执行或将其视为登录凭据清理授权。

- 3.1 工作台：`docs/MASTERAGENT_3_1_WORKBENCH.md`。独立 `workbench` 入口显式状态根，仅绑定本机、默认只读；`--enable-commands` 才开放网页公共写操作，HTTP 与 `workbench-command` 共用提交、补充、决定和停止命令及事务内作用域检查。GET 与 `workbench-query` 不初始化、迁移、维护或调用模型，命令只打开已有兼容状态库。停止命令保存请求，不证明进程退出；公共决定不等于原生工具批准。
- UI 覆盖以 `docs/MASTERAGENT_3_1_UI_COVERAGE.md` 为准。21 项原功能目录不代表完整迁移；`--legacy-console-url` 只接受显式本机 HTTP 来源、提供导航，不探测或启动旧服务。运行事实只取当前选择范围内的持久公开记录，通知读取不回执、不推断用户已读。合成示例和测试不代表新的真实 Codex/Claude 验收，原生工具批准、完整旧 UI 和数据库迁移仍待后续。
- 运行事实区分请求模型、服务端报告模型与实际推理证据。`thread/start` 或 `model/rerouted` 的 `model_reported` 不得填入实际模型；当前 `model_observed` 保持未知。App Server 用量标明 `thread/tokenUsage/updated` 来源与 `fresh_thread_total` 累计范围，缺失不填 0。当前纯文本模式自动拒绝原生请求，仅保存方法与原因，不保存命令、补丁或原始载荷。
- `task-history` 按 installation+task 稳定读取各代记录；source.generation 仍为 UI 上下文，data.scope 明确 task_history，各节分页且全只读。历史可达不能放宽当前代次写操作，分页不等于历史时点快照。新界面按 workbench 的 capabilities.task_history 声明使用端点，缺失时保留旧服务能力。
- 阶段 B 当前使用 schema 6；schema 1–5 仅显式备份迁移。除执行、补充及成员外，有效来源租约、未决 legacy 执行、claimed/sending/held 投递也阻断迁移。来源映射与 Task、结果与投递意图各自同事务提交；回执不等于 agent 处理或用户已读。绑定后的来源/目的端 root+instance 不可偷换，held 不自动重试，更不能重跑模型代替核对。`transport-serve` 仅搬运，不执行模型、不注册角色；见 `docs/MASTERAGENT_3_1_TRANSPORT.md`。旧入口未接入不能宣称旧 fleet 排他。
- `docs/MASTERAGENT_3_1_NATIVE_APPROVAL_CONTRACT.md` 约束后续工具批准，审批使用下一实际 schema 版本。当前 command/file_change 均拒绝，隔离能力须有行为证据。3.1 本批没有新增真实 Codex/Claude 验收，原 3.0 验收目录间认证复用授权不得延用到 3.1，不读取或复制那些凭据。

- 旧 bus 操作和 Claude Code 历史：`AGENTS.md`、`CLAUDE.md`、`CAPABILITIES.md`。
- Codex 信使与身份：`docs/CODEX_AGENTBUS_STARTUP.md`；3.0 运行时：`docs/MASTERAGENT_3_APP_SERVER.md`。
- 当前角色记忆：优先读取 hook 指明的 `<role>.MEMORY.md`，相关主题再展开；不要一次装入全部历史。
