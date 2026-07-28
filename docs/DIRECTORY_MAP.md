# agent_fleet 目录与文件作用总览（Directory Map）

> BUS 2.0 后的完整工程地图：**每个目录 / 每个文件的职责**。
> 维护：MasterAgent（基建）。命令一律 `ab <cmd>`。架构基线：`~/.claude/agent_bus` 是指向 `C:\apphome\agent_fleet\agent_bus` 的 **junction**，项目根 = 唯一物理家。
> 概览版见根 `README.md`；本文是逐文件详版。

---

## 根目录（自恢复锚点 + 工具 + 脚本）

| 文件 | 作用 |
|---|---|
| `CLAUDE.md` | **会话自恢复入口** + 角色分工 + 纪律（Claude Code 自动加载，必须在根）|
| `AGENTS.md` | 总线操作手册：命令 / 执行模型（前台 cli vs daemon）/ 职责边界 |
| `CAPABILITIES.md` | 已打通的对外通道说明（sql_agent / EDGE CDP / Airflow）|
| `README.md` | 工程地图（概览版）|
| `recover.py` | 活体自检：谁在跑 / inbox 待领 / done 待读 / Edge 锁 / fleet 期望态 |
| `BUS_ONBOARDING.md` | 培训师新人 onboarding 流程（ui/api.py 提示词页真读 + identity hello 指向）——运行时操作锚点·留根 |
| `SESSION_RELIABILITY.md` | 会话可靠性（compact 失忆 / 自醒·投递不可信 硬化）——reinject hook 指向·运行时操作锚点·留根 |
| `SPECIALIZATION.md` | 专一性纪律（CLI 本能把非路由内容自揽的根因与「骑本能重定向为路由」解法）——reinject hook + onboarding 指向·运行时操作锚点·留根 |
| `LONGRANGE_TASK_BRIEF_2026-07-13.md` | 长程任务机制简报（历史锚点）|
| `bus_smoke.py` | **回归冒烟金标准**（46 命令面盘点 + 39 结构用例）——2.0 重构回归闸 |
| `bus_smoke_baseline.json` | 冒烟基线（命令面快照，回归对比用）|
| `bus_mechanisms.json` | 机制注册表（单一源，供 WebUI 🔧机制 tab）|
| `bus_tray.py` | 系统托盘指示器（poll WebUI `/api/tray`，MasterUI 维护）|
| `bus_tray_start.vbs` | 无窗口启动托盘（ASCII 无 BOM，手动 wscript 起，不开机自启）|
| `group_metrics_spec.py` | 工作组指标口径定义（使用方 owner）|
| `sync_code.ps1` | junction 守卫（检测到 reparse 点即 no-op，防 hook 误把文件硬链回自己）|
| `sync_memory.ps1` | MasterAgent 运行记忆硬链镜像维护（Write/Edit 断链后刷新）|
| `sync_host_config.ps1` | 宿主 settings.json 快照刷新 + CLAUDE.md 软链完好性守卫（`host_config/`）|

---

## `agent_bus/` — 总线本体（物理原件）

### 入口 + 启动器
| 文件 | 作用 |
|---|---|
| `agentbus.py` | **CLI 入口 + 薄命令分发器**（175 行）：re-export + `_CMD_GROUPS` + 分组 help + `main`。46 子命令 |
| `ab` / `ab.cmd` | 短命令启动器（固化 env python + 真实路径 + UTF-8，消除裸 python / 长路径 / 编码坑）|
| `worker_daemon.py` | daemon 形态执行体（后台常驻自醒 worker；当前 fleet 空，能力保留 dormant）|
| `dispatch_agent.py` | 派遣一次性 agent（`ab dispatch` 用）|
| `claude_run.py` | 起 claude 子进程封装（CREATE_NO_WINDOW 防弹窗）|
| `pool_dispatcher.py` | 工作组池化派发 |
| `longrange_net.py` | 长程任务「生长网」引擎（多根 DAG + 里程碑闸 + 决策合批）|
| `start_worker.ps1` / `stop_worker.ps1` | 拉起 / 停止 daemon worker |
| `complexity_snapshot.py` | 复杂度快照工具（使用方 owns）|
| `external_read.py` / `external_msg.py` / `external_sheet_read.py` / `external_canvas.py` | EDGE CDP 通道（本机 Edge 调试口读云文档/消息/表格/画布）|

### `agent_bus/bus/` — BUS 2.0 一体化包（20 模块，依赖分层无环）
| 模块 | 作用 | 层 |
|---|---|---|
| `config.py` | 路径/常量/`_now`（机器相关配置从 `config.json` 读）| 叶子 |
| `io.py` | 原子写 / 审计 / JSON 落地（含 os.replace 重试）| 叶子 |
| `roster.py` | agents.json 读写 / 角色目录 / 退役判定（archive-aware）| 叶子 |
| `proc.py` | 进程探活 / 心跳年龄 / claude pid 扫描（psutil）| 底层 |
| `presence.py` | presence 心跳读写 / pid 标注 | 底层 |
| `selfwake.py` | 自醒/park 中层 + guard 豁免 | 中层 |
| `notify.py` | toast / 完成通知（HUMAN_ATTENDED 分级）| 中层 |
| `mailbox.py` | 任务原子领取原语（O_EXCL；解 core↔pool 环）| 中层 |
| `pool.py` | 工作组 / park / fanout / mem-cap | 命令层 |
| `core.py` | **命脉**：send/wait/serve/done/request/peek/replies + wait 循环 | 命令层 |
| `edge.py` | Edge 互斥锁（acquire/release + stale 回收）| 命令层 |
| `roles.py` | register/retire/revive/retired/caps/roster/route + caps 池 | 命令层 |
| `exec.py` | `cmd_dispatch` 派遣一次性 agent | 命令层 |
| `sessions.py` | 会话元数据 / 标注 / 活死态 | 命令层 |
| `tasks.py` | 长期任务线看板 + ask-user 待办池 + answer 回填 | 命令层 |
| `health.py` | 健康总检 / sweep / reap / supervise / prune / webui-guard / mem-cap / stale（最大簇）| 命令层 |
| `status.py` | **`_who_data` 权威状态源**（verdict 单一源）+ who/list | 命令层 |
| `identity.py` | hello/whoami/selfwake*/register/tag + onboarding | 命令层 |
| `refs.py` | 三 store 跨引用契约 + `ab refs` 校验（P2-2b）| 命令层 |
| `__init__.py` | 包初始化 |  |

### 配置 + 运行态 JSON / DB
| 文件 | 作用 |
|---|---|
| `config.json` | **机器相关路径集中配置**（claude_bin / python_bin / repo / usage_db …；换机只改这一处）|
| `agents.json` | 角色花名册（注册名 + caps）——canonical，别读镜像 |
| `capabilities.json` | 能力池定义（caps 登记，培训师 owns）|
| `fleet.json` | daemon 期望态（`daemons=[]` 刻意保留 dormant，`_comment` 载恢复法）|
| `fleet_status.json` | server.py 每 15s 刷的舰队战报快照（daemon→agent 定时通信）|
| `retired_roster.json` | 归档式退役档案（11 条，含 session_ids / succeeded_by）|
| `todos.json` | ask-user 非阻塞待办池 |
| `tasklines.json` | 长期任务线看板数据 |
| `session_tags.json` / `pid_tags.json` | 会话 / pid 标注 |
| `usage.db` | **持久方舟**：token 用量增量 ETL（Claude Code 清老 jsonl 也留住记录）|
| `events.jsonl` / `longrange_events.jsonl` / `metrics.jsonl` | 审计 / 长程网事件 / 指标流水 |
| `*.bak-*` / `*.pre-*.bak` | 各文件历史备份（agents/todos/fleet/capabilities/usage.db 改动前留档）|

---

## 其它顶层目录

| 目录 | 作用 |
|---|---|
| `ui/` | 协调 WebUI 控制台（`server.py` @ 127.0.0.1:8799；`start_ui.ps1`/`stop_ui.ps1`；`WEBUI_FEATURES.md` 功能文档；`install_autostart.ps1`）——MasterUI lane |
| `hooks/` | Claude Code 生命周期 hook：`reinject_post_compact.py`（compact 后自定位）/ `block_askuser.py`（防阻塞询问转待办池）/ `enforce_selfwake.py`（自醒守卫）/ `turn_state.py`（回合观测）/ `on_memory_write.py`（记忆写触发重链）|
| `docs/` | 设计 / 评审 / 简报 / 计划文档（本文所在；ARCHITECTURE_REVIEW / BUS_2.0_PLAN / BUS_2.0_P2_DESIGN / NEWINFRA_* / LONGRANGE_HOWTO …）|
| `memory/` | MasterAgent 运行记忆硬链镜像（源 = `~/.claude/projects/XXX/memory`；只镜像 MasterAgent/bus 运行记忆，不含业务知识）|
| `host_config/` | 宿主全局配置（`CLAUDE.global.md` = 全局 `~/.claude/CLAUDE.md` **物理原件**·宿主路径是文件软链接·2026-07-27 + `settings.snapshot.json` 快照；换机回填用，含 Compact 指令 + 早压 env + hook 注册）|
| `githooks/` | 发版钩子（`reference-transaction`：打 vX tag 时自动 zip 快照到同级 `agent_fleet_releases/`）|
| `session-archive/` | 退役会话 jsonl 归档 + manifest（轮换时冻结存档，可 --resume 回溯）|
| `proposals/` | 待提议的外部变更草案（如 `XXX_CLAUDE.md.proposed`）|
| `__pycache__/` | Python 字节码缓存（自动生成，非源码）|
