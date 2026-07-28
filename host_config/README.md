# host_config/ —— 宿主全局配置(CLAUDE.md=物理原件 + settings=快照)

> **为什么有这个目录**:有些 bus 相关配置住在**宿主全局** `~/.claude/`(对所有会话生效才放那),只迁 agent_fleet 会丢。本目录让它们随 agent_fleet 走。
>
> **两种形态**:
> - `CLAUDE.global.md` = **物理原件**(2026-07-27 用户令,与 agent_bus junction 同模式):`~/.claude/CLAUDE.md` 已改为指向它的**文件软链接**(SymbolicLink),内容单一源、迁 agent_fleet 即带走,无快照分叉。
> - `settings.snapshot.json` = **快照**(非 live 镜像):配置类改动少,快照即可(用户定调 2026-06-30),改后跑 `sync_host_config.ps1` 刷新。

## 内容
- `CLAUDE.global.md` —— 全局 `~/.claude/CLAUDE.md` 的**物理原件**(宿主路径是软链)。含:① 数据默认不出本机红线;② 全局通知/不 sleep-polling 规则;③ WinRT Toast 模板;④ **## Compact Instructions** ← compact 自举层、bus 关键。
  **⚠ 编辑铁律**:改全局指令**直接编辑本文件**(物理家);别对 `~/.claude/CLAUDE.md` 做原子写(Write/先删后建)——那会把**软链打回普通文件**、内容分叉。改完无需 sync(软链即时生效)。
- `settings.snapshot.json` —— `~/.claude/settings.json` 全文快照。含:① `model`;② **`env`:`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=65` + `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`**(65% 早压,实测生效);③ **PostToolUse hook 注册**(记忆硬链刷新,脚本在 `agent_fleet/hooks/on_memory_write.py`);④ enabledPlugins 等。

## 新机迁移回填(只迁了 agent_fleet 时)
1. **重建 CLAUDE.md 软链**(需管理员或开发者模式;若新机 `~/.claude/CLAUDE.md` 已有内容先并进物理原件再删):
   `cmd /c mklink "%USERPROFILE%\.claude\CLAUDE.md" "C:\apphome\agent_fleet\host_config\CLAUDE.global.md"`
   (无法提权的兜底:直接把 `CLAUDE.global.md` 复制为 `~/.claude/CLAUDE.md`,退化为快照模式、改动记得双向同步。)
2. 把 `settings.snapshot.json` 的 `env` / `hooks.PostToolUse` / `model` 并进新机 `~/.claude/settings.json`(注意 hook 里的绝对路径 `C:/apphome/agent_fleet/...` 按新机实际改);
3. 其余按 agent_fleet 根 `CLAUDE.md` 的「迁移/换机」清单走。

## 刷新
- 全局 CLAUDE.md:**无需刷新**(物理原件在此,宿主是软链)。
- 改过 `~/.claude/settings.json` 后:
  `powershell -NoProfile -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\sync_host_config.ps1`

## 历史
- 2026-07-27 前 `CLAUDE.global.snapshot.md` 是 CLAUDE.md 的快照(已被物理原件取代删除,git 历史可查)。
