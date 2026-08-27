# BUS_GUIDE — agent bus 使用说明(可查·单一入口)

> **这是什么**:bus 的**心智模型 + 导航页**,一处看懂【角色 / 机制 / 进程 / 管理 / 思考】。
> **怎么用**:**live 数据一律跑命令看**(`ab roster` / `ab who` / `ab health` / `ab caps` / `ab commands`),本页只给**稳定结构**,不硬编易变数据(防漂移·数据真实性)。详情链到各权威文档。
> **不上云**:本页本机可查(Read / 将来 WebUI 127.0.0.1),**不发 Artifact / 不外发**(数据不出本机红线)。
> **状态**:v1(Master数分 起草 + MasterAgent 补权威·2026-08-27)。§3 机制/§1 角色权威内容已补(MasterAgent);§2/§5/§6 实测+接续(Master数分)。**待办**:WebUI 化承载(MasterUI)。

---

## 1. 角色与分工

**live 花名册**:`ab roster`(在岗/caps/专长)· `ab caps [--role X]`(能力池)· `ab who`(进程实况)。**别信硬编,以命令为准。**

**管理层(3 位·都协调不执行业务)**:
| 角色 | 定位 | cwd | 形态 |
|---|---|---|---|
| **Master** | 业务总控·派业务给各 agent 收结果 | 业务库 | 交互 CLI·不 loop |
| **MasterAgent** | 基建演进(bus/UI/daemon/通知/记忆) | agent_fleet | 交互 CLI·不 loop |
| **培训师** | 新人 onboarding(只管协议层) | agent_fleet | 常驻 daemon |

**其他协调/支撑位**:**Master数分**(数据/口径/验证 + 档案清理 owner)· **MasterUI**(WebUI 127.0.0.1:8799 开发)。
**业务/执行 agent**:数据分析(示例组:示例组员2/3/5)· 对外通道(云文档 CDP)等 —— 见 `ab roster`。
**分工铁律**:改了接口/机制/契约 → **主动通报消费方**(对称协作律);跨领域活先 `ab route <关键词>` 找对口、别自揽。

---

## 2. 执行体与进程(含资源画像·实测)

**四种执行体**:
| 执行体 | 是什么 | 上下文 | effort 可注入? | 空闲内存 |
|---|---|---|---|---|
| **前台 CLI**(主流) | hello + 后台 wait 自醒(或 /loop serve) | **常驻热·跨轮保留** | ✗(已运行进程注入不到)→**吃全局** | **高**(claude.exe) |
| **daemon** | worker_daemon·fleet.json·supervise 维持 | 每任务冷起·无状态 | ✓(启动器带 --effort) | **低**(python.exe) |
| **dispatch** | 一次性 ad-hoc claude -p·干完即逝 | 每任务冷起 | ✓(`ab dispatch --effort/--model`) | 任务期才有 |
| **wait** | 事件自醒(watchdog 阻塞) | 无 | — | **低** |

**资源画像(实测 2026-08-27)**:
- 交互 CLI(`claude.exe`)= **341–469MB/个**(运行时 + 活会话上下文 + TUI 常驻);20 tab ≈ **6.5GB**。
- wait/daemon(`python.exe`)= **15–30MB**(示例组员5 daemon 空闲 = 25.7MB·**CPU 0.0%**·无 claude 子进程)。
- **口诀:阻塞省的是 CPU(≈0),不是内存;内存 = 进程里装着什么。** daemon 空闲 = 0 token(claude -p 每任务起、干完即逝);成本只在真干活时发生。

**该用哪个**:**长线协调/管理 → 前台 CLI**(需跨轮状态 + 交互 + 可观测);**离散/无状态执行(尤其低频硬活)→ daemon/dispatch**(每任务干净 + effort 可注入 + 空闲省)。协调位**不起 daemon**(实测不稳且抢活)。

---

## 3. 机制

**单一源**:`bus_mechanisms.json`(**96 机制 / 13 类**·机制注册表)——取代散落各提示词/代码/记忆的机制描述。**看全量**:WebUI 🔧机制 tab(127.0.0.1:8799)或读该文件;本节只给**分类地图**导航,不逐条 dump(防漂移)。
> 机制注册表 ≠ 能力池:**caps**=agent 被授权做什么(粗·路由+最小授权·培训师管);**机制**=bus 提供什么功能(细·MasterAgent/Master数分)。

**根基原语**(贯穿所有机制):`O_EXCL` 原子竞争(claim/edge/组 task 单胜者)· `watchdog` 事件驱动(零轮询)· `os.replace` 原子写 · `PYTHONUTF8`(GBK 兼容)。

**13 类地图**(每类代表机制;详情→注册表):
| 类 | 代表机制 |
|---|---|
| **消息/任务流与队列** (8) | inbox/processing/done 队列 · send/wait/done/request/peek/replies · answer 待办回路 |
| **自醒与可靠性** (19) | hello/serve · presence 心跳 · `_self_wake_state` 四态 · **wait 注册表** `wait.reg.json` · 宿主自检自杀(治孤儿) · 重挂 yield-not-kill · block 真无限(`--timeout 0`) · 竞争裁决走 reg · selfwake-guard 掉线报警 · 完成通知触发自醒 · park/unpark |
| **工作组池化** (7) | join/leave/send-group · `_claim_group_task` 组任务原子领 · shared 契约 · fanout-collect |
| **健康/回收/监控** (12) | `ab health` 总检 · `supervise` fleet 对账(WebUI 90s) · `reap` 孤儿回收 · health-sweep 消息丢失处置 · who/list · prune · metrics.jsonl BI 埋点 · mem-cap 并发上限 |
| **角色/能力管理** (12) | register/retire/revive/retired · caps 授权 · roster 花名册 · route 对口路由 · ask-user 待办池 · 培训闸 · hello 喂角色页 |
| **WebUI 与通知** (8) | server.py · WinRT toast(分级/节流) · turn_state hook · 插话纠偏锚 hook · bus_tray 托盘 |
| **Edge 互斥/云文档** (5) | edge-acquire/release · 锁陈旧判断 · watchdog 等释放 · 锁主活检 |
| **派遣与临时执行** (6) | dispatch 一次性 · worker_daemon · claude_run(claude -p 流式) · requires_approval 闸 · **effort+model 注入** · **daemon 跨任务接续**(见 §6) |
| **代码持久与配置** (8) | cfg/config.json · `_write_json` 原子 · PYTHONUTF8 · `_audit`→events.jsonl · fleet.json · pid/session_tags |
| **长期任务线与待办** (5) | tasklines.json · todos.json · 决策点 · stale 停滞防丢 |
| **进程与身份** (6) | `_proc_scan`/`_claude_ancestor`/alive/orphan 判活 · webui-guard |
| **架构/恢复与文档** (4) | recover.py · CLAUDE.md 自举 · 会话可靠性硬化主线 · 池化 |
| **内部协议与守卫** (3) | run_in_background · block_askuser hook(硬拦 AskUserQuestion 转待办池) · `_is_bus_member` 门禁 |

> ✓ **本轮新增已同步注册表**(2026-08-27·按 freshness_rule):派遣类 +2 = **effort+model 注入**(commit 28306a3)· **daemon 跨任务接续**(层A 落档重读 + 层C `--resume`·commit f219e82·详见 §6)。注册表 `meta.total` 94→96、`last_updated` 已记。

---

## 4. 管理与协调

- **协调位交流**:不 loop、挂后台 `ab wait --role <自己> --timeout 0` 事件自醒;完成通知(`kind:"notice"`)自动唤起发起方。
- **派活流程**:`ab send --to X --from Y --task "<自包含指令+产出位置+约束>"` → inbox→processing(worker 领)→ worker 干 → `done` 回填 → `ab replies --role Y` 收。
- **路由优先·广播兜底**:先 `ab route <关键词>` 找对口 send;仅跨域/无归属才广播。
- **待办池(需用户拍板)**:一律 `ab ask-user`(非阻塞·投 UI 待办池),投完继续别等;**禁 AskUserQuestion**(阻塞占会话·hook 已硬拦转投)。
- **自醒铁律(处理与重挂顺序)**:被唤醒/接活/compact 后 **①先排空 inbox(peek 领完→处理→done)→ ②再重挂 wait → selfwake 验 ✓在岗 → ③才开新任务**。根因:wait 起手即 claim 已有消息,先挂再埋头 = claimed 悬单烂 processing。**不重挂 = 掉线**;空唤醒 → 静默重挂 + 最多一行(别重 dump 状态)。
- **失能防线**:长命 CLI 必配 **工作态落档 + 重读**(compact 后 reinject 重定位)—— 否则 compact 有损 → 失能。

---

## 5. 思考 / effort 格局

- **模型**:Opus 4.8(`opus[1m]`)= 自适应思考默认开,不需手动打开;调的是**深度 = effort**(low/medium/high/xhigh/max)。`budget_tokens`/`MAX_THINKING_TOKENS` 已废,effort 取代。
- **注入面**:effort **只在 daemon/dispatch 面能 per-role/per-task 注入**(启动器带 `--effort`);**交互 tab 注入不到 → 吃全局 `~/.claude/settings.json` effortLevel**。故全局 effort 是管理层(交互 tab)的**唯一旋钮**。
- **优先级**:dispatch 任务级 `--effort` > `agents.json[role].effort` > 全局 `effortLevel`。
- **分级口径(Master数分 owner·见记忆 effort-tiering-by-role)**:
  - **xhigh**:MasterAgent / MasterUI / Master(繁重)
  - **high**:Master数分 / 示例组员2 / 示例组员3
  - **medium**:培训师
  - **硬案 max**:**示例组员5**(示例组复制体·effort=max 常驻 daemon)专跑多层深根因(5+ 探测);硬案路由给它=自动 max,不拖其他组员。ad-hoc 备选=`ab dispatch --effort max`。
- **口径原则**:effort 跟"该 role 每轮要不要多步推理"走;深根因主靠**方法(逐层证伪 + 对抗验证 + 定性必实证)**,max 只抬"愿意继续探"的上限、且双刃(prone to overthinking)。

---

## 6. daemon 接续上一任务 —— 上下文管理

**原则:绝不让 daemon 在进程内跨任务背上下文**(那就丢了它省/可注入的全部好处)。接续**外置**,三层:
- **A|工作态落档 + 重读(默认·已落地 commit f219e82)**:前任务完成时把**提炼工作态**落 `agent_bus/_workstate/<thread>.json`(schema 见该目录 README:summary/next/findings/artifacts/session_id);派接续任务带 **`--continue-from-state agent_bus/_workstate/<thread>.json`**(`ab send/dispatch`)→ worker_daemon/dispatch 自动前置"先读该文件恢复上文再干"(`claude_run.resolve_continue`),**不进程内背全上下文**。省(只重读提炼态)+ 稳(扛进程死/compact/重启)。
- **B|CLI 协调位持线程 + 传指针**:派活的 CLI(Master/MasterAgent)是跨任务的有状态记忆,把状态文件指针塞进接续任务 payload。**有状态协调在 CLI,daemon 只执行离散步骤读写持久态。**
- **C|`--resume` 全上下文(已落地·贵·仅 A 不足时)**:**`ab send/dispatch --resume-session <session_id>`** → `claude -p --resume` 全上下文接续;session_id 从上轮 dispatch 回执的 `session_id` 字段取(run_streaming 从 stream-json 捕获回流)。实测:两次独立 claude -p 经 --resume 接上暗号「紫色河马42」=全上下文真续。
> **口径(选型归Master数分)**:**默认 A**(落档重读·省);A 提炼态不够细节 → **C**(--resume 全上下文·贵)。协调靠 B(CLI 持线程)。机制/字段/CLI 归 MasterAgent(commit f219e82·bus_smoke 43/43)。

---

## 7. 命令速查

**live**:`ab commands`(全命令)· `AGENTS.md` 命令速查节(带说明)。高频:
`ab whoami / hello / wait / selfwake / peek / done / send / replies / roster / route / caps / who / health / dispatch / ask-user`。

---

## 权威源与分工

**详情文档**:`AGENTS.md`(派活/管理)· `CAPABILITIES.md`(通道:sql_agent/CDP/Airflow/记忆)· `BUS_ONBOARDING.md`(带训流程)· `bus_mechanisms.json`(机制)· `role_profiles.json`(角色页)· `agents.json`/`fleet.json`(注册/daemon 池)。
**本页分工**:进程/资源/effort/接续节 = Master数分(本轮实测产出);机制/角色权威内容 = MasterAgent 补;WebUI 承载 = MasterUI。**live 数据以命令为准,本页只给稳定结构。**
