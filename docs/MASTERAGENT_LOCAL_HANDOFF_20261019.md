# 2026-10-19 本机封存与收尾

本批次始于 2026-10-19，最终维护复验和封包于 2026-10-20 完成；归档目录保留批次日期。

本次按用户要求规划后续版本、将已验版本和开发证据封存在项目内，并清理项目外的本轮开发副本。用户明确保留最终执行 Codex / Claude Code 会话清理的步骤；本次只交付脚本和合成测试，没有清理真实会话、注销账户或删除专用登录环境。

## 后续接手

从 [下一会话入口](MASTERAGENT_NEXT_SESSION.md) 开始。它记录首读文件、当前基线、路径和身份边界、第一张可安全执行的 B-00 工单，以及可直接复制给下一位接手者的提示。

[执行工单](MASTERAGENT_EXECUTION_BACKLOG.md) 共 34 项：B 12 项、C 7 项、D 9 项、候选版与正式发行 6 项。每项都有前置、最小产物、涉及模块、范围、测试、证据和回退/停止条件。[版本路线](MASTERAGENT_VERSION_ROADMAP.md) 记录 dev.4 → dev.5 → dev.6 → rc → 3.1.0 → 3.1.x / 3.2 的门槛、设计理由及可重新评估条件。

规划不等于发行。当前正式版仍为 `3.0.0`；已验开发版为 `3.1.0-dev.3`、schema 5、公共 API `3.1-dev1`。B 仍缺旧入口适配、常驻搬运和真实文本试点；Claude、工具与完整 UI 等缺口继续列入 3.1，不能挪到未来版本后宣称完成。

## 已封存内容

唯一归档入口为 [releases/README.md](../releases/README.md)。

- 11 个历史版本的原始 `.pyz` 与包内源码，逐版清单记录大小、SHA256、schema、原来源和证据。没有重建或覆盖历史二进制。
- 3.0 正式包摘要仍为 `69402eaad81035e121fed0d2af07b31f660ea49abc6f80b0516811831967addf`；dev.3 摘要仍为 `538172c6352df4ef79744e2e3582584f1fc223a564b0b65936cc491f5ebd078a`。
- `releases/development-archive/2026-10-19/development-evidence.zip`：1748 个开发文件，原始总量 25,593,259 字节；ZIP 为 7,177,973 字节。每个文件已对照源文件与 ZIP 校验 SHA256；清理后又完成独立 ZIP 校验。
- 本次源码、测试和接手材料封存于 `releases/handoff/2026-10-19/`；实际内容和摘要以该目录 `handoff-manifest.json` 为准。当前文档/测试快照不冒充旧版构建时的历史快照。
- 原外部 `genesis_agent_fleet_v3.0.0_local-bundle.zip` 已按原字节复制到 `releases/3.0.0/`，摘要为 `241bf6b3c89ad64bc39a2dcfc93c416fa7f1580f92bc6d59387e34b5c41488a9`。

归档只留本机，不是可公开分发的资料。认证、原生 home、私人会话与会话隔离备份不进入上述开发证据/源码包。归档库保留历史绝对路径与实例绑定，不可在项目内启动或手改绑定继续发送；新验收使用公开 API 建立独立合成环境。

## 已执行的副本清理

精确源范围为 `C:/Users/<用户>/Documents/Codex/2026-10-08/c-apphome-agent-fleet-docs-prompts` 内本轮 `outputs/`、`work/`，没有扫描或删除其它无关项目。

1. 归档校验后删除普通开发文件 **1527 个**，只移除已空目录；记录在 `cleanup-result.json`，结果为 `completed`。
2. 单独保存旧 Git worktree 的分支、HEAD、差异及完整文件，复核 **221 个**文件后通过 Git 正常移除。分支 `codex/masteragent-3-boundary` 保留，恢复说明见 `worktree-recovery.json`；操作结果见 `worktree-removal.json`。
3. 清理后复核：清单内 **1748 个源文件全部已移除**；不进入排除目录的检查没有发现其它普通文件。剩余 16 个原生 home 未遍历、未删除。
4. 55724、61670、54051、60141 临时预览已无监听，未发现引用本会话源路径的专用预览进程。本轮没有停止旧 8799 控制台或生产服务，没有迁移生产数据。

全部结果以 [final-cleanup-status.json](../releases/development-archive/2026-10-19/final-cleanup-status.json) 为当前汇总；最初 inventory 是清理前的保守元数据清单，不能替代最终状态。普通清理脚本不用于会话、凭据或任意用户目录。

## 留给用户最后收尾

| 剩余内容 | 当前状态与下一动作 |
| --- | --- |
| Codex / Claude 全部本机会话 | 按 [会话清理手册](SESSION_CLEANUP.md) 退出客户端与信使后先 Plan，核对范围再 Apply；最后可独立 Restore 或 Purge。本次没有执行真实清理 |
| 16 个专用原生 home | 精确路径见归档 manifest 的 `excluded` 及最终状态清单。12 个 3.0 验收 home，另有 native-inspection、control、disabled、schema-probe home。会话脚本可显式列入支持范围，但认证/配置保留；若需销毁专用登录目录，由用户另行核对处理 |
| 外部旧发行 ZIP | `C:/apphome/agent_fleet/releases/genesis_agent_fleet_v3.0.0_local-bundle.zip` 仍存在；项目内已有验证一致的副本。自动审批审查拒绝了删除操作，仅返回 `blocked by policy`，没有更具体原因；未绕过或重试删除 |
| 4 个本轮人工测试临时目录 | `%TEMP%/session-cleanup-fixture-tyiujg_s`、`session-cleanup-fixture-puzy1115`、`session-cleanup-fixture-xaj5y4p2`、`session-cleanup-fixture-kz81a8rv`。仅人工测试数据；删除被同类自动审批拒绝，留用户核对。绝对路径已写入最终状态清单 |

会话清理脚本只对明确列出的本机目录及已识别结构负责。遇到未知版本、混合缓存或未明确指定的 junction 目标，会列出 `uncovered`；默认拒绝将其当成全部完成。`AllowSupportedSubset` 是用户明确选择只处理支持部分，余项仍保留。认证、配置、记忆、云端/远端会话和其它未列目录不被静默删除。

## 验证与保存责任

本轮平台核心没有修改：当前 37 个平台源码/资源的摘要仍与 dev.3 包完全一致。历史平台回归为 Python 546 项（545 通过、1 项因软链接权限跳过）和前端 52 项通过；本轮未重复平台整套测试、未新增真实模型验收。

本轮另验证了归档、清理维护脚本、Windows 路径边界、中断恢复及文档链接，最终维护测试记录保存在 `releases/handoff/2026-10-19/verification.json`。合成检查不等于真实账户清理或桌面重启验收。

`releases/` 和 `maintenance/` 已加入 Git 忽略，后续 tag 快照排除二者并写入项目内 `releases/legacy-tag-snapshots/`。本轮只做 hook 语法检查，未创建 tag 或触发旧粗快照。本机封存不是异地备份；若后续移走此项目，请一并保管这两个目录，不能以为 Git 提交已经包含它们。
