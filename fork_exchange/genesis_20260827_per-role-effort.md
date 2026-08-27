# genesis 分岔改动:per-role / per-task effort 注入 + daemon 路径承载

> fork:genesis · 日期:2026-08-27 · 覆盖:per-role/per-task 推理档位(effort)机制 + daemon 路径复活承载 max 硬案。
> 主题:**任何"想给不同角色配不同推理预算(effort/reasoning budget)"的 bus 都会撞上的可行性边界,及可自动化的落点**——对另一个 bus 吸收价值最高的是【scope 洞见】,机制次之。

## ★核心洞见(最高吸收价值·去语境化):per-role effort 只有【启动面】能自动注入

一个 bus 想让"角色 A 用 xhigh、角色 B 用 medium"时,会发现**三条路径可行性天差地别**:

| 执行面 | 能否 per-role 自动注入 | 机制 |
|---|---|---|
| **daemon / dispatch**(程序 spawn 的 `claude -p`) | ✅ **能** | 起进程时按角色解析档位、注入 `--effort <level>` |
| **已运行的交互 tab** | ❌ 不能自动 | 无法向已跑会话注入;`/effort` 是用户侧输入、agent 自己发不了;多角色共用 cwd → 项目级 settings 也切不干净 |
| **新开/重启的交互 tab** | ◐ 半自动 | 启动时 `claude --effort <level>`(对交互会话也生效,非仅 -p);需角色用带档命令启动而非裸 `claude` |

- **运行中交互会话可用 `/effort <level>` 手动即时切换**(官方 slash 命令·不重启),但**不可被总线自动化**。
- **结论**:要"per-role 推理预算全自动",把**需要固定高档的角色放到 daemon/dispatch 执行面**(那里能注入),而非依赖交互 tab。这是本次"daemon 路径复活"的根本动机。

## 改动 1:per-role / per-task effort(+ model)注入 claude -p ★平台面

- **改了什么/为什么**:此前 `claude -p`(daemon/dispatch 起的)**不注入任何 `--effort`/`--model` = 全队一刀切吃全局 settings**,粒度到不了角色。现按"角色声明 + 任务覆盖"注入。
  - 解析优先级:**per-task(dispatch `--effort`)> per-role(`agents.json[role].effort`)> None(不注入=继承全局)**。
  - 同处顺带接线 `--model`(genesis 侧发现:`agents.json` 的 `model` 字段**声明了但无任何代码读取=静默空转**,一并接上)。
- **可吸收性**:✅ 平台面机制(任何有 daemon/dispatch `claude -p` 执行面的 bus 通用)。
- **吸收单元**:
  - `agent_bus/claude_run.py`:`resolve_effort(role, task_effort)` / `resolve_model(role)` + `run_streaming(..., effort=None, model=None)` 注入 `--effort`/`--model`;
  - `agent_bus/worker_daemon.py`、`agent_bus/dispatch_agent.py`:解析并传入(dispatch 侧读 `rec.effort` 做 per-task 覆盖);
  - `agent_bus/bus/exec.py`(dispatch rec 加 `effort`/`model` 字段)+ `agentbus.py`(`ab dispatch --effort/--model` 参数)。
- **验收**:bus_smoke 43/43;单元测优先级(task>role>None);**实测铁证**=daemon 执行任务时抓 `claude` 进程命令行含 `--effort max`。
- **需适配**:`agents.json[role].effort` 的**档位值(口径)是实例面**——吸收方按自己角色分工填(genesis 口径:改基建/前端码 xhigh、数据诊断 high、协议/外发 medium)。

## 改动 2:daemon 路径承载 max 硬案(dedicated max-effort worker 模式)★设计可吸收

- **改了什么/为什么**:为落地"硬案(多层深根因)用 max、例行活别常驻 max(白烧 token + 官方警示 max 过度思考)"的口径,**不对例行角色逐案调档**,而是**专设一个 effort=max 的常驻 daemon 复制体**,把硬案路由给它 → 自动 max。绕过交互 tab 注入不到的 scope 边界(见核心洞见)。
- **可吸收性**:◐ 设计模式可吸收(dedicated max worker + 硬案路由),✅ 值得任何 bus 借鉴;**具体角色/fleet 条目=实例面不吸收**。
- **吸收单元(模式)**:`fleet.json` 加 daemon 条目 `{role, cwd, wait_timeout}` → supervise 拉起 → worker_daemon 每任务经改动1注入该角色 effort。
- **验收**:supervise dry-run 认到 → 真起 `✓ daemon在听` → 命令行 `--effort max` 铁证 → 自检任务 status=ok。
- **需适配 / 实例面**:genesis 建的是 `示例组员5`(cwd=业务库·示例组复制体);另一个 bus 换自己的硬案角色。**注意**:此改动**局部推翻了 genesis 早前"全 daemon 移除·全前台化"策略**(2026-07-01)——即"daemon vs 前台 cli"是随需可逆的策略选择,非单向;哪面能满足需求(此处=per-role effort 自动注入)就用哪面。

## side-finding(可吸收的通用教训):声明了却无消费者的 config 字段 = 静默空转

- `agents.json` 的 `model` 字段长期"声明但无代码读取",看似 per-role model 已配、实则从未生效。**教训**:配置字段必须有消费端读取路径,否则是 no-op 幻觉;加字段时同步接线 + 加一条断言/日志证明真被读。genesis 本次一并接线 model。

## 全量变更清单(含 minor/cosmetic·锚到 commit·零遗漏)
> 约定(2026-08-27 用户令):对比基版的【每一条】改动都进 fork,连"改一个字符让显示更美观"也记——每 commit 一行、标可吸收性,发版时 `git log <上个tag>..<tag>` 即完整清单。重大机制上面已四层详述,此表兜住全部(含琐碎)。

| commit | 文件 | 一句话 | 可吸收性 |
|---|---|---|---|
| `28306a3` | claude_run/worker_daemon/dispatch_agent/bus.exec/agentbus | per-role/per-task effort+model 注入 claude -p(本文改动1) | ✅ 平台面 |
| `dcbe4fc` | asr/asr_service·list_devices·config.json | 本地实时语音转写后端(soundcard→webrtcvad→whisper→原声wav·幻觉抑制) | ✅ 平台面(通用工具·hotwords值实例面) |
| _待提交_ | agent_bus/agents.json + fleet.json | 示例组员5 max硬案daemon注册 + effort口径接线(本文改动2) | ◐ 实例面(genesis舰队/口径) |
| _待提交_ | ui/api·routes·page_body·page_js·ui_smoke(+baseline) | 语音转写 WebUI tab+端点+原声回放 | ✅ 平台面(UI结构需适配) |

> ⚠ 后两行【暂缓提交】:工作树多 agent 并发缠结(page_js/page_body 99% EOL 翻转污染 + api.py 与 MasterUI 的 BI/tray/groups 语义改动同文件 + agents.json 我 json 重格式化)——盲提会扫别人未完成的活/出 EOL 垃圾 diff。待 MasterUI 侧 UI 工作先提交后叠加,再回填本表 commit 号。

---
**整体验收**:吸收后 bus_smoke 43/43 绿 + 平台三问(归属:机制归平台/档位值归业务口径;清除:移 fleet 条目+清 agents.json.effort 即空载;换域:换角色名即用)。
**不可吸收(实例面·跳过)**:`agents.json` 各 role 的 effort 档位值(genesis 口径)、`示例组员5` 角色 + `fleet.json` 该条目(genesis 舰队)、业务库 cwd。
