# AGENTS — agent 管理与任务分派(agent bus)

多个 Claude Code tab(各跑一个 role)互相派活的文件总线。让 worker 在各自 tab 里**活的、可旁观**地执行(子代理做不到)。无服务端,纯文件 + watchdog 事件(不 sleep polling)。

- 代码:`C:\apphome\agent_fleet\agent_bus\`(`agentbus.py` + `ab.cmd` + 自带 README;运行时规范路径 `~/.claude/agent_bus`)。
- 调用:`python C:/apphome/agent_fleet/agent_bus/agentbus.py <cmd>`,或把该目录加 PATH 后 `ab <cmd>`。

---

## ⭐ 让一个 tab "上岗在岗"(标准两步)— 执行靠可见 CLI,不靠 daemon
**架构(2026-06-22 切换)**:任务一律由【可见 CLI tab】执行(人看得见在干活);daemon 只做通知/消息管理(收进常驻 WebUI,某 role 有活无执行体就弹 toast)。执行 daemon 已退役。

**第 1 步(每个 tab 仅一次)报到**:
```
ab hello --role <这个tab的角色名>          # 例:ab hello --role worker-a。绑定「角色↔本tab进程」
```
**第 2 步 上岗**:同一 tab 输入 `/loop`,把下面这段原样贴进去当 prompt:
```
你是 agent bus 上的 worker(已 hello 声明过身份)。每轮只做一件事:
1) 用 Bash(超时 600000ms)运行:
   python C:/apphome/agent_fleet/agent_bus/agentbus.py serve --timeout 590
   它自动认出本 tab 的角色并阻塞等任务。
2) 看输出:
   - {"idle":true,...}  → 本轮什么都不做(loop 自动再起一轮继续等)。
   - {"error":...}      → 还没声明身份,先跑一次 ab hello --role <本tab名>,本轮结束。
   - 任务 JSON(含 id/from/task)→ 严格按 task 执行;完成后运行:
     python C:/apphome/agent_fleet/agent_bus/agentbus.py done --id <那个id> --result "<结果/产物路径>" --status ok
3) 不做任务以外的事。
```
**语义**:`serve` 阻塞守 watchdog 等任务(等待期间几乎不烧 token,最多 ~10min),任务一来自动领取→按 task 执行→`done` 回填→空闲继续守。起了之后 `who` 显示 `✓worker在听`,干活时显示 `▶在做:<标题>`,全程可见。
- **hello = 报到(只登记身份,不接活);/loop = 上岗(自动接活)。** 只 hello 不起 /loop = `已声明(hello),未起循环` = 不会自动执行。
- **铁律**:没法从外部叫醒发呆的会话——idle tab 不会被总线"推"成在岗,必须本 tab 自己起 /loop;没起的,任务留 inbox + WebUI 弹 toast 提醒人去开。
- Master(派活方)/ MasterAgent(基础设施,我)按设计**不起循环**;SQLAGent 维护 sql 代理,非任务 worker。

## 命令速查
```
ab who [--max-age 900]                          # 谁在跑+是谁(presence 心跳 vs 实际 claude 进程)
ab hello --role <本tab名>                        # 本 tab 自报身份(绑定本 tab 的 claude 进程)
ab tag --pid <pid> --label 其他事务              # 按 PID 给非-fleet tab 打标注(默认私有🔒:只存标签,不存路径/备注)
ab list                                         # 各 role 队列深度(inbox/processing/done)
ab register --role X                            # 建 role 目录
ab send --to X --from Master --title "..." --task "..."   # (Master 派任务)打印 task id
ab peek --role X                                # 看某 role 待办(不领取)
ab serve [--timeout 590]                        # (worker 用·通用)自动认出本 tab 角色 → 阻塞等任务(需先 hello 一次)
ab wait --role X [--timeout 110]                # (worker 用·指定角色)阻塞到有任务,领取并打印 JSON
ab done --id <id> [--role X] --result "..." [--status ok|error] [--notify auto|always|never]  # (worker 用)回填;--role 省略自动识别
ab replies --role Master [--clear]              # (Master 收)读回填给我的结果
```

### 新增原语(2026-06-22 落地,见 ARCHITECTURE_REVIEW.md)
```
ab request --to B --from A --task "..." [--chain "..." --timeout 300]  # 发子任务给 B 并【阻塞等 B 应答】(请求-应答);自请求/环/超时三防
ab send ... --requires-approval                 # 标高风险:任何自动监听者(tab serve/daemon/wait)都不会自动领取,留 inbox 待审
ab wait --role X --include-approval             # 有人值守的 tab 显式领取高风险任务(审过后执行)
ab reap [--role X] [--ttl 4000 --grace 120 --dry-run]   # 孤儿回收:owner 进程已退/超期仍卡 processing 的任务退回 inbox
ab supervise [--dry-run]                         # daemon 期望态对账:读 fleet.json 重启已死 daemon + 回收孤儿 daemon(worker_daemon 进程但 role 已不在 fleet);只杀 worker_daemon,前台 CLI/dispatch 不碰。WebUI 每 90s 自动跑 → 从 fleet 摘角色即自动清其残留 daemon
ab prune [--dry-run]                             # 清空的遗留 role 目录(无 presence 且队列全空)
ab dispatch --task "..." [--from X --name N --cwd D --timeout 3600]  # 派【一次性 ad-hoc agent】:有总线沟通(request/send)、不监听队列、干完即逝、可重派;结果回填 <from>/done(replies 收)
ab edge-acquire --who X [--timeout 120 --ttl 300]  # 抢 Edge/云文档操作互斥锁;抢到 {acquired:true},被占且持有者活着→watchdog 等释放(不轮询)、超时 {acquired:false,timeout:true,holder}
ab edge-release --who X [--force]                   # 释放(默认只释放自己持的;--force 强释)
ab caps [--role X]                                  # 能力池:无参看全池+谁持有;--role 看某角色被分配的能力(最小授权)
ab ask-user --from X --title "..." [--detail .. --priority 高/中/低 --ref <任务id>]  # 把【待用户确认】项投进 UI 待办池(不滚走);source=确认·X,capture-only 无回路
ab sessions [--project X --limit N --json]          # 列可恢复 Claude Code 会话(~/.claude/projects/*.jsonl:cwd/aiTitle/最近/大小);给 UI"可恢复会话"视图用;真 resume 在终端 claude --resume <id>
```

### 两类执行体(2026-06-23)
- **持久 role worker**(注册、常驻):由 CLI 启动 daemon(`start_worker.ps1`)或 tab `/loop serve`,监听自己 role 队列、接连多个任务。适合稳定专责角色。
- **一次性 ad-hoc agent**(`ab dispatch`):为单条临时任务即时派一个 comms-capable 执行体,**不注册 role、不监听队列、干完即逝、失败重派**。适合临时活、不想占一个常驻角色。两者都能用 `request` 跟别的 agent 协作。
- **live.log 只读实时视图**(`claude_run.py`):headless daemon / dispatch 跑 claude -p 时,过程**流式写**到 `<role>/live.log`(daemon)或 `_dispatch/<id>.live.log`(ad-hoc)——`tail -f` 即可实时看它的工具调用(🔧)和回复(💬)。虽 2.1.186 不能 attach,过程也可见。
- **request**(worker↔worker 协作):daemon 模式下 B 的 claude -p 执行中可直接 shell 调 `ab request --to C --from B --chain "<我任务里的 chain 逗号拼接>"` 去问 C 并等。一个 worker 阻塞在 request 期间不再 serve 自己 inbox(要么做活含等子结果、要么在听)。
- **requires_approval(统一闸)**:写库/外发等高风险任务,Master 派活时加 `--requires-approval`。闸在【领取路径 `_wait_core`】统一实现——所有自动监听者(tab `serve` / daemon / 普通 `wait`)都不会自动领取,任务留在 inbox(`who`/`peek`/WebUI 可见)等人工处理。有人值守的 tab 用 `wait --include-approval` 显式领取执行。**对 tab worker 和 daemon 一致生效,无需重启 worker(都 shell 最新 agentbus.py)。**
- **完成通知**:done 默认 `--notify auto` → error 必弹 toast、Master 完成弹(45s 节流);嫌吵用 `--notify never`。
- **BI 资源消耗模块(WebUI,2026-06-23,v1.3)**:WebUI 顶部「📊 BI · 资源消耗」面板,运营总览口径看全舰队 LLM 开销 + 系统资源。**埋点**:claude_run 从 claude -p 的 `result` 事件抓 `usage`(token in/out/cache)+ `total_cost_usd` + `duration_ms`,daemon/dispatch 任务完成后 `ab.record_metric()` 追一行到 `bus/metrics.jsonl`(append-only,O_APPEND 原子)。**聚合**:`/api/bi`(server.build_bi)按角色/全队聚合今日 + 近24h 的任务数/token/成本/平均耗时,叠加 psutil 进程树 CPU%/内存快照(daemon_pid 整树,含其 fork 的 claude -p 子进程)+ 当前在跑任务。前端每 10s 刷。**数据自埋点上线起累积,无历史**;`metrics.jsonl` 只本机。改了 claude_run/daemon 须重启 daemon 才生效(`supervise`)。**任务活动历史(v1.4)**:token/成本无法回填(以前没测过),故另列「📜 任务活动历史」——`server.build_activity()` 从 `events.jsonl` 配对 claim→done 实时重建各角色全程 任务数/近24h/ok·err/平均耗时/最近活跃(过滤 `__*`/`zz*` 测试角色),诚实区分"消耗"(token/$,前向)与"活动"(计数,全程)。同在 `/api/bi` 返回(`activity` 键)。
- **能力池 + 新人 onboarding(2026-06-24,v1.5)**:agent 按专项【最小授权】。能力池 = `agent_bus/capabilities.json`(bus-comms / self-wake / confidentiality / external-tool-edge / sql-fetch / high-risk);每个 role 在 `agents.json` 有 `caps:[...]` 只列它需要的能力(`ab caps [--role X]` 查)。**铁律:external-tool-edge 只给【对外 agent】,业务 agent 不直接操作云文档——要外发就把内容交给对外 agent**(对外 agent 待建;届时 Edge 只它一个碰,edge-lock 争用基本消失)。**新人 onboarding**:① `hello` 输出自带自醒说明(v1.4.6);② 专设常驻 **`培训师`** role(在 fleet,手动触发)——`ab send --to 培训师 --task "带一下 X"`,它按 `agent_fleet/BUS_ONBOARDING.md` 流程:先握手验证 X 自醒通信、再按 X 的 caps **只发对应能力模块**、回报。培训师只管总线协议层,业务口径归业务老 agent。
- **edge-lock(Edge/CDP 焦点互斥,2026-06-23,v1.2)**:同一时刻只能一个 CDP 客户端操作 Edge(抢焦点会串台)。**铁律**:凡要操作 Edge/云文档(复制/点击/粘贴/编辑)的活,包成 `edge-acquire --who <自己>` → `connect_over_cdp 操作` → `edge-release --who <自己>`。抢不到(timeout)就稍候重试或回报忙;务必 release(或靠 ttl 兜底)。机制:`bus/edge.lock`(`O_EXCL` 原子抢),stale 回收=持有者进程死 **或** 锁超 ttl(默认 300s)→ 下一个抢的人自动偷锁、不死锁;`who`/`recover` 末尾显示当前持锁者。worker_daemon / dispatch 的任务 prompt 已内置这条(碰 Edge/云文档才抢,daemon 下次重启生效)。**只解决①串行有序止血**;②独立 9223 profile、④官方 API 治本未做(按需排期)。
- **审计**:所有 send/claim/done 自动记一行到 `agent_bus/events.jsonl`(全链路可追溯,clear replies 后仍留痕)。

> `who` 判据:`mode=listening`(来自 wait)且心跳没超 wait 周期(+60s)→ `✓ worker在听`;
> 只 `hello` 过 → `已声明(hello),未起循环`;心跳超期但进程在 → `⚠ worker静默`(用长 timeout 阻塞监听时可能误报,以 list 里任务是否进 processing 为准);进程退 → `残留`。

## agent 管理:发现 + 识别(关键认知)
- OS 里所有 `claude.exe` **同名同命令行**,系统只能数"有几个在跑",**识别不出谁是什么 role**。
- 身份只能**会话自报**:`hello` / `wait` 向上回溯父进程链找到本 tab 的 claude 进程,把 `role↔pid↔cwd` 写进 `<role>/presence.json`(含 last_seen 心跳)。
- **铁律**:一个会话只能声明自己是谁,无法知道你给它 tab 起的名 → 在**每个 tab 各跑一次 `ab hello --role <该tab名>`**;worker 起 `/loop` 后 `wait` 会自动声明。在错 tab 跑 hello 会贴错身份。
- `ab who` 判定:`✓活的worker / 活着没在听 / 进程已退残留 / 未声明`,并列出在跑但没声明的进程。

## 作为 Master 派活(标准流程)
1. `ab who` 看目标 role 是不是 `✓ worker在听`(没在听就先让那个 tab 起 /loop)。
2. `ab send --to <role> --from Master --title "..." --task "<自包含指令,含产出位置+约束>"`。
3. 过程:任务 inbox→processing(worker 领走)→ worker 干 → done 回填到 `Master/done/`。
4. `ab replies --role Master` 收结果(可 `--clear` 读后清)。
5. 改投:目标没在听时,可把任务删掉重 `send` 给在听的合适 role(见 SQLAGent 边界)。

## worker tab 启动(自动执行模式)— 通用 loop,所有 worker tab 同一段
前提:该 tab 先 `ab hello --role <本tab名>` 一次(声明身份)。然后输 `/loop`,prompt 原样贴:
```
你是 agent bus 上的 worker(已 hello 声明过身份)。每轮只做一件事:
1) 用 Bash(超时 600000ms)运行:
   python C:/apphome/agent_fleet/agent_bus/agentbus.py serve --timeout 590
   它自动认出本 tab 的角色并阻塞等任务。
2) 看输出:
   - {"idle":true,...}  → 本轮什么都不做(loop 自动再起一轮继续等)。
   - {"error":...}      → 还没声明身份,先跑一次 ab hello --role <本tab名>,本轮结束。
   - 任务 JSON(含 id/from/task)→ 严格按 task 执行;完成后运行:
     python C:/apphome/agent_fleet/agent_bus/agentbus.py done --id <那个id> --result "<结果/产物路径>" --status ok
     (done 不填 --role,自动识别;失败用 --status error,错误写进 --result)
3) 不做任务以外的事。
```
> `serve` 靠"本 tab 的 claude 进程 pid ↔ presence.json"反查角色,所以同一段 prompt 在任何 worker tab 都成立,无需改 role。

## 执行模型(2026-06-22 切换)— 可见 CLI 执行 + WebUI 通知,执行 daemon 已退役
用户定调:**任务一律由【可见 CLI tab】执行(跑 `/loop serve`,人看得见在干活);daemon 只做通知与消息管理,不再 headless 执行**。原因:headless `claude -p` daemon 不可观测、会半途 silent 死、还截走本该人监督的任务(实测踩过多次)。
- **执行 = 可见 tab**:在该 role 的 tab 里 `hello` 一次,再 `/loop` 跑上面那段 serve prompt。`who` 显示 `✓ worker在听`,且 claim 后显示 `▶ 在做:<标题>`(current_task 可视)。
- **通知/消息管理 = 常驻 WebUI**(127.0.0.1:8799):某 role 来了新任务但【没有活的执行 tab】→ 自动弹 WinRT toast「📥 X 有新任务待处理」提醒人去开/处理;完成通知由 `done` 端 toast。不另起需看护的进程。
- **执行 daemon 已全部退役**;`fleet.json` 清空、`supervise` 成 no-op。`worker_daemon.py`/`start_worker.ps1`/`stop_worker.ps1` 仅作"特殊场景显式后台执行"的保留件(默认不用)。如确需无人值守后台执行某 role,才 `start_worker.ps1 -Role X -Cwd ...`(用正斜杠 cwd 避免 bash 吞反斜杠)并在 fleet.json 登记——但这违背"可见执行"原则,谨慎。
- 历史坑(保留备查):.ps1 必须 ASCII;daemon prompt 走 stdin 不走 argv;daemon 跑改 daemon 的任务会自杀(对自身 stop_worker)。

## 协调层交流协议(2026-06-23):后台 wait 自醒(回调唤起)+ 兜底自查
协调位(Master/MasterAgent)**不 loop、不起 daemon**(给协调位起 daemon 实测不稳且会抢它的活),但要能收 Master↔MasterAgent 的消息。**正解:用"自己启动的后台任务完成→回调唤起本会话"做事件自醒**:

**机制**:协调位**挂一个后台 `agentbus wait --role <自己> --timeout 3600`**(run_in_background)阻塞等自己 inbox;消息一到 → 该后台任务完成 → **harness 用 task-notification 回调唤起本会话** → 读出消息处理 → **再挂一个后台 wait 继续等**。于是【可对话 + 事件自醒】兼得:后台任务不锁对话、消息一来就把我唤起处理,**不用 toast 喊人、不用 daemon、不用 /loop 锁对话**。
- 触发是"**自己启的后台任务完成**"(协调位合法唤醒途径之一),不是"外部消息直接唤醒 idle 会话"——这是绕过"叫不醒 idle 会话"那堵墙的正解。把人喊回来(toast)不是本意,只作可选旁路。
- **每次被唤起、处理完,必须重新挂一个后台 wait** 保持耳朵开着(否则后续消息就收不到)。
- **兜底**:用户开口唤起时也先 `peek` 自己 inbox(防后台 wait 恰好在 idle-timeout 间隙)。
- **协调位之间用 `send`(异步),不用 `request`**(对方的 wait 会领走应答路由,或不在)。
- 注意:后台 `wait` 会 claim 进来的消息(移到 processing),处理完按需 `done`;高风险任务(requires_approval)wait 默认跳过、留 inbox。
- **完成通知触发自醒(v1.1)**:`done` 回填给一个【自醒在岗的协调位】(Master/MasterAgent)时,会中心化地往其 inbox 投一条 `kind:"notice"` 轻量完成通知 → 触发其后台 wait 自醒 → 它醒来读 `<self>/done/<id>` 取完整结果。于是"派活→worker 干完→发起方自动被唤起"闭环,不用人盯 done。worker 无需改(集中在 `agentbus.done`)。防环/防噪:只对自醒协调位投、`sync` request 应答不投、`--notify never` 不投、notice 的 done 因 from=worker 不再触发 notice。
- **收到 `kind:"notice"` 的处理协议**:读 `notice_of` 指向的 `<self>/done/<id>.json` 取完整结果 → `done` 掉该 notice → **别把它当普通任务执行、别回灌**。

## 责任边界
worker 自动执行收到的任务。**高风险(写库/外发)** 派给"先审后做"的 tab,或在 task 里要求 worker 先 EXPLAIN/确认;SQL 写库仍走 sql_agent 审批协议。

## 角色与边界(2026-06-22)
- **MasterAgent**(基础设施层,"对 Master 自身的 Master")— **只演进 agent_fleet 这套协调基础设施本身**:agent bus / 协调 WebUI / worker daemon / 完成通知机制 / 记忆备份。cwd 在 `C:\apphome\agent_fleet`。`--from MasterAgent`,`replies --role MasterAgent`。**职责协议:每升级一次基建,主动 `send --to Master --from MasterAgent` 简报"升了什么+怎么用"**——Master 是把任务外派给业务 agent 的消费方,要据此改派活方式。
- **Master**(业务总控)— 处理其他事务,**把业务任务外派给各业务 agent 并收结果**;cwd 在 `XXX`。`--from Master`,`replies --role Master`。是 MasterAgent 升级基建后的"使用方"。
- **SQLAGent** — **维护 sql_agent 代理(server.py)那个 cli**,不是通用查库 worker;**别给它派查询活**。查库由各 worker 自己走 sql_agent 协议(见 CAPABILITIES.md #1)。
- **"业务Agent"** — 各自专责 worker。

> worker 自动执行收到的任务;高风险(写库/外发)派给先审后做的 tab,或在 task 里要求先 EXPLAIN/确认。

## 隐私:其他事务🔒(私有标注)不暴露
`其他事务`(及任何 `private` 标注的 PID)是**与本 fleet 无关的私事 tab**。铁律:
- **不探**:Master / who / recover **不得** psutil 取其 cwd、不读其会话/transcript、不报其在做什么。
- **不存**:`pid_tags.json` 对私有标注只留 `label/private/时间`,**绝不存路径或细节**。
- `who` 只显示 `pid=标签🔒`。符合用户隐私协议(个人/无关内容不沉淀、不进可见输出)。
