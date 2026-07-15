# 架构源代码 + 提示词地图

> 支撑这套多-agent 文件总线架构的全部源代码与提示词。配合 `ARCHITECTURE_REVIEW.md`(评审+落地表)、`AGENTS.md`(操作手册)看。
> 日期:2026-06-23

---

## 一、源代码分层地图

### 🅰 总线核心 —— `~/.claude/agent_bus/agentbus.py`(~1000 行,纯文件总线 CLI)
一个文件搞定整个总线,无服务端。函数按职责分组:

| 子系统 | 关键函数 | 作用 |
|---|---|---|
| 原子性 | `_write_json`(tmp+os.replace)、`_claim`(原子移+短重试)、`_gen_id` | 不读半截、领取不重复执行 |
| 事件等待 | `_wait_core`(watchdog 阻塞)、`_oldest_claimable`(跳高风险)、`_wait_for_reply`(request 等应答) | 不轮询、文件事件唤醒 |
| 身份/探活 | `_claude_ancestor`(父链找 claude pid)、`_touch_presence`、`_resolve_self_role`、`_alive_claude_pids`、`_self_wake_state`、`_owner_live`、`_daemon_alive` | "谁是谁"自报 + 判活 |
| 命令(17) | register/send/wait/serve/request/done/peek/replies/hello/who/tag/list/reap/supervise/prune/selfwake/dispatch | 全部对外能力 |
| 通知 | `_maybe_toast`/`_fire_toast`/`_toast_throttled` | done 端 WinRT toast 分级 |
| 审计/可视 | `_audit`(events.jsonl)、`_set_current_task`(presence 写"在做") | 留痕 + ▶在做 |
| 配置 | `cfg`(env>config.json>default) | 路径外置 |

### 🅱 执行体
| 文件 | 角色 |
|---|---|
| `worker_daemon.py` | 持久 daemon:watchdog 阻塞等任务 → 注入角色身份的 prompt → `claude_run` 流式跑 claude -p → done;含 requires_approval 闸 |
| `dispatch_agent.py` | 一次性 ad-hoc:单发、comms prompt、回填派发方、干完即逝 |
| `claude_run.py` | 共享流式执行器:`claude -p --output-format stream-json` → 解析 🔧/💬 写 live.log + 抓最终结果 |
| `start_worker.ps1` / `stop_worker.ps1` | daemon 生命周期 + tab/daemon 互斥守卫(ASCII-only) |

### 🅲 配置(声明式)
| 文件 | 内容 |
|---|---|
| `config.json` | 机器相关路径(claude_bin/cwd/repo/...),换机改一处 |
| `agents.json` | 角色注册 + 专长 desc(注入进 daemon prompt) |
| `fleet.json` | daemon 工作池(supervise 自愈的期望表) |

### 🅳 协调/恢复/UI/记忆(`C:\apphome\agent_fleet\`)
| 文件 | 角色 |
|---|---|
| `ui/server.py`(~1500 行) | WebUI:`compute_fleet_cached`(3s TTL)、5s `fleet_status.json` 战报、`start_supervisor`(90s 自愈)、`_maybe_notify_pending`(toast)、`/api/live`(live.log 面板)、self_wake 徽章 |
| `recover.py` | 冷启动探针(who/list/replies/selfwake/Edge/坐标) |
| `CLAUDE.md`/`AGENTS.md`/`CAPABILITIES.md` | 自恢复文档(本身就是提示词) |
| `hooks/on_memory_write.py`+`sync_memory.ps1` | 记忆硬链自动备份 |
| `agent_bus/`(硬链镜像) | 总线本体代码的硬链快照(让本目录自举,见末节) |

---

## 二、提示词地图(塑造 agent 行为的自然语言)

### 1. worker daemon 角色注入 prompt(`worker_daemon.run_task`)
`你是 agent bus 上的【<role>】角色执行体。你的专责领域:<agents.json desc>。以该角色视角直接执行...不要去 bus 取任务/起 loop。完成后简洁中文回复(原样回填)。` → 让 headless daemon 名副其实扮演角色。

### 2. ad-hoc 一次性 agent prompt(`dispatch_agent`)
`你是一次性 ad-hoc agent(代号 X)。只完成这件即结束、不监听队列。你有总线沟通能力:request/send 用法...回填给派发方。` → 临时工:有沟通、不等队列、干完即逝。

### 3. /loop serve prompt(AGENTS.md,可见 tab worker)
`每轮跑 serve --timeout 590;{idle}空转、{error}先 hello、任务JSON就执行 done。`

### 4. agents.json 专长 desc(按已注册角色,被 #1 注入)
角色专长在运行时从 `agents.json` 读取,随部署注册;源码不固化具体业务角色。

### 5. 恢复入口 CLAUDE.md(给冷启动 CLI 的元提示词)
"你是 MasterAgent... 角色分工... 恢复三步... 每升级基建主动简报 Master" —— 让无记忆新 CLI 重建身份。

### 6. 行为协议(AGENTS.md)
上岗两步、协调层后台 wait 自醒协议、requires_approval 闸、隐私铁律。

---

## 三、架构原则 → 代码落点

| 原则 | 代码体现 |
|---|---|
| 事件驱动不轮询 | watchdog:`_wait_core` / server `start_watcher` |
| 原子领取不重复 | `os.replace`+短重试(`_claim`) |
| 叫不醒 idle 会话 | 所以有 wait/daemon;协调位用 bg-task 完成回调自醒 |
| 自醒 | daemon=watchdog 阻塞;协调位=后台 `wait` 完成回调;检测=`_self_wake_state` |
| 身份自报 | `_claude_ancestor` 父链 + presence |
| 隐私 | `pid_tags` private 只存 label |
| 高风险统一闸 | `_oldest_claimable` 领取路径跳过 requires_approval |
| 路径可迁移 | `config.json`+`cfg()` |

---

## 四、提示词工程的坑 → 对策(都踩过)
1. daemon 被项目记忆带偏(以为自己是 bus worker)→ prompt 显式"别取任务/别起 loop,只做内联"。
2. daemon 不知自己是谁(全自报通用 worker)→ 从 agents.json 注入角色+专长。
3. 多行 prompt 被 claude.cmd argv 截断 → 一律走 stdin 传。
4. bash 双引号内反引号被执行污染 send 的 task → 单引号/无反引号。
5. .ps1 中文被 PS5.1 当 GBK 崩 → start/stop_worker.ps1 纯 ASCII。

---

## 五、自举 / 迁移(本目录是否自包含)

**可迁移单元 = 本目录 `C:\apphome\agent_fleet\`**,需自包含全部源代码:
- 协调/恢复/UI/记忆:本目录原生(CLAUDE.md/AGENTS.md/CAPABILITIES.md/recover.py/ui/hooks/memory)✓
- **总线本体代码**:真身在 `~/.claude/agent_bus\`,通过硬链镜像到本目录 `agent_bus\`(同卷 NTFS 共享 inode),所以本目录**自包含全部代码**。
- 刷新硬链:`sync_code.ps1`(原子保存会断链,改过总线代码后跑一次)。

**换机迁移**:复制本目录 → 把 `agent_bus\*` 放回 `~/.claude/agent_bus\` → 编辑 `agent_bus/config.json` 6 路径 → 跑 `recover.py` 自检。
