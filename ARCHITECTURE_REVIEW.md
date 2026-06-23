# agent_fleet 架构评审 + 改进提案

> 作者:MasterAgent(本套多-agent 架构的演进/自我改进角色)
> 日期:2026-06-22 · 范围:agentbus 文件总线 + daemon + WebUI + 自恢复体系
> 真相源:已通读 agentbus.py / worker_daemon.py / start_worker.ps1 / stop_worker.ps1 / ui/server.py / recover.py / CLAUDE.md / CAPABILITIES.md / AGENTS.md / 两条核心记忆 + hook。

---

## ✅ 落地状态(2026-06-22,Master 授权后已实现 + 自测)
评审完成后,Master 授权"全部落地"。下列改进**已实现并端到端自测通过**(全程增量、每步 import+冒烟、未破坏在跑舰队):

| 项 | 内容 | 命令/文件 | 关键自测 |
|---|---|---|---|
| **#1 孤儿回收器** | `reap`:owner 进程已退(非心跳)且超 grace,或超 ttl(4000s) → 退回 inbox(requeue_count),毒任务(超3次)→ `failed/` | `agentbus reap [--dry-run]` + `_owner_live`/`_daemon_alive` | 造孤儿→退回;**dry-run 当场逮到正在写外部消息的 in-flight 任务,验证"按进程判活"修复(否则会重复执行)** |
| **#2 请求-应答** | `request`:send 子任务 + 阻塞等 `<from>/done/<id>`,读后消费;自请求拒/chain 环检测/强制超时三层防死锁;done 零改动 | `agentbus request --to --from --task [--chain --timeout]` + `_wait_for_reply` | 自请求拒、环拒、端到端 A↔B 同步应答、超时分支 |
| **#3 完成通知** | done 端分级 WinRT toast:error 必发 / Master 完成发+45s 节流 / sync 应答不弹 / worker 互发不弹;`--notify auto\|always\|never` | `_maybe_toast`/`_fire_toast` 并入 `cmd_done` | 7 gating 用例全对 + 真 toast 验证 + 旧式 done 回归 |
| **#4 路径外置** | 机器相关路径集中 `config.json`(env 可覆盖);worker_daemon/server/recover/hook 全改读;**修正"迁 agent_fleet 即全部"名实不符**(CLAUDE.md 加迁移清单) | `agent_bus/config.json` + `cfg()` | cfg/env/fallback + 4 模块解析 + 全回归 |
| **#5 自愈+互斥** | `supervise` 读 `fleet.json` 重启已死 daemon(接进常驻 WebUI 90s 线程);start_worker 对已有活 tab-loop 的 role `REFUSE` | `agentbus supervise` + `fleet.json` + start_worker 守卫 + server `start_supervisor` | 杀→复活;互斥拒绝;WebUI roll 后 200 |
| **#6 收尾** | `events.jsonl` 审计(send/claim/done);`prune` 清空遗留 role;**`requires_approval` 统一闸**(领取路径 `_wait_core` 实现:tab serve/daemon/wait 都不自动领高风险,留 inbox 待审;`send --requires-approval` 标记,`wait --include-approval` 人工领);`reap` 接入 recover 巡检 | `_audit` / `cmd_prune` / `_oldest_claimable`+`_wait_core` 闸 / recover [2.5] | 审计 3 行;prune 清 unknown;**统一闸实测:普通 wait 跳过高风险领普通、只剩高风险则 idle 留 inbox、--include-approval 才领;tab+daemon 一致** |

**本轮暂缓(低价值/高 churn,待 Master 点单再做)**:`current_task` 写进 presence(可观测)、done/archive 定期 GC、`done --requeue` 自动重试。
**部署说明**:以上全部**向后兼容、未改既有 send/wait/done 语义**;新命令对在跑 worker 即时可用(它们 shell 调最新 agentbus.py)。仅滚动重启了 MasterAgent daemon(加载 requires_approval 闸)和 WebUI(加载 supervisor 线程),**未动业务 daemon**。worker 间 `request`/高风险 `requires_approval` 的使用说明待并入 AGENTS.md。

---

## 1. 架构全景

### 一句话定位
**一套跑在本机文件系统上、完全可观测的"多个 Claude CLI 会话之间"的任务分派总线。** 它把"多个各司其职的交互式 tab"变成 Master 可编排的 worker 队列。
- **它解决什么**:用户嫌子代理(subagent)不可观测、不可旁观;想让每个 role 在自己的活 tab 里"看得见地"干活,且 tab 之间能互相派活、收结果。
- **核心硬约束(决定了全部设计)**:**无法从外部唤醒一个 idle 的交互式会话**。所以不能"推"任务给会话——只能让会话主动"阻塞监听"(tab 里 `/loop`+`serve`,或无窗口 `daemon`),任务到了它自己醒。这是整套架构所有形态的根因。
- **边界**:单机、单用户、本机文件系统信任域;无服务端、无网络传输、无鉴权。

### 组件清单
| 组件 | 角色 | 关键文件 |
|---|---|---|
| **总线核心** | CLI:register/send/wait/serve/done/peek/replies/list/hello/who/tag | `~/.claude/agent_bus/agentbus.py` |
| **无 tab worker** | 隐藏后台守护,阻塞 watchdog 等任务,来活临时拉 `claude -p` 跑完回填 | `worker_daemon.py` + `start/stop_worker.ps1` |
| **可视化控制台** | stdlib HTTP,复用 agentbus 数据模型,watchdog→SSE 实时推 | `agent_fleet/ui/server.py`(常驻 127.0.0.1:8799) |
| **冷启动探针** | shell 出 who/list/replies + 探 Edge 9222 + 打印坐标 | `agent_fleet/recover.py` |
| **自恢复文档** | 入口/通道/agent管理说明书 | `CLAUDE.md` / `CAPABILITIES.md` / `AGENTS.md` |
| **记忆硬链备份** | 项目记忆 .md 硬链镜像进 agent_fleet;PostToolUse hook 自动重链 | `memory/` + `hooks/on_memory_write.py` + `sync_memory.ps1` |

### 数据模型(全是文件)
```
~/.claude/agent_bus/
  agents.json                    role 注册表
  pid_tags.json                  非-fleet 进程隐私标注(private 只存 label)
  <role>/inbox/<id>.json         待领任务(send 落这,status=pending)
  <role>/processing/<id>.json    已领、正在做(claim 后移到这)
  <role>/archive/<id>.json       做完归档(最接近审计日志)
  <role>/done/<id>.json          别人回填给"我"的结果(我是发起方时收这)
  <role>/presence.json           身份+心跳(claude_pid/cwd/last_seen/mode)
  <role>/daemon.log              daemon 模式的运行日志
```

### 数据流 / 生命周期
1. **send**:`_write_json`(写 .tmp → `os.replace` 原子落地,避免 watcher 读半截)到 `<to>/inbox/<id>.json`。id = 毫秒时间戳-role-随机 hex,**文件名即 FIFO 排序键**。
2. **claim(原子领取)**:`os.replace(inbox→processing)`。多监听者(tab loop + daemon)并存时**只有一个 rename 成功,抢输者 FileNotFoundError → None**,所以绝不重复执行。
3. **执行**:tab loop 在同一会话内连续做(上下文累积、warm);daemon 每个任务**临时拉一个全新 `claude -p --dangerously-skip-permissions`**(cwd=XXX,冷启动、无跨任务记忆)。
4. **done**:结果写 `<from>/done/<id>.json`,原任务带 status/result 归档到 `<role>/archive/`,processing 文件 unlink。
5. **收结果**:Master `replies --role Master`(可 `--clear`),或 WebUI D 区。
6. **身份**:`hello`/`wait`/`serve` 向上回溯父进程链找到本 tab 的 `claude.exe` pid,写 presence.json(`claude_pid↔role↔cwd`+心跳)。`serve` 反查"本 tab pid↔presence"自动认出角色,故所有 worker tab 同一段 prompt。
7. **探活**:`who`/recover/WebUI 把 presence 心跳 × 实际在跑 claude 进程交叉核对 → `✓在听 / 已声明未起循环 / ⚠静默 / 残留 / 未声明 / daemon在听/已退`。

---

## 2. 优点 / 脆弱点 / 风险(逐条失败模式)

### 做得好的地方
- **原子性扎实**:写用 tmp+os.replace,领取用 os.replace 抢占,两处都对——并发领取不会重复执行,半截文件读不到。这是整套设计最硬的一块。
- **真·事件驱动**:wait/serve/WebUI 全用 watchdog,无 sleep polling,符合全局规则;`_wait_core` 在 observer 起来后**再查一次 inbox 消除竞态**,细节到位。
- **零逻辑重复**:WebUI、recover 都复用 agentbus 的 helper(import 或 shell-out),没有第二套真相。
- **身份机制诚实**:正视"OS 里所有 claude.exe 同名,识别不出谁是谁"这一事实,用"会话自报+父链回溯+心跳"解决,`who` 判据分级清楚。
- **隐私边界落到代码**:private 标注只存 label,who/WebUI 对 private 进程不 psutil 探细节,不是只写在文档里。

### 脆弱点与失败模式(对应任务里的逐条拷问)

**① daemon 崩了谁拉起?——没人。**
`start_worker.ps1` 幂等但**必须人工调用**。daemon 进程本身**没有任何看护**:崩了 presence 显示 `✗ daemon已退`,但不会自动重启。对比 WebUI 已经拿到了计划任务自动重启(RestartCount 999)的待遇,**daemon 反而没有**。→ 这是可靠性上的不对称缺口。

**② claude -p 冷启动慢 + 被项目记忆带偏——真实且已踩过。**
每个任务拉全新 `claude -p`:要重载 CLAUDE.md + 记忆索引 + 工具定义 + MCP 初始化,**冷启动数秒级**,且每任务都付一次全量上下文重建的 token。更隐蔽的是**语义带偏**:项目 CLAUDE.md/记忆里写着"你是 bus worker、去 serve 取任务",daemon 拉起的 claude -p 会被带偏去起 loop/取任务——已知坑,目前靠 prompt 里硬写"只做内联任务、别碰 agentbus"压制。**这是"靠 prompt 对抗自身记忆"的脆弱平衡,记忆一改可能复发。**

**③ 原子领取在崩溃/超时下够不够?——领取够,回收不够。**
原子领取只保证"不重复执行",**不保证"领了一定做完"**。致命缺口:**任务进了 `processing/` 后,如果 worker tab 崩溃 / 关机 / 卡死,这个任务永远卡在 processing,没有任何机制把它退回 inbox 或重派。** 没有 lease/TTL、没有 reaper、没有 requeue;`who`/recover 只显示 processing 计数,**不做孤儿检测**(不会告诉你"这条 processing 的 owner 已经死了")。
- daemon 路径稍好:`claude -p` 自带 3600s timeout,超时会 `done --status error` 自己收尾——但**超时后子 claude 进程可能没被杀掉**(Windows subprocess 超时不可靠杀孙进程),留僵尸。
- tab /loop 路径:tab 崩在任务中途 = 永久孤儿,无人回收。

**④ presence 在 tab 与 daemon 并存时翻动——会,且无强制拦。**
同一 role 既起了 tab `/loop` 又起了 daemon:两者都往同一个 presence.json 写心跳(一个 mode=listening/claude_pid,一个 mode=daemon/daemon_pid),**互相覆盖 → who 判定在"worker在听"和"daemon在听"之间跳变**;同时两者抢同一 inbox(原子领取防了重复执行,但有一个是纯浪费)。记忆里写了"二选一",但**代码不强制**:`start_worker.ps1` 只检查"有没有已在跑的 daemon",**不检查"有没有活的 tab loop"**。

**⑤ 跨机迁移后路径/凭据失效——且"迁 agent_fleet 即带走全部"的说法名实不符。**
硬编码绝对路径遍布:`CLAUDE_BIN=C:\Users\<用户>\...\claude.cmd`、`DEFAULT_CWD=C:\BaseSpace\...`、server.py 的 `ABDIR=C:\Users\<用户>\.claude\agent_bus`、hook 的 `SYNC` 路径。换机/换用户名全断。
**更要紧的认知纠偏**:文档说"只迁移 agent_fleet 文件夹即带走全部",但**真正的总线(agent_bus:代码、presence、队列)在 `~/.claude/`,不在 agent_fleet**;agent_fleet 里只有记忆 .md 的硬链镜像。所以"带走 agent_fleet"=带走记忆,**不等于带走总线**。这是文档承诺与现实的偏差,迁移时会咬人。

**⑥ 隐私边界——基本稳,一处可改进。**
private 标注只存 label、不探细节,who/WebUI 都守住了。唯一可改进:**未被 tag 的私事 tab** 会以 `未声明 [pid]` 出现在输出里(虽然不含 cwd),需要人工记得去 tag。属于流程而非代码漏洞。

**⑦ 每任务起 claude -p 的 token/时延成本——daemon 模式的固有税。**
- daemon:**idle 零 token**(纯 python 阻塞 watchdog),但**每任务付一次全量冷启动**(上下文重建 token + 数秒延迟),且**任务间无记忆**(做不了"接着上一个任务继续"的活)。
- tab /loop:idle 也几乎零 token(`serve` 阻塞在 watchdog,不烧 token),但**同一会话上下文跨任务累积**(warm、能接力),**代价是上下文越堆越大、最终触发压缩**,且占一个可见 tab。
- 结论:**两种宿主是"冷/无状态/省窗口" vs "热/有状态/占窗口"的权衡,不是谁取代谁。** 选择应由任务性质驱动:无状态小任务→daemon;需要接力/可旁观/高风险→tab。

### 其它风险点
- **安全/信任**:任何能写文件系统的进程都能往 inbox 投任务,而 worker 跑 `--dangerously-skip-permissions` → **一个恶意 inbox 文件 = 拿全工具权限的任意代码执行**。可接受(单用户本机信任域),但要心里有数:总线的安全边界=本机用户账户,别把 BUS 目录暴露给共享/同步盘。
- **daemon 跑高风险任务无人把关**:daemon 拉 `claude -p --dangerously-skip-permissions` **完全无人值守**。若 Master 把"写库/外发"任务派给某 daemon role,它会**直接执行,绕过"先审后做"原则**——而 daemon 不会停下来问。当前只靠"约定别这么派",无机制兜底。
- **结果/归档无限增长**:done 不 clear 就堆积(recover 会显示历史回填),archive 永不 GC。轻微,但久了脏。
- **遗留 role 未清**:旧 role `协调者`(已被 Master 取代)仍有遗留 tab 活着、`unknown` 空目录还在。名实不一致,易误派。
- **看不到"在做什么"**:presence 没有 `current_task` 字段,busy worker 在干哪条任务得去翻 processing/ 才知道。

---

## 3. 冗余 / 可简化处

- **wait vs serve**:`serve` = `_resolve_self_role()` + `wait`,二者共用 `_serve_wait`,无逻辑重复。**建议保留两者**:`serve` 给 worker(自动认角色)、`wait` 给"明确指定角色"的场景(如 daemon、调试)。不值得合并。
- **tab loop vs daemon**:**不建议二选一收敛**(见 ②⑦,是真权衡)。但**建议把"同 role 二选一"从文档约定升级为代码强制**(见 backlog #3)。
- **recover.py vs WebUI**:一个是 CLI 冷启动探针、一个是常驻可视化,受众不同,都 shell/import agentbus 真相源,**不是冗余**。
- **真正能砍的**:遗留空 role 目录(如旧 `协调者`/`unknown`)、堆积的 done/archive——是数据卫生,不是架构冗余。

---

## 4. 改进 backlog(排优先级)

### A. 低成本即可做(各 ~10–40 行,不动架构)

| # | 问题 | 提案 | 代价/收益 |
|---|---|---|---|
| **A1** | **processing 孤儿无人回收**(③,最大耐久缺口) | 加 `reap` 命令(或并进 who/recover):扫各 role 的 processing,若任务 `claimed` 时间超阈值 **且** 其 owner presence 已死/超心跳 → 移回 inbox(带 `requeue_count`+1)或标记 stranded。 | 低成本、**高收益**。补上"领了没做完"的唯一漏洞。 |
| **A2** | tab/daemon 并存 presence 翻动(④) | `start_worker.ps1` 启动前先看该 role presence:若存在**活的 tab-loop**(mode=listening 且 claude_pid 在跑)→ 拒绝/告警;`serve` 侧同理可警示。 | 低。消除翻动 + 浪费抢单。 |
| **A3** | 看不到 worker 在做什么 | claim 成功后把 `current_task_id/title` 写进 presence;done 后清空。who/WebUI 显示。 | 低。可观测性直接提升。 |
| **A4** | 遗留 role / 数据堆积 | 加 `prune` 命令:删空的遗留 role 目录、GC 超 N 天的 done/archive。先清旧 `协调者`/`unknown`。 | 低。卫生。 |
| **A5** | 未 tag 私事 tab 露 pid | who/WebUI 对"未声明"进程不再列裸 pid,或提示"请 tag"。 | 低。隐私收尾。 |

### B. 需较大改动(动机制 / 跨组件)

| # | 问题 | 提案 | 代价/收益 |
|---|---|---|---|
| **B1** | **daemon 崩了无人拉起**(①) | 把 WebUI 已验证的"计划任务自动重启"模式推广给 daemon;或加一个轻量 **fleet 看护**(单进程/计划任务),按一份 `fleet.json` 期望表保证配置内的 daemon 常活。 | 中。可靠性对齐 WebUI。 |
| **B2** | **daemon 跑高风险任务无人把关**(安全) | 任务加 `requires_approval: true` 字段;daemon `_wait_core` 领到这类任务**拒收并留在 inbox**(只让交互 tab 领),或 done error 提示"需人工 tab"。Master/WebUI 派高风险时默认置位。 | 中。把"先审后做"从约定变机制。 |
| **B3** | 失败任务要人工重派 | `done --status error` 可选 `--requeue`,带 `attempt` 计数,超 max 才真失败。 | 中。 |
| **B4** | 跨机迁移路径全断 + "迁 agent_fleet 即全部"名实不符(⑤) | ① 把 `CLAUDE_BIN/DEFAULT_CWD/ABDIR/SYNC` 收进一份 `config.json`(或全用 env + `Path.home()`),迁移只改一处。② **修正文档**:讲清总线在 `~/.claude/agent_bus`、agent_fleet 只镜像记忆;给一份真正的"迁移清单"(带哪几个目录)。 | 中低。迁移从"挨个改硬路径"变"改一文件"。 |
| **B5** | 无统一审计 | 加 append-only `events.jsonl`(send/claim/done 各一行),作为全链路可追溯日志(clear replies 后也留痕)。 | 中。 |
| **B6** | daemon 冷启动税 + 记忆带偏(②⑦) | 探索"温 worker":daemon 维持一个常驻 claude 会话(管道/Agent SDK)摊薄上下文重建,而非每任务冷拉;并用**精简的任务专用 system prompt**(不加载完整项目记忆)从根上消除带偏,而非靠 prompt 对抗。 | 较大、探索性。收益大但依赖 claude -p/SDK 能力,先做 PoC。 |

---

## 5. 长期方向

**当前判断:继续自维护,但补齐 A1–A4 + B1/B2 这几块"耐久与安全"短板。** 理由:它能用、可观测(正是当初否决子代理的核心诉求)、零外部依赖、贴合"驱动多个人类可见 tab"这个真实需求。

**何时该迁到平台原生能力(触发条件):**
- **迁到原生 agent-teams / channels / sub-sessions**:当 Claude Code 提供**可观测**的跨会话任务路由(可观测是硬指标——子代理当初就是因为不可观测被否)。届时把"分派/收结果"层换成原生 channel,**保留文件总线做本机 fleet 的探活与可视化**即可。
- **迁到 routines / scheduled cloud agents**:当 headless 定时/排队成熟,可替换 daemon 的"冷拉 claude -p"路径(尤其无状态批处理),省掉自己看护进程的负担。
- **升级为本机微服务**:当 fleet 超过 ~10 role、或要**跨机/多用户**时——文件总线缺中心协调、鉴权、跨主机传输会成瓶颈。届时把 agentbus 核心包成一个本机小 HTTP 服务(WebUI 已经是雏形:它已经在 127.0.0.1 上跑 HTTP + SSE),队列/presence 走 API 而非裸文件。

**反向(继续自维护)的条件**:只要 (a) 仍需驱动多个**人类可见**的交互 tab、(b) 无可观测的原生等价物、(c) 维持单机单用户信任域——文件总线就是性价比最高的形态,别为了"现代化"而迁。

---

## 6. 缺口专章 ①:任务执行中的 agent ↔ agent 交流(request / ask-and-await)

### 结论先行
**bus 底层已经够用,缺的只是一个 `request` 便利原语。** 现状 `send --to/--from` 已支持任意→任意,且 `done` 读任务里的 `from` 把结果路由回 `<from>/done/<id>.json`——**关联键现成就是 task id**,不需要新 id 体系。`request` = `send` 子任务 + **在 `<from>/done/` 上阻塞等那个 id 的应答文件出现**。新增代码 ~40 行,`done` **零改动**。

### 设计评估(Master 提的方案成立,补三个必须项)
Master 的设想正确:`request --to B --from A --task ...` 发子任务给 B 并阻塞等 B 的 done(B 的 `done --from... ` 其实是 `done --role B`,它读到任务 `from=A` 自动路由回 `A/done/<id>`)。daemon 模式下 B 的 `claude -p` 执行中可直接 shell 调 `request` 去"问 C 并等"。**但必须补齐:关联 id 的消费、强制超时、循环/死锁防范。** 逐条给确切实现。

### 确切实现

**(a) 等应答 helper —— 复用 `_wait_core` 同款 watchdog 模式,只是改成等 `done/` 下的特定文件名:**
```python
def _wait_for_reply(from_role, tid, timeout):
    """阻塞到 <from_role>/done/<tid>.json 出现(B 的 done 已按 from 路由到这),读出并消费。
    消费(unlink)是为了不污染 from_role 的 replies —— 同步应答不该再被当成异步回填。"""
    done_dir = _role_dir(from_role) / "done"
    target = done_dir / (tid + ".json")
    def _read_consume():
        try:
            obj = json.loads(target.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": "bad reply: %s" % e}
        try: target.unlink()
        except Exception: pass
        return obj
    if target.exists():
        return _read_consume()
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    ev = threading.Event()
    class H(FileSystemEventHandler):
        def on_created(self, e):
            if str(e.src_path).endswith(tid + ".json"): ev.set()
        def on_moved(self, e):
            if str(getattr(e, "dest_path", "")).endswith(tid + ".json"): ev.set()
    obs = Observer(); obs.schedule(H(), str(done_dir), recursive=False); obs.start()
    try:
        if target.exists():          # 起 observer 后再查一次,消竞态(同 _wait_core)
            return _read_consume()
        ev.wait(timeout=timeout)
    finally:
        obs.stop(); obs.join()
    return _read_consume() if target.exists() else None
```

**(b) `request` 命令 —— send + 等应答,内置三道防护:**
```python
def cmd_request(args):
    frm = args.frm or os.environ.get("AGENTBUS_ROLE", "unknown")
    # 防护1:禁止自请求(A 阻塞等自己 → 永远不会去 serve 自己的 inbox → 必死锁)
    if args.to == frm:
        print(json.dumps({"error": "self-request forbidden (to==from)"}, ensure_ascii=False)); return
    # 防护2:循环检测 —— chain 里已有目标就是环(A→B→A / A→B→C→B)
    chain = [c for c in (args.chain.split(",") if args.chain else []) if c]
    if args.to in chain:        # 目标已在调用链上 → 环(A→B→A、A→B→C→B 都被挡)
        print(json.dumps({"error": "cycle detected: %s in chain %s" % (args.to, chain)}, ensure_ascii=False)); return
    _role_dir(args.to)
    tid = _gen_id(args.to)
    task = {"id": tid, "from": frm, "to": args.to, "title": args.title or "",
            "task": args.task, "created": _now(), "status": "pending",
            "reply_mode": "sync", "chain": chain + [frm]}   # 把自己加进 chain 往下传
    _write_json(BUS / args.to / "inbox" / (tid + ".json"), task)
    # 防护3:强制有限超时 —— 即便环没检出,所有人最终超时退出,绝不永久挂
    rep = _wait_for_reply(frm, tid, args.timeout)
    print(json.dumps(rep if rep is not None else {"timeout": True, "id": tid, "to": args.to}, ensure_ascii=False))
```
argparse:`p=sub.add_parser("request"); p.add_argument("--to",required=True); p.add_argument("--task",required=True); p.add_argument("--from",dest="frm"); p.add_argument("--title",default=""); p.add_argument("--chain",default=""); p.add_argument("--timeout",type=float,default=300.0)`。

### 死锁 / 嵌套防范(三层,从弱到强)
1. **禁止自请求**(to==frm 直接拒)——挡掉最蠢的一类。
2. **chain 软环检测**:task JSON 带 `chain` 数组,`request` 把 `frm` 追加传下去;目标已在 chain 内即拒。**跨 `claude -p` 传递靠 worker 自觉**:worker prompt 要求"若要问别的 agent,跑 `request --to X --from <我> --chain '<我收到的任务里的 chain,逗号拼接>'`"。best-effort——claude -p 不一定可靠透传。
3. **强制有限超时(硬兜底,最关键)**:每个 request 都有 finite timeout,**所以即便环没被检出,链上所有人最终超时返回,系统绝不永久挂死**。建议**嵌套递减预算**:传 `--deadline <绝对时间戳>` 往下,子 request 的 timeout = deadline - now,越深越短,根超时即全链收敛。

### 并发语义(必须讲清,否则会误用)
- tab `/loop` 里 worker 阻塞在 `request` 期间,**它自己的 inbox 不再被 serve**(claude 这一轮卡在 request 上)。即"边问 B 边接新活"做不到——一个 worker 同一时刻要么在做活(含等子结果)要么在听,不能两者兼。
- daemon 同理:`claude -p` 一次只跑一个任务(含其嵌套 request),daemon loop 等这单整体结束才 re-arm。
- **超时遗留**:request 超时后,子任务可能仍在 B 的 inbox/processing,B 迟早 done → 写 `A/done/<tid>` 但已无人等 → 变游离回填。**正好由 §4-A1 的 reaper / GC 兜底**(或 request 超时时把该子任务标 `abandoned`)。

---

## 7. 缺口专章 ②:任务完成主动通知 Master(不轮询)

### 把约束想透:唯一"醒着"的接收者是人,不是 Master
Master 是 idle 交互会话,**推不进去**(同"唤不醒 idle 会话"那堵墙)。所以"通知 Master"本质是**"通知在场的人,让人去碰一下 Master / 开 WebUI"**。三条可落地的通道恰好对应:① OS 事件 toast 给人;② WebUI SSE(开着才看到);③ Master 下次开口被动捞 replies。下面给推荐组合 + 谁来发。

### 通道对比
| 通道 | 触达 | 时机 | 谁发 | token |
|---|---|---|---|---|
| **WinRT toast(done 内置)** | 人(通知中心可累积) | 每次完成(可节流/分级) | **`done` 子进程跑 PowerShell** | 0 |
| WebUI SSE | 人(控制台开着时) | done 写文件即推 | 已实现,无需加 | 0 |
| Master 被动汇报 | LLM(人下次开 Master 时) | 下一轮 | Master 醒来先 `replies` | 正常 |
| PushNotification | 人(反扰 60s,走开自动推) | — | **只有活着的 claude 会话能调,done 子进程调不到** | — |

### 推荐方案(分级,守全局规则:事件通知 > 不弹模态)
1. **`done` 内置 WinRT toast(主推)**——完成时刻就在 done 端,**且 done 在 tab/daemon 两种宿主都会跑**,所以无论谁干的活都能触达人;0 token。**为什么是 WinRT 不是 PushNotification**:PushNotification 是 Claude Code 会话内建工具,`python agentbus.py done` 这个**子进程调不到**;而 WinRT 有现成模板(AppID `ClaudeCode.NotifyTest`,全局 CLAUDE.md 里那段),fire-and-forget 一条 PowerShell 即可。**必须 try/except + 短超时,绝不阻塞 done 本身。**
2. **分级触发,防刷屏**:
   - `status=error` → **必发**(失败要人知道)。
   - `status=ok` 且 `from` 是人值守角色(Master)→ 发,但**节流**(距上次 toast < X 秒则并入/跳过)。
   - worker 之间互发(非人值守 from)、`reply_mode=sync`(§6 的同步应答,调用方已同步拿到)→ **不弹**。
   - 加开关 `done --notify auto|always|never`,默认 `auto`(= error 必发 + Master 完成发 + 节流)。
3. **谁来发**:**done 端发,不要另起 notifier 守护进程**。理由:我们刚在 §2/§4 批评"daemon 没人看护";再加一个常驻 notifier 又是一个要被看护的东西。done 端发是零新增进程、零新增看护面,且天然覆盖所有宿主。
4. **WebUI SSE 保留**为"开着就实时看"的被动大屏(已实现,done 写文件即 _bump → SSE),与 toast 互补:toast 负责"把人喊回来",SSE 负责"回来后看全貌"。
5. **Master 被动汇报保留**:Master 每次被唤醒先 `replies --role Master` 主动报未读——这是 LLM 侧唯一能做的(它醒不过来,只能醒来时先看),已是现状。
6. **PushNotification 的正确位置**:不在 done 端,而在"**有一个活着的 Master 会话、它自己发现需要把走开的人喊回来**"时——那一刻 Master 是活会话,可直接调 PushNotification(享受 60s 反扰 + 走开自动推)。即:**done→WinRT(无会话也能发);活 Master→PushNotification(有会话时更优)**,两个时刻两套,不冲突。
7. **铁律**:全程**不用 MessageBox / 模态对话框**(全局规则:多任务并发弹窗堆叠,用户无法逐一处理)。

### done 端 toast 落地骨架(并入 `cmd_done` 末尾,best-effort)
```python
def _maybe_toast(sender, status, title, result):
    # 只对人值守 from + 分级;失败必发,成功节流。绝不抛错、绝不阻塞。
    if sender not in ("Master",) and status == "ok":
        return
    head = ("✅ 任务完成" if status == "ok" else "❌ 任务失败") + " · " + (title or "")
    body = (result or "")[:120].replace("'", "’")
    ps = ('$x=New-Object Windows.Data.Xml.Dom.XmlDocument;'
          '$x.LoadXml(\'<toast><visual><binding template="ToastGeneric">'
          '<text>%s</text><text>%s</text></binding></visual></toast>\');'
          '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('
          '"ClaudeCode.NotifyTest").Show([Windows.UI.Notifications.ToastNotification]::new($x))' % (head, body))
    try:
        subprocess.Popen(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # 不等、不阻塞 done
    except Exception:
        pass
```
(需在 done 里先建好 WinRT 类型加载头那两行 `[Windows.UI.Notifications...]|Out-Null`,或封进同一段 PS;细节见全局 CLAUDE.md 的 WinRT 模板。)节流状态可存 `BUS/.last_toast`。

---

## 附:Top 3 改进建议(摘要)
1. **processing 孤儿回收器(A1)** — 当前最大耐久漏洞:任务领了没做完会永久卡死,无人回收。加 lease/TTL + reaper 退回 inbox,低成本高收益。
2. **daemon 自愈 + tab/daemon 互斥(B1+A2)** — daemon 崩了没人拉起(WebUI 有自动重启、daemon 反而没有);同 role 并存会导致 presence 翻动。补自动重启 + 启动期互斥拦截。
3. **高风险任务对 daemon 设闸 + 路径外置(B2+B4)** — daemon 跑 `--dangerously-skip-permissions` 无人把关却可能收到写库/外发任务;同时硬编码绝对路径让跨机迁移全断、且"迁 agent_fleet 即全部"名实不符。加 `requires_approval` 闸 + 把路径收进单一 config。
