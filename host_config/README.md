# host_config/ —— 宿主全局配置(CLAUDE.md=物理原件 + settings=快照)

> **为什么有这个目录**:有些 bus 相关配置住在**宿主全局** `~/.claude/`(对所有会话生效才放那),只迁 agent_fleet 会丢。本目录让它们随 agent_fleet 走。
>
> **两种形态**:
> - `CLAUDE.global.md` = **物理原件**(2026-07-27 用户令,与 agent_bus junction 同模式):`~/.claude/CLAUDE.md` 已改为指向它的**文件软链接**(SymbolicLink),内容单一源、迁 agent_fleet 即带走,无快照分叉。
> - `CODEX.global.md` = Codex 全局 `AGENTS.md` 的物理原件；宿主 `~/.codex/AGENTS.md` 与它保持同一 NTFS 文件内容。当前 Windows 账户没有创建文件软链接的权限，因此用 NTFS **硬链接**(HardLink)实现单一底层文件，Codex 仍从标准全局入口读取。
> - `settings.snapshot.json` = **快照**(非 live 镜像):配置类改动少,快照即可(用户定调 2026-06-30),改后跑 `sync_host_config.ps1` 刷新。

## 内容
- `CLAUDE.global.md` —— 全局 `~/.claude/CLAUDE.md` 的**物理原件**(宿主路径是软链)。含:① 数据默认不出本机红线;② 全局通知/不 sleep-polling 规则;③ WinRT Toast 模板;④ **## Compact Instructions** ← compact 自举层、bus 关键。
  **⚠ 编辑铁律**:改全局指令**直接编辑本文件**(物理家);别对 `~/.claude/CLAUDE.md` 做原子写(Write/先删后建)——那会把**软链打回普通文件**、内容分叉。改完无需 sync(软链即时生效)。
- `CODEX.global.md` —— 全局 Codex 指令物理原件；`~/.codex/AGENTS.md` 是它的 NTFS 硬链接，因此**两条路径是同一文件**，编辑任一路径都会更新同一内容。Codex 全局指令入口遵循 `~/.codex/AGENTS.md`；agent_fleet 项目用精简的 `AGENTS.override.md` 覆盖同目录旧 `AGENTS.md`，旧文档按需查阅。新会话会读取更新后的指令，已运行会话不保证热加载。
- `CODEX.hooks.json` —— Codex 用户级 hooks 的物理原件；`~/.codex/hooks.json` 应为其 NTFS 硬链接。全局 SessionStart 按精确角色绑定/显式 role-next 票据加载角色卡与项目记忆索引；已配置项目自己的 startup/resume 注入时跳过重复加载，compact/clear 仍走全局恢复。
- `settings.snapshot.json` —— `~/.claude/settings.json` 全文快照。含:① `model`;② **`env`:`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=65` + `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`**(65% 早压,实测生效);③ **PostToolUse hook 注册**(记忆硬链刷新,脚本在 `agent_fleet/hooks/on_memory_write.py`);④ enabledPlugins 等。

## 新机迁移回填(只迁了 agent_fleet 时)
1. **重建 CLAUDE.md 软链**(需管理员或开发者模式;若新机 `~/.claude/CLAUDE.md` 已有内容先并进物理原件再删):
   `cmd /c mklink "%USERPROFILE%\.claude\CLAUDE.md" "C:\apphome\agent_fleet\host_config\CLAUDE.global.md"`
   (无法提权的兜底:直接把 `CLAUDE.global.md` 复制为 `~/.claude/CLAUDE.md`,退化为快照模式、改动记得双向同步。)
2. 把 `settings.snapshot.json` 的 `env` / `hooks.PostToolUse` / `model` 并进新机 `~/.claude/settings.json`(注意 hook 里的绝对路径 `C:/apphome/agent_fleet/...` 按新机实际改);
3. 其余按 agent_fleet 根 `CLAUDE.md` 的「迁移/换机」清单走。
4. **配置 Codex 全局指令**：确保 `~/.codex/AGENTS.md` 指向/链接到本目录的 `CODEX.global.md`。优先用符号链接；无创建符号链接权限时，可在同一 NTFS 卷使用硬链接(`New-Item -ItemType HardLink ...`)保持单一物理文件。不要复制成两份长期维护。

## 刷新
- 全局 CLAUDE.md:**无需刷新**(物理原件在此,宿主是软链)。
- 全局 Codex `AGENTS.md`:源文件与宿主入口是同一文件(当前为硬链接)，编辑后无需复制或同步；新会话载入全局指令。
- 改过 `~/.claude/settings.json` 后:
  `powershell -NoProfile -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\sync_host_config.ps1`

## 历史
- 2026-07-27 前 `CLAUDE.global.snapshot.md` 是 CLAUDE.md 的快照(已被物理原件取代删除,git 历史可查)。
