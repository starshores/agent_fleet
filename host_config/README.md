# host_config/ —— 宿主全局配置快照(迁移母本)

> **为什么有这个目录**:bus 源码靠 `agent_bus/` 硬链镜像随 agent_fleet 走;但有些 bus 相关配置住在**宿主全局** `~/.claude/`(对所有会话生效才放那),只迁 agent_fleet 会丢。这里放它们的**快照**,作迁移到新机时往新机 `~/.claude/` 回填的母本。
>
> **定位 = 快照(非 live 镜像)**:源码类用硬链(自动新鲜);配置类改动少,**快照即可**(用户定调 2026-06-30)。改了全局配置后跑 `sync_host_config.ps1` 刷新本快照。

## 内容
- `CLAUDE.global.snapshot.md` —— `~/.claude/CLAUDE.md` 全文快照。含:① 全局通知/不 sleep-polling 规则;② WinRT Toast 模板;③ **## Compact Instructions(bus 通用·压缩时套用)** ← compact 自举层、bus 关键。
- `settings.snapshot.json` —— `~/.claude/settings.json` 全文快照。含:① `model`;② **`env`:`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=65` + `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`**(65% 早压,实测生效);③ **PostToolUse hook 注册**(记忆硬链刷新,脚本在 `agent_fleet/hooks/on_memory_write.py`);④ enabledPlugins 等。

## 新机迁移回填(只迁了 agent_fleet 时)
1. 把 `CLAUDE.global.snapshot.md` 的内容并进新机 `~/.claude/CLAUDE.md`(尤其 Compact Instructions 节);
2. 把 `settings.snapshot.json` 的 `env` / `hooks.PostToolUse` / `model` 并进新机 `~/.claude/settings.json`(注意 hook 里的绝对路径 `C:/apphome/agent_fleet/...` 按新机实际改);
3. 其余按 agent_fleet 根 `CLAUDE.md` 的「迁移/换机」清单走。

## 刷新
改过全局 `~/.claude/CLAUDE.md` 或 `settings.json` 后:
`powershell -NoProfile -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\sync_host_config.ps1`
