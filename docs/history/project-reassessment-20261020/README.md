# 整体项目重评证据 · 2026-10-20

主文档：[MasterAgent 项目整体重新评估](../../MASTERAGENT_PROJECT_REASSESSMENT_20261020.md)。本目录评估整个项目，不替代另一个 `model-contributions-20261020` 历史归因目录。

## 本轮实际做了什么

| 证据 | 内容与结果 | 范围限制 |
| --- | --- | --- |
| [evidence.json](evidence.json) | 源文件/文档摘要、包完整性与工作树匹配、Git 元数据、13 项发现索引、历史测试日志摘要 | 静态与归档核验，不是整套测试重跑或当前生产检查 |
| [runtime_child_reply_result.json](runtime_child_reply_result.json) | 两个场景都有真实 SQLite 子结果；一次性执行提示词遗漏子结果，成员领取路径包含。各 1 次父 Attempt，均由替身完成 | 真实 Platform、领域状态和提示词构造；原生探测/执行为替身，没有模型或常驻服务 |
| [architecture_endpoint_overlap_result.json](architecture_endpoint_overlap_result.json) | 来源/目的端先绑定再安装重叠工作区均使原路由准入失败；反向顺序均拒绝，共四案例、0 Attempt | 真实领域 API 与临时合成库；未覆盖全部祖先/子目录与并发组合 |
| [product_uncertain_request_reproduction.json](product_uncertain_request_reproduction.json) | 同一 CommandStore 重试复用 ID，模拟保存仍为 1 条；重建 Store 后相同内容使用新 ID，模拟保存为 2 条 | 真实前端模块、内存后端替身；未运行真实浏览器、HTTP 或后端，不证明生产已重复执行 |
| [architecture_endpoint_overlap_summary.md](architecture_endpoint_overlap_summary.md) | 路径案例的操作顺序、结果与执行说明 | 保留首次证据脚本修正记录，不把它计为产品测试失败 |

两个 Python 复现仅在项目外创建专用临时根，使用合成数据，结束前验证本次创建的目录边界并清理；结果记录 `temporary_root_removed=true`。前端复现只使用内存替身。所有永久证据保存在本目录，没有外发。

R01/R02/R03 是当前缺口的复现，成功复现不等于产品已修复。R04–R13 的证据等级逐项见主报告与 `evidence.json`。原 3.0 真实验收和 dev.4 全套回归是历史证据，本轮没有重新执行。

## 后续如何复查

脚本均从自身位置确定项目根。Python 需要 3.11+，前端复现需要能执行现有 ES 模块的 Node；本轮使用本机 `server` Python 与 Node v24.14.0。下面从项目根运行，未来复查请使用新的输出文件名，保留本次基线。

```powershell
python -B docs/history/project-reassessment-20261020/runtime_child_reply_repro.py

python -B docs/history/project-reassessment-20261020/architecture_endpoint_overlap_repro.py --output docs/history/project-reassessment-20261020/architecture_recheck_NEW.json

node docs/history/project-reassessment-20261020/product_uncertain_request_reproduction.mjs

python -B docs/history/project-reassessment-20261020/collect_evidence.py --output docs/history/project-reassessment-20261020/evidence_recheck_NEW.json
```

运行时脚本默认输出 JSON，指定 `--output` 时拒绝覆盖已有文件；架构脚本要求新输出位于本目录。前端脚本输出 JSON，不自动保存磁盘。采集脚本只核查静态文件、归档和已有复现结果，不隐式运行其它脚本；发现源码与本次包或复现摘要不一致会停止，应建立新评估基线，不能覆盖旧证据掩盖变化。

源码入口：

- [collect_evidence.py](collect_evidence.py)
- [runtime_child_reply_repro.py](runtime_child_reply_repro.py)
- [architecture_endpoint_overlap_repro.py](architecture_endpoint_overlap_repro.py)
- [product_uncertain_request_reproduction.mjs](product_uncertain_request_reproduction.mjs)

本目录没有保存认证、私有会话、生产邮箱、业务库或原生推理内容。临时路径、合成 ID 和源码摘要用于本机复查；将来开源时仍需选择公开范围。
