# Codex 接入现状与待分析问题

日期：2026-10-09  
用途：为下周 Astra 可用后进行一轮系统分析，汇总近期 Codex 接入旧 Agent Bus、记忆加载、wait 协作和运营视图相关的修改与核查结果。  
范围：记录已有实现和观测，不代表 Codex 运行链路已完成生产验收；本记录不启动新开发。

## 一、先区分两条工作线

当前仓库同时包含两条容易混淆的工作线：

1. `masteragent/`、`python -B -m masteragent` 是隔离的 MasterAgent 3.0 新基座，按自身开发约定使用合成业务和临时根。
2. `.codex/`、`agent_bus/`、`ui/` 的本轮 Codex 接入工作，是让 Codex 会话能接入现有文件型 Agent Bus 和它的运营界面。

Codex 接入旧 Bus 不等于 3.0 已替换旧服务，也不等于 3.0 的真实 Codex 执行验收完成。3.0 当前进展和认证/真实调用限制见 [MASTERAGENT_3_PROGRESS.md](MASTERAGENT_3_PROGRESS.md) 与 [ASTRA_PLATFORM_REVIEW.md](ASTRA_PLATFORM_REVIEW.md)。

## 二、已实施或已有代码的接入点

### 1. 身份按 Codex session 精确绑定

- Presence / wait 注册表和相关识别、状态、锁活性路径加入 `host_kind`、`host_session_id` 等宿主通用字段；旧 Claude PID 字段保留作兼容。
- Codex 线程身份必须与 presence 中的 session id 精确匹配。共享 Codex 进程可以承载多个线程，因此 PID 不能作为角色身份兜底，也不能按工作目录猜角色。
- 新 Codex 线程通过一次性 `codex-role-next --role ... --cwd ...` 明确选角色；匹配到项目 cwd 的 SessionStart hook 消费票据、报到并启动 bridge。没有票据的线程保持未绑定。
- 相关实现：`agent_bus/bus/codex_startup.py`、`agent_bus/bus/identity.py`、`agent_bus/bus/presence.py`、`agent_bus/bus/selfwake.py`、`.codex/hooks/agentbus_codex_session_start.py`。

### 2. 全局与项目级 Codex 配置分层

- 全局规则物理源：`host_config/CODEX.global.md`；用户入口应为 `C:\Users\<用户>\.codex\AGENTS.md`。
- 用户级 hooks 物理源：`host_config/CODEX.hooks.json`；启动配置中登记了 SessionStart 角色/记忆上下文和相关事件钩子。用户已在 Codex 中信任对应 hook，用户配置记录了 trust state。
- 项目级配置：`.codex/config.toml`，承载项目专属的 MasterAgent compact 恢复和 agent-bus 启动 bridge。
- 用户此前检查发现 `~/.codex/AGENTS.md` 是 HardLink，而全局规则约定期望 SoftLink；本轮未修复。Astra 应核对全局规则来源、链接类型和实际读取路径，避免出现多份物理副本漂移。
- 相关说明：`host_config/README.md`、`docs/CODEX_AGENTBUS_STARTUP.md`。

### 3. Codex SessionStart 角色记忆注入

- 已实现按精确 session 身份取得角色，不从 cwd 或文件名推断身份。
- 记忆解析优先 `<role>.MEMORY.md`；缺少时回退该角色工作区的 `MEMORY.md` 路由索引。另可发现 `memory/roles/<精确角色名>-*.md` 提炼记忆：单条可注入，多条提供为任务按需读取入口。
- MasterAgent compact hook 按 Codex `host_session_id` 精确匹配后重注入职责卡和能力边界；未知 session 不获得 MasterAgent 身份。
- 对“Codex 是否有完整 Claude 记忆模块”的结论应拆开回答：当前实现覆盖身份绑定、启动/恢复上下文入口和按需记忆发现；并未证明 Claude 记忆生命周期的全部行为都迁移完成。记忆的自动写入、整理/合并、废弃治理及 Codex 侧等价的 Read 轨迹验收仍需单独审计。
- 角色记忆核查暴露一个具体覆盖差异：业务项目有 `memory/roles.INDEX.md` 指向 `memory/role_anchor_doc_mgmt.md`，但该锚位于 memory 根目录，不符合 hook 的 `memory/roles/<role>-*.md` 自动发现路径；且没有 `示例成员3.MEMORY.md`。因此不能仅凭 SessionStart 有载入项目 `MEMORY.md` 就认定示例成员3角色锚已自动加载。
- 相关代码：`.codex/hooks/agentbus_codex_global_context.py`、`.codex/hooks/agentbus_codex_session_context.py`、`.codex/hooks/reinject_masteragent_context.py`。

### 4. Wait bridge 与收件顺序

- `.codex/codex_wait_bridge.py` 以独立 bridge 维护一个已登记 wait；启动时先领取当前 inbox backlog、确认新 wait 已登记，再提交模型回合。
- 执行期间另一个 wait pump 继续收取后续消息并排队，当前批次串行完成后再交给 Codex。代码还处理 wait 返回消息与重新挂 wait 之间的竞态，将已领取消息缓冲后再进入工作回合。
- 收到 `notice` 按 notice 协议只读取 `notice_of` 指向的 done 结果并回执，不作为普通任务执行。
- 需谨慎区分：桥接器代码设计为自动重挂，不代表每条用户粘贴/手工触发的 bus 事件都由该 bridge 实际投递。近期 `ab done` 提示当前没有 armed wait，而用户看到消息又称桥接器将重挂；需在 Astra 分析时核对 bridge PID 生命周期、wait.reg 与实际 Codex 回合的关联，解释这类状态不一致。
- 相关说明与代码：`docs/CODEX_AGENTBUS_STARTUP.md`、`.codex/codex_wait_bridge.py`、`.codex/hooks/agentbus_codex_session_start.py`。

### 5. 运营资源与任务协作视图

- 已接入 Codex 用量/桥接状态相关的 UI 和 API 代码，改动涉及 `ui/codex_usage.py`、`ui/api.py`、`ui/common.py`、`ui/page_js_bi.py` 等；既有完成通知称代码接入后需重启 bridge/WebUI 或新开会话才生效。
- 任务协作页增加全角色回报汇总：默认聚合活跃角色最近 200 条 done，支持按收件角色过滤，汇总视图限制清除范围。通知回报记载 WebUI 8799 重启返回 HTTP 200；只做过 AST 静态检查，未做人工端到端界面验收。
- Astra 应核验 UI 中 Codex 用量数据的来源、缺失值口径、任务和 token 成本是否重复/漏算、过滤范围与更新时间；不能把“页面可打开”当作数据正确性证明。

## 三、近期核查结果及证据边界

- 当前 Codex 会话的 `ab whoami --json` 通过 session_id 精确匹配为 MasterAgent；这是本会话身份结果，不是其他 Codex 会话的证明。
- 旧 Bus 的 wait 曾在 Codex host 下登记为 listening，且做过 `selfwake` / wait 注册核验；这证明 Bus 看见注册状态，不单独证明每次消息都能自动唤起正确 Codex turn。
- 已有模拟/静态核查覆盖角色身份匹配和未知身份不授予角色、钩子 TOML / Python 结构等。新 Codex 会话启动、resume、compact 的真实端到端记忆注入仍需由干净线程验收。
- 用户已信任 SessionStart hook；trust state 解决的是 hook 获准运行，不是其在新线程运行成功的证据。
- WebUI 8799 有过启动成功和 HTTP 200 的记录；不等于全部 UI 数据或交互验收完成。
- 近期 bus notice 曾因“claimed 后无 turn 活动”重新入队，说明会话回合交付/回执链仍应作为重点观测对象，而不是只检查 inbox/done 文件。

## 四、Astra 下一轮建议审查的问题

1. **身份与启动**：startup/resume/compact/clear 各自的角色选择和记忆注入是否一致；无票据、过期票据、重复 session 绑定、共享 Codex 进程多线程时是否安全。
2. **记忆模块覆盖**：逐项对照 Claude 现有记忆层级（全局规则、项目规则、角色卡/角色索引、主题索引、记忆正文、启动必读、compact 恢复、读轨迹/boot-check、记忆写入与整理），标记 Codex 已实现、部分实现、缺失和不适用；特别核实示例成员3锚及其他业务角色路径。
3. **桥接器存活性**：SessionStart async hook 的退出/取消语义、wait pump 是否在模型工作期间真在听、消息到达与重挂竞态、Codex turn 失败/中断/超时后队列如何恢复、notice 重投与幂等。
4. **身份与 bus 观测一致性**：比较 presence、wait.reg、process、bridge 当前 turn、inbox/processing/done 的事实，定位 `ab done` 显示 unarmed 与桥接器宣称会重挂的成因。
5. **运营/成本数据**：Codex 事件与 Claude 历史数据是否分命名空间、token 用量/成本字段如何映射、任务归属/过滤是否正确、轮询或刷新是否影响观测完整性。
6. **全局存储原则**：HardLink/SoftLink 预期和实际是否统一；全局规则、hooks、项目配置的物理源是否单一且升级不覆盖用户配置。
7. **验证分层**：逐项注明静态检查、模拟事件、真实 Codex 会话行为、真实模型调用、UI 人工验收；不得将较低层级证据升级表述成生产级通过。

## 五、分析时应保持的范围

- 本记录只汇总 Codex 接入旧 Agent Bus 的近期改动和查询。后续由 Astra 做架构分析时，应将它与 MasterAgent 3.0 隔离开发状态并列审查，不能因旧 Bus 桥接可用就假定 3.0 的部署和迁移已完成。
- 本轮没有修改业务仓、没有启动真实模型调用、没有验证真实 Codex 登录/计费，也没有做生产迁移或完整 UI 人工验收。
- 当前主工作树有多处既有修改与未跟踪文件；本记录只作本机交接资料，不代表这些改动已提交或已发布。
