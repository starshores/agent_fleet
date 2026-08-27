# _workstate —— daemon 跨任务接续的【提炼工作态】文件(2026-08-27)

> 用户令/Master数分口径:daemon 绝不在进程内跨任务背全上下文(丢其省 token/可注入优势),接续外置。
> 三层:A)状态文件落档+重读(默认·省+稳) B)CLI 协调位持状态指针 C)`--resume` 全上下文(贵·仅 A 不足时)。
> 本目录 = 层A 的落地:per work-thread 一个提炼工作态文件。

## 约定
- **路径**:`agent_bus/_workstate/<thread>.json`(thread = 一条工作线的稳定 id,由编排方定)。
- **谁写**:前序任务的执行体(daemon/dispatch 的 claude)在完成时,把【提炼工作态】落到该文件(不是全上下文·是压缩要点)。
- **谁读**:接续任务经 `--continue-from-state <该文件>` 派发 → 启动器自动在 prompt 前置"先读该文件恢复上文"(见 claude_run.resolve_continue),执行体读它接着干。

## schema(建议字段·JSON)
```json
{
  "thread": "<工作线id>",
  "updated": "<ISO时间>",
  "summary": "已完成什么/当前进展(压缩要点·非流水)",
  "next": ["下一步1", "下一步2"],
  "findings": ["关键发现/结论(带实证)"],
  "artifacts": ["产物路径(文件/表/commit)"],
  "session_id": "<可选·上轮claude session·A不够时供层C --resume>"
}
```

## 三层怎么选(口径归Master数分)
- **默认 A**:发接续任务带 `--continue-from-state`,执行体读提炼态接着干。省 token、稳。
- **A 不够(需完整上文细节)→ C**:发接续任务带 `--resume-session <上轮session_id>`(从上轮回执/本文件 session_id 取)→ claude -p `--resume` 全上下文接续。贵,按需。
- **B**:有状态协调在 CLI 协调位(持线程),把状态指针塞进接续任务 payload,daemon 只执行离散步骤读写持久态。

## 清理
提炼态是运行态过境产物·非素材;工作线结束即可删该文件(卫生代谢可纳入)。
