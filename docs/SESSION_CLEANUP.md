# Codex / Claude Code 会话离线清理

本轮交付清理脚本和合成测试，**没有执行真实会话清理**。用户最后关闭客户端后自行执行。入口是 `scripts/Cleanup-Agent-Sessions.ps1`，核心是 `scripts/session_cleanup.py`。默认 `Plan` 只生成已支持会话文件的清单；`Apply`、`Restore`、`Purge` 都是独立显式动作。

这里的 `AllSessions` 表示清理所列本机目录下、脚本能识别的所有会话，不按项目或日期过滤。它不代表删除云端会话、其它主机、未列出的自定义 home、任意历史备份或嵌套混合缓存。遇到未识别结构会列入 `uncovered`；默认不能应用，有意先处理已支持子集时须明确加 `-AllowSupportedSubset`，归档会标记 `partial: true`。

## 覆盖范围

| 数据 | 处理 |
| --- | --- |
| Codex `sessions`、`archived_sessions`、`history.jsonl`、`session_index.jsonl` | 会话及索引一起隔离 |
| Codex `shell_snapshots`、`thread-writer-locks`、`tui-thread-reference-capabilities`、`rollout-migrations` | 已知会话衍生文件隔离 |
| `state_5.sqlite` | 备份整个 DB/WAL/SHM/journal 文件组；在离线副本中清除会话及关联表，保留 projects、project_roots、thread_sections、远控/导入配置和迁移结构 |
| `thread_history_1.sqlite`、`goals_1.sqlite` | 清已知会话表，保留迁移结构 |
| `logs_2.sqlite` | 仅删除 `thread_id IS NOT NULL` 的日志，保留非会话日志 |
| `queue_1.sqlite` | 仅支持无业务表的已知空结构；有未知表时保留并列未覆盖 |
| `.codex-global-state.json` 及 `.bak` | 仅移除代码中列明的会话索引顶层键，其余键保留；旧完整文件在隔离归档中，可恢复 |
| Claude `history.jsonl`、`sessions`、`session-env`、`file-history`、`paste-cache`、`shell-snapshots`、`todos`、`tasks`、`debug` | 已知会话及衍生文件隔离；保护项仍保留 |
| Claude 项目树 | 只选 UUID 命名的 `.jsonl`、`sessions-index.json`，以及 UUID 会话目录下的 `subagents` / `tool-results`；目录骨架、memory 和任意业务文件保留 |

不会删除 `auth.json`、`.credentials.json`、配置、hooks、skills、plugins、agent_bus、memory/memories 或 `memories_1.sqlite`。也不注销账号，不清操作系统凭据库。对混合全局状态中的 `electron-persisted-atom-state`、业务配置/历史里嵌入的会话引用、旧备份、浏览器缓存、托管 worktree 和 remote/cloud 会话没有全清承诺。发现混合 UI 状态包含会话相关键、未知编码或未识别的顶层会话键时，清单会记录 `uncovered`，不能默认为完整清理。本脚本将认证、配置和记忆作为独立数据保留；会话清理不包含这些内容。保留下来的记忆本身可能含会话摘要。

官方说明指出 `CODEX_HOME` 默认位于 `~/.codex`，同时存放配置、认证、历史等不同类型数据；不能把删除整个目录等同于清理会话。本文脚本是本项目的本机维护实现，**不是官方 reset 命令**，未声称跨版本通用。[OpenAI 配置目录说明](https://learn.chatgpt.com/docs/config-file/config-advanced)

## 用户最后执行

先保存工作，退出所有 Codex 桌面/CLI、Claude Code、App Server、agent bus 信使和可能重新启动它们的后台服务。脚本不会自动杀进程。`-ClientsStopped` 是用户确认，脚本仍会检查进程和文件占用；无法检查时拒绝操作。执行期间不要重新打开这些客户端。

在普通 PowerShell 中，先生成新清单。下面是已知两个主要 home 与 Claude 物理项目根的示例；**脚本交付时没有运行这些命令**：

```powershell
$project = 'C:/apphome/agent_fleet'
New-Item -ItemType Directory -Path "$project/maintenance" -Force | Out-Null
& "$project/scripts/Cleanup-Agent-Sessions.ps1" `
  -Action Plan `
  -CodexHome 'C:/Users/<用户>/.codex' `
  -ClaudeHome 'C:/Users/<用户>/.claude' `
  -ClaudeProjectsRoot 'C:/apphome/agent_fleet/projects' `
  -PlanPath "$project/maintenance/session-cleanup-plan.json"
```

清单包含本机绝对路径、大小、修改时间、SHA-256 和未覆盖项，不输出会话正文、数据库行、认证或配置值。计算摘要时会读取文件字节；SQLite schema 在临时副本中检查，不打开原库写入或创建原库 SHM。临时副本在检查结束时删除。清单只允许写在项目目录内，并拒绝覆盖已有清单。

`~/.claude/projects` 在此机器是 junction；必须指定物理根 `C:/apphome/agent_fleet/projects`。其中其它业务 junction 也不会被跟随：查看 `uncovered`，将其明确物理目标追加到 `-ClaudeProjectsRoot` 数组后重建清单。脚本只在明确列出的物理根中识别会话文件，不能把文件系统盘根或用户 home 当作物理项目根。

多个原生验收目录、其它 `CODEX_HOME` 或 `CLAUDE_CONFIG_DIR` 用数组显式列出：

```powershell
# 示例占位路径须替换成清单核对后的真实物理路径。
$codexHomes = @('C:/Users/<用户>/.codex', 'D:/verified-native-home')
$claudeRoots = @('C:/apphome/agent_fleet/projects', 'D:/verified-claude-project')
& "$project/scripts/Cleanup-Agent-Sessions.ps1" -Action Plan `
  -CodexHome $codexHomes -ClaudeHome 'C:/Users/<用户>/.claude' `
  -ClaudeProjectsRoot $claudeRoots `
  -PlanPath "$project/maintenance/session-cleanup-plan-reviewed.json"
```

Python 入口也支持重复 `--codex-home`、`--claude-home`、`--claude-projects-root` / `--claude-project-root`，必须显式指定 `--scope AllSessions`。本轮归档清单 `releases/development-archive/2026-10-19/archive-manifest.json` 的 `excluded` 中，`native_home_or_session_reserved_for_user` 条目是用户最终核对这些专用目录的线索。不要自动把清单中的任意目录当作 home。清理本脚本支持的会话后，原生验收目录中的登录和配置仍保留；若要彻底销毁专用目录，包括凭据，留给用户另行核对处置，本脚本没有整目录删除开关。

检查清单后，客户端保持退出，再应用：

```powershell
& "$project/scripts/Cleanup-Agent-Sessions.ps1" -Action Apply `
  -PlanPath "$project/maintenance/session-cleanup-plan.json" -ClientsStopped
```

新增或改变文件、schema 变化、路径变成 junction、硬链接、锁未释放等会拒绝操作；需要重新生成和审阅清单。未识别数据库不会删库或猜测字段。明确选择先清已支持部分时，在上述 Apply 命令加 `-AllowSupportedSubset`；保留下来的内容仍在 `uncovered` 中，不能将该结果描述为全部完成。

每次应用创建 `maintenance/session-quarantine/<UUID>/`，先复制并验证原文件 SHA-256，再移除源文件；数据库和混合全局配置在副本上处理后写回。支持跨磁盘源目录。应用前后均有进程检查，文件句柄禁止并发写入；这些保护不能替代用户关闭所有能自动重启客户端的服务。空间不足或中断时保留隔离原件和恢复清单，不自动删除失败现场。不要外发该目录：它包含本机会话和混合数据库备份。

备份、替换和恢复均先写独占临时文件，完成写入、刷新和摘要校验后才发布；不会把半文件当成备份或覆盖新的目标文件。恢复时，缺失备份的项必须证明原路径仍保有原始完整内容，否则报恢复不完整，不会标成已恢复。最终 Purge 保存逐文件进度并最后删除清单，正常的中断可用原归档 ID 继续。断电或强杀可能留下 staging 等未登记现场，脚本会拒绝盲删；这些残留需先保留、核对。

## 恢复或最终删除隔离副本

`Apply` 输出确切归档路径。保留该路径，恢复时会核对备份哈希、恢复边界和当前文件是否被客户端改动；不会覆盖清理后新产生的会话或数据库。发生冲突应先保留双方文件、人工核对，不要加删除或强制覆盖参数。

```powershell
$archive = 'C:/apphome/agent_fleet/maintenance/session-quarantine/<Apply 返回的 UUID>'
& "$project/scripts/Cleanup-Agent-Sessions.ps1" -Action Restore `
  -Archive $archive -ClientsStopped
```

确认不再需要恢复后，**独立执行** `Purge`，它只删除这个项目隔离目录中清单登记且哈希一致的文件。不得传整个 maintenance、项目、home 或盘根；意外文件、链接或哈希不符都会拒绝。

```powershell
& "$project/scripts/Cleanup-Agent-Sessions.ps1" -Action Purge `
  -Archive $archive -ConfirmArchiveId (Split-Path -Leaf $archive) -ClientsStopped
```

这是普通文件删除，不是介质安全擦除。清理完成后仍可能有前述保留项或未覆盖项。不要直接递归删除 `.codex`、`.claude` 或 Claude 的整个 `projects` 树。

## 验证事实

```powershell
& C:/path/to/python.exe -B -m unittest discover `
  -s C:/apphome/agent_fleet/tests_maintenance -p test_session_cleanup.py -v
```

测试仅创建人工数据和临时根，覆盖：默认只读、盘根/home 边界、凭据/记忆保留、清单过时、新文件竞态检查、活跃进程保护、Windows 文件锁、junction 越界与显式物理根、混合 DB/索引联动、WAL 副本与原始文件组恢复、中断保留原件、篡改恢复路径、哈希核对及独立 purge。普通 symlink 测试可在缺少系统权限时跳过；Windows junction 测试独立运行。合成通过不代表真实账号数据已清理或真实桌面重开行为已验证。

2026-10-20 末轮补查包括半写备份/替换/恢复、发布时出现新文件、锁内来源与 sidecar 变化、备份和原件同时缺失、清单硬链接与重复操作、越界临时文件意图及 Purge 中断续跑。最终运行结果以 `releases/handoff/2026-10-19/verification.json` 为准；归档批次沿用任务开始日期。
