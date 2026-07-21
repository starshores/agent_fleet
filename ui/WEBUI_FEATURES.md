# Bus 控制台 WebUI — 功能文档(MasterUI 维护)

> 用户 2026-07-14 指名 MasterUI 维护本文档:现有面板 + 系统托盘件都进文档。
> **数据口径**(在岗/在跑/静默/休眠/卡claim 等定义)归 **Master数分2**;**渲染/机制/文档** 归 **MasterUI**。要调口径找Master数分2 对齐。
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
| `fleet` | 🛰 舰队/运维 | 每角色 verdict(在岗/静默/掉线/**🅿️休眠**/进程已退)+ 自醒 + turn 状态 + 队列计数;未认领 claude 进程(tagged/unknown) | `/api/state` |
| `bi` | 📊 运营/资源 | 运营/资源 BI(token 消耗为主口径,非费用) | `/api/bi` |
| `usage` | 📈 历史用量 | usage.db 历史用量(ETL·token_stats) | `/api/usage` |
| `caps` | 🧬 能力池 | 能力池(谁被授权哪些 cap·登记归培训师) | `/api/caps` |
| `ctx` | 🧠 上下文 | 各会话上下文占用(proj 如实显·不掩码) | `/api/ctx` `/api/ctxpanel` |
| `prompts` | 📝 提示词审核 | 提示词审核(每节**行号 gutter**·左侧等宽行号列/每逻辑行一行·内容 wrap 不打乱行号;真实文件节带 **📂开文件** 按钮→默认程序打开) | `/api/prompts` + POST `/api/open-file` |
| `tasklines` | 🎯 任务线看板 | 长期任务线卡片 + 待决策徽标 | `/api/tasklines` |
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
- **口径(Master数分2 定)**:
  - **在岗N** = 进程活着的真角色(vclass live/idle/warn;排除 dead/none/parked)。
  - **在跑M** = `processing/` 里 non-notice claim 新鲜(<`RUN_TTL` 3600s)且 owner 在岗 → 真执行。
  - **静默K** = 在岗里 verdict 含"静默"(N 的子集)。
  - **休眠P** = parked(故意暂离·不计在岗)。
  - **卡死claim** = `processing/` 里超 RUN_TTL 的僵尸 claim(rot·单列告警·不计在跑)。
  - ⚠ **不用 `who.current_task` 当在跑源**——该字段滞留在已卡死旧 claim 上 → 虚报在跑。

## 后端 API 一览
- **GET**:`/api/state` `/api/events`(SSE) `/api/health` `/api/tray` `/api/reap`(dry-run) `/api/todos` `/api/live` `/api/tasklines` `/api/groups` `/api/mechanisms` `/api/longrange` `/api/bi` `/api/caps` `/api/usage` `/api/dispatch_metrics` `/api/sessions` `/api/ctx` `/api/ctxpanel` `/api/prompts`
- **POST**:`/api/send` `/api/supervise` `/api/clear_replies` `/api/todos/{add,update,delete,dispatch,answer}` `/api/lr-decide`(决策合批写回) `/api/open-file`(📂开文件·**白名单硬约束**:只允许 `build_prompts()` 当前 openable 的真实文件 path·`os.startfile` 本机默认程序打开·127.0.0.1 only·拒任意路径=防 WebUI 变任意文件打开器攻击面)

## 运维
- 起:`ui/start_ui.ps1`(pythonw 后台)。停:`ui/stop_ui.ps1`。改代码后:`py_compile` 自检 → stop → start。
- 改 `server.py` 一律 **Edit**(禁整文件覆盖)。改 JS 后必**实测点击可达**(端点通≠tab 能开)。
- 隐私:127.0.0.1 自看控制台无外部方,默认**不脱敏**(cwd/proj 如实);唯一遮罩=用户显式 `pid_tags private=true`。见记忆 `privacy-mask-selfconsole-virus-variant`。
