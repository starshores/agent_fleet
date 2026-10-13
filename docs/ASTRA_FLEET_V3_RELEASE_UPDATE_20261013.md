# 给 Astra 的 3.0.0 发布增量简报

日期：2026-10-13（Asia/Shanghai）。接续 [dev.5 与真实 Fleet 快照](ASTRA_FLEET_DEV5_ACCEPTANCE_BRIEF_20261013.md)。本文整理本机正式发行的最新证据，不代表旧 fleet 已切换到 V3，也不授权外发或生产迁移。

## 当前结论

- `masteragent-3.0.0.pyz` 已于 2026-10-13 15:37:51 在本机正式发行，状态 schema 4，范围是 Windows 前台常驻、纯文本协调基座。release gate 标记 `eligible=true`、`formally_published=true`、`destination=local_only`。
- 这是**真实原生模型验收**：最终包的两个角色完成接单、同成员连续任务、执行中纠偏、停止、断线后显式恢复，以及同构建 A→B→A；最终轮 10 个实际原生 Attempt、42 项验收检查通过。首轮失败候选另保留 2 个实际 Attempt，合计 12 个；首轮失败不能并入最终通过计数。
- 现有 Codex/agent_bus 舰队是另一条真实运行线。本轮 `fleet-state` 仍显示 20/20 预期角色 armed、0 recent/stale/off、2 parked、守卫值守。V3 没有接管旧 bus 的任务、会话或生产状态；发布证据明确标记 `old_fleet_takeover=false`、`production_migration=false`。

## 本轮独立核对

| 核对项 | 结果 |
| --- | --- |
| 正式包 SHA256 | `69402eaad81035e121fed0d2af07b31f660ea49abc6f80b0516811831967addf`，与 release gate、真实验收报告一致 |
| 包内容 | ZIP 可完整读取；清单 19 个文件摘要均匹配；包内 `masteragent/` 文件与当前主目录源码逐字节一致 |
| 包入口 | `python -B masteragent-3.0.0.pyz --version` 返回 `3.0.0` |
| 原始证据链接 | 真实验收报告和事件 JSONL 的 SHA256 均与 release evidence 中记录一致；事件文件 257 行 |
| 发布者回归记录 | V3 368 项中 367 通过、1 项因 Windows 符号链接权限跳过；旧 fleet 诊断定向 29/29 通过。本轮仅核对报告，未重跑测试或真实模型 |

真实验收报告标记 `passed=true`、`simulated=false`、`synthetic_attempts=0`、`real_native_model_attempts=10`。42 项检查包含精确原生 thread/turn、请求与观察模型一致、纠偏回执和结果采纳、进程退出、未知历史保留、A/B/A home 隔离与成员结束。验收固定 `codex-cli 0.162.0-alpha.17.2` 和 `gpt-6-astra`；报告中的 Attempt 数不是原生内部计费请求的精确数量。

首轮发现原生自动生成的 bundled skills 目录使第二任务预检被拒；最终包在显式启动配置里关闭 `skills.bundled.enabled` 和 `skills.include_instructions`。首轮失败证据保留，最终轮使用独立目录和重建包。源码回归、旧 fleet 定向回归与真实验收是不同证据层，不能互相替代。

## Astra 下一步应审查

1. **发布范围与承接条件**：3.0.0 的纯文本协调能力已在独立状态根完成真实验收；哪些实际任务适合先单入口灰度，如何避免旧 bus 与 V3 双重领取？
2. **恢复语义**：停止、断线、`needs_reconcile` 与确认原生进程退出的证据是否足够；未确认状态必须继续待核对，不自动重放。`online` 是租约，不代表模型连通；outbox 持久化也不代表已投递或已读。
3. **尚缺能力**：原生 resume、模型自主调用平台命令、运行中人工审批桥、业务文件工具、Windows 服务托管和生产迁移均未在本版交付。评估前不要把纯文本验收扩写成这些能力已可用。
4. **认证与部署**：本次专用 home 的认证复用经用户限范围授权；后续部署仍需明确可执行路径、固定版本、模型、独立 home 与认证流程，不应继承私人或旧 fleet 凭据。
5. **旧 fleet 稳定性**：继续观察 20 角色信使状态和缺失/损坏状态文件诊断；release 的旧 fleet 回归覆盖判据，不等于可自动接管掉线角色。

## 本机证据入口

- 正式说明：`docs/MASTERAGENT_3_RELEASE.md`；阶段状态：`docs/MASTERAGENT_3_PROGRESS.md`。
- 本地交付目录：`C:\Users\<用户>\Documents\Codex\2026-10-08\c-apphome-agent-fleet-docs-prompts\outputs\`。
- 同目录正式包及证据：`masteragent-3.0.0.pyz`、`MA_V3_0_RELEASE_HANDOFF_20261013.txt`、`MASTERAGENT_3_RELEASE_GATE.json`、`MASTERAGENT_3_RELEASE_EVIDENCE.json`、`MASTERAGENT_3_REAL_ACCEPTANCE.json`、`MASTERAGENT_3_REAL_EVENTS.jsonl`。首轮失败候选及事件亦在同目录独立保留。

本轮未启动或迁移服务、未调用真实模型、未读取无关会话或上传资料。
