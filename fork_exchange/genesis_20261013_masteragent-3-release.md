# genesis 2026-10-13：MasterAgent 3.0 本机发行交接

本单供另一个 bus 按 [四层进化规则](../docs/MULTI_BUS_EVOLUTION.md)评估吸收。3.0.0 已在本机发行；旧 genesis bus 仍独立运行。本次未打 Git tag，故不声称完成带 commit 全量表的 tag 发布。`C:\apphome\agent_fleet\releases` 下的 `genesis_agent_fleet_v3.0.0_local-bundle.rar` / `.zip` 是精选文件归档，不是旧 githook 生成的整仓 tag 快照。发行包摘要与验证见 [正式版说明](../docs/MASTERAGENT_3_RELEASE.md)。

| 增量 | 为什么 | 可吸收性 | 吸收单元及验证 |
| --- | --- | --- | --- |
| 业务包、安装/代次、角色部署与通用基座分离 | 更换业务时保持知识、状态和权限归属 | 平台面，可吸收 | `masteragent/contracts.py`、`deployment.py`、`service.py`；同构建空载→A→B→A |
| Task/Attempt 双租约、未知结果待核对、显式恢复 | 避免超时或断线后重复外部效果 | 平台面，可吸收 | `masteragent/storage.py`、`service.py`、`worker.py`；检查迟到回填和恢复边界 |
| Windows 前台成员事件接单与 App Server 精确 thread/turn | 保持持续收件与受控原生执行 | 需适配宿主 | `masteragent/members.py`、`state_waiter.py`、`codex_app_server.py`；固定原生版本下查实际进程和回执 |
| 旧 Codex 信使仍独立值守 20 个角色 | 避免发行即误停现有运行线 | 实例面，跳过 | 当前 `agent_bus/` 状态和精确 session 绑定；消费方按自身现场确认 |
| 3.0 仍缺旧 bus 单入口适配与真实业务工具 | 发行包不能直接接管旧任务或角色职责 | 需适配 | 接管清单见 [工作态](../docs/MASTERAGENT_3_TAKEOVER_20261013.md)；先验证单入口与回报，再灰度切换 |

本次发行产物尚未进入 Git tag。未来若打 `v3.0.0` tag，须在打 tag 前以实际 `git log <上个tag>..<候选提交>` 补齐每个 commit 的变更表，保留 minor/cosmetic 行，并由发布钩子生成对应 zip。不得用当前脏工作树臆造提交清单。
