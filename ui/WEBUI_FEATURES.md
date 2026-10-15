# Bus 控制台 WebUI — 功能文档(MasterUI 维护)

> 用户 2026-07-14 指名 MasterUI 维护本文档:现有面板 + 系统托盘件都进文档。
> **数据口径**(在岗/在跑/静默/休眠/卡claim 等定义)归 **Master数分**;**渲染/机制/文档** 归 **MasterUI**。要调口径找Master数分 对齐。
> 单文件实现:`ui/server.py`(`ThreadingHTTPServer` @ 127.0.0.1:8799)。纯本机,不外发(符合"数据不出本机"红线)。

## 架构与实时机制
- **唯一进程**:`server.py`,pythonw 后台常驻(WMI 拉起,无黑窗)。`start_ui.ps1` / `stop_ui.ps1` 起停。
- **实时刷新(零请求扫描)**:后台 `start_fleet_refresher(20s)` 单线程跑【子进程】全进程扫描 → 刷 `_fleet_cache`;所有请求线程**只读缓存秒回**,绝不在请求路径现扫(避免 GIL 饿死)。健康徽标同样后台子进程刷 `_health_cache`。
- **前端刷新**:SSE `/api/events` 推变更版本 → 前端按可见 tab 拉对应 API(`showTab` 门控,只刷当前 tab 的重数据)。`morphInto`(morphdom)增量更新,focus-safe(跳过 activeElement)。
- **新增 tab 必同步 4 处**:navbar 按钮 / `TAB_NAMES` / `showTab` 分支 / `<section data-tab>`。漏 `TAB_NAMES` → showTab 守卫静默弹回 fleet(死代码)。见记忆 `webui-new-tab-4-syncpoints`。

## 顶栏
- **健康徽标**(点开面板):自醒在岗/待确认/掉线、队列 in/proc/held/failed、消息丢失(孤儿/卡死/久滞)、daemon 期望/在跑、WebUI owner pid/陈旧实例、Edge 调试锁。源 `/api/health`(缓存)。

## Tabs(TAB_NAMES 顺序)
| key | 标题 | 用途 | 主 API |
|-----|------|------|--------|
| `fleet` | 🛰 舰队/运维 | 精确会话身份、Codex 信使投递态与当前会话工作态；运行模型、角色默认模型、默认思考强度分列显示，后两列可编辑；队列计数；Codex 共享宿主 PID 与角色工作区分开显示 | `/api/state`；POST `/api/role-model-prefs` |
| `bi` | 📊 运营/资源 | Claude usage.db 与 Codex rollout token 分源；“工作中会话”按精确 Codex session 的本机 App Server 状态，“Bus 在跑任务”按托盘新鲜 claim 口径；Codex 共享 App Server 进程树只计一次，资源不能按角色拆分 | `/api/bi` |
| `usage` | 📈 历史用量 | Claude usage.db 与 Codex rollout 分开展示，不估 Codex 美元成本 | `/api/usage` |
| `caps` | 🧬 能力池 | 能力池(谁被授权哪些 cap·登记归培训师) | `/api/caps` |
| `ctx` | 🧠 上下文 | Claude 的 65% 自动压缩预警与 Codex 的上报窗口占用分开；Codex 阈值未知不套用 Claude 告警 | `/api/ctx` `/api/ctxpanel` |
| `prompts` | 📝 提示词审核 | 提示词审核(hello/Claude 提示词 + Codex 全局源与宿主入口、项目指令、MasterAgent 角色记忆、hooks 配置；按精确 presence 展示 startup/resume、compact 角色卡、compact 项目 CLAUDE.md/记忆三类实际注入预览;每节**行号 gutter**;真实文件节带 **📂开文件** 按钮→默认程序打开) | `/api/prompts` + POST `/api/open-file` |
| `tasklines` | 🎯 任务线看板 | 长期任务线卡片 + 待决策徽标 | `/api/tasklines` |
| `tasks` | 📋 任务/协作 | 默认汇总活跃角色最近 200 条完成回报，前端先显示 30 条并折叠正文，可按收件角色筛选或逐批展开 | `/api/state?reply_role=__all__` |
| `groups` | 🧩 工作组 | 工作组池化 + 组内并行度指标 | `/api/groups` |
| `mechanisms` | 🔧 机制 | bus 机制注册表(13 类·`bus_mechanisms.json`)·related_cap 交叉链 | `/api/mechanisms` |
| `roles` | 👤 角色 | 角色注册表(`role_profiles.json`·MasterAgent owns):每角色启动流程/明式·隐式输入/边界 + **compact hook 注入两层**(🌐全局层=`meta.common_rules` verbatim 展示一次;每卡🧩角色层 render_role + 「完整 compact 注入」折叠=`build_context(role)` **WYSIWYG 逐字**)。★单一源硬口径:调 `hooks/reinject_post_compact.py` 导出函数产出、**绝不手抄副本**(防第三处飘);hook 不可读→降级横幅不塌页 | `/api/role-profiles` |
| `longrange` | 🗂️ 长程任务 | **锚定生长网**:脊柱锚点 / 活跃段 fork-merge DAG / ★决策合批单(approve/reject 一次批·`/api/lr-decide`→`longrange_net.resolve_batch`)/ 三角色 / 🔭跟踪者活动 / 复杂度曲线 / 💡已省N次确认徽标 | `/api/longrange` |
| `tasks` | 📋 任务/协作 | 派任务/收结果/待办池(ask-user) | `/api/todos` + POST `/api/send`·`/api/todos/*` |
| `live` | 🔴 实时 | 实时事件流 | `/api/live` |

## 🖥️ 系统托盘指示器(bus_tray.py)— MasterUI 维护
- **文件**:`agent_fleet/bus_tray.py`(主程序·纯 pywin32 Shell_NotifyIcon + PIL 画数字圆盘)+ `bus_tray_start.vbs`(pythonw 静默启动·**无 BOM**·wscript 可解析)。
- **自启**:`%APPDATA%\...\Startup\bus_tray.vbs`(删该文件=取消自启)。**两个 vbs 必须无 BOM**,否则 wscript 报"无效字符"启动失败。
- **单实例**:`Global\BusTraySingleton` mutex;句柄存 `_state` 长期持有(否则 GC→释放→守卫失效)。
- **图标数字** = 在跑 agent 数;**颜色**:🟢有在跑 / 🔵在岗全 idle / 🟠有静默或卡claim / 🔴全离线 / ⚪探测失败。
- **tooltip**:`在岗N · 在跑M · 静默K · 休眠P · 卡claimS · 时间`。
- **交互**:双击开 WebUI;右键 = 打开控制台 / 立即刷新 / 退出。poll 5s。
- **数据源(单一源·实时)**:优先 GET **`/api/tray`**(与运维面板同吃 `compute_fleet_cached` 缓存口径,数字永远和面板一致);WebUI 不可达时退回本机 `agentbus who --json` + 扫 processing claim。
- **口径(Master数分 定)**:
  - **在岗N** = 进程活着的真角色(vclass live/idle/warn;排除 dead/none/parked)。
  - **在跑M** = `processing/` 里 non-notice claim 新鲜(<`RUN_TTL` 3600s)且 owner 在岗 → 真执行。
  - **静默K** = 在岗里 verdict 含"静默"(N 的子集)。
  - **休眠P** = parked(故意暂离·不计在岗)。
  - **卡死claim** = `processing/` 里超 RUN_TTL 的僵尸 claim(rot·单列告警·不计在跑)。
  - ⚠ **不用 `who.current_task` 当在跑源**——该字段滞留在已卡死旧 claim 上 → 虚报在跑。

Codex 会话的直接交互回合不一定形成 Bus claim。舰队页与运营页的“会话工作中”由本机 App Server `thread/read(includeTurns=false)` 读取精确 `presence.host_session_id` 的 `active/idle` 状态；只取状态，不读取 turn 或对话正文。观察失败时状态为未知，不把信使在岗当作正在工作。舰队缓存约 20 秒刷新一次；“Bus 在跑任务”与托盘保持同一口径。

## 后端 API 一览
- **GET**:`/api/state` `/api/events`(SSE) `/api/health` `/api/tray` `/api/reap`(dry-run) `/api/todos` `/api/live` `/api/tasklines` `/api/groups` `/api/mechanisms` `/api/longrange` `/api/bi` `/api/caps` `/api/usage` `/api/dispatch_metrics` `/api/sessions` `/api/ctx` `/api/ctxpanel` `/api/prompts`
- **POST**:`/api/send` `/api/supervise` `/api/clear_replies` `/api/role-model-prefs`(仅写角色后续 Claude 派工/daemon 的 `agents.json` 默认 model/effort；空值继承全局，不切换当前交互会话) `/api/todos/{add,update,delete,dispatch,answer}` `/api/lr-decide`(决策合批写回) `/api/open-file`(📂开文件·**白名单硬约束**:只允许 `build_prompts()` 当前 openable 的真实文件 path·`os.startfile` 本机默认程序打开·127.0.0.1 only·拒任意路径=防 WebUI 变任意文件打开器攻击面)

## 后台常驻线程(8799 always-on 驱动·不依赖任何 agent 醒着·main() 启动)
- `start_fleet_refresher(20)` / `start_health_refresher(25)` / `start_ctx_refresher(12)` / `start_who_refresher(20)`:唯一刷新者·子进程扫描填缓存(请求路径零扫描)。
- `start_status_pusher(15)`:刷 `fleet_status.json`(daemon→agent 定时通信·复用缓存)。
- `start_selfwake_guard(180)`:3min 跑 `ab selfwake-guard`；Codex 调用精确会话信使恢复器，旧 Claude 掉自醒才投手动重挂提醒和 WinRT toast。
- `start_codex_messenger_guard(45)`:检查精确绑定的 Codex 信使；近期空闲、进程已退且无 processing 时重启同一 session 的信使。启动失败经 30 秒合批窗、无法安全恢复持续 90 秒后，单次扫描只向 MasterAgent 发送一条全队汇总；每角色诊断保留在本机。维修模式、旧会话、活跃任务和待核对任务不自动重启；记录在 `agent_bus/codex_messenger_guard.jsonl`，不重放任务。
- `start_maint(1800)`:30min 维护心跳=`reap`(退卡死/孤儿 processing→inbox)+ `todos-archive --older-min 1440 --apply`(归档完成>24h·防池膨胀)·仅动真卡死/旧完成态。
- `start_route_reap(300)`:**5min 独立 tick** 跑 `ab route-reap`(工作组路由超时自愈·超15min未done路由任务re-route)·**不搭 30min start_maint**(任务池太粗)·幂等 no-op(MasterAgent令·2026-07-23)。
- 全部同模子:首轮先 sleep·各自 try/except 静默不掀翻 8799·子进程 CREATE_NO_WINDOW 免弹窗。

## 运维
- 起:`ui/start_ui.ps1`(pythonw 后台)。停:`ui/stop_ui.ps1`。改代码后:`py_compile` 自检 → stop → start。
- 改 `server.py` 一律 **Edit**(禁整文件覆盖)。改 JS 后必**实测点击可达**(端点通≠tab 能开)。
- 隐私:127.0.0.1 自看控制台无外部方,默认**不脱敏**(cwd/proj 如实);唯一遮罩=用户显式 `pid_tags private=true`。见记忆 `privacy-mask-selfconsole-virus-variant`。
