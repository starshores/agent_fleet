# BUS 2.0 重构方案(重组 + 机制优化)

> 起草:MasterAgent · 2026-07-14 · 待用户签字后开工
> 范围选择:用户选 **B = 重组 + 机制优化**(不推倒重来 C,不止步纯重组 A)
> 分工:三兄弟(MasterAgent 总架构+core/CLI/aux;MasterUI 做 UI;Master数分 做验证/口径)—— 待用户确认②
> 最高约束:**数据不出本机**(全程本机文件,无外发);**canonical `~/.claude/agent_bus/agentbus.py` 全程可跑**(全员每次 wait/done/hello 都 shell 它,断一下全员掉线)

---

## 0. 现状基线(v1.10 · 92 机制/13 类)

**病灶(散在哪)**
1. 两个巨石文件:`agentbus.py` 3267 行/~55 子命令/~120 函数;`ui/server.py` 4930 行。
2. 辅助工具平铺无包:worker_daemon / dispatch_agent / claude_run / pool_dispatcher / complexity_snapshot / group_metrics_spec / clouddoc_×4 / bus_tray / recover。
3. 状态 JSON 十几个概念重叠:agents / capabilities / fleet / fleet_status / retired_roster / tasklines / todos(2179行) / longrange_tasks(818行) / bus_mechanisms / pid_tags / session_tags。
4. CLI 命令面平铺 55 个,无分组。
5. 文档 15+ md 重叠+陈旧(AGENTS.md 有 3 行完全重复)。

**已知机制优化靶点(B 的抓手)**
- **状态判定 4 处各算**(记忆 `role-verdict-computed-in-4-places`):who/list/health/selfwake/tray/WebUI 各独立算 verdict → 收敛单一 `_who_data()` 源。高价值、根治一类反复 bug。
- **待办/任务/决策三套重叠**:todos(ask-user 待办池)/ tasklines(长期任务线)/ longrange(生长网 DAG 决策)按 ref 交叉 → 评估归一或清晰分层(需数分口径把关)。
- **fleet vs fleet_status 重叠**;daemon 前台化迁移中的死路径清理。
- **health-sweep 在注册表出现 2 次**(cat4+cat10);edge + clouddoc_×4 收编;死机制退役。

---

## 1. 目标形态(一体化 · 内部仍模块)

```
agent_bus/
├── busctl.py            # 单一入口:busctl <组> <命令>;并保留 agentbus.py 扁平别名(兼容全员)
├── bus/                 # 一体化包
│   ├── io.py            共享:_write_json / O_EXCL 原子 / 重试
│   ├── config.py        cfg / config.json
│   ├── audit.py         events.jsonl
│   ├── proc.py          _proc_scan / _claude_ancestor / 身份反查
│   ├── status.py        ★单一状态源:_who_data 收敛(喂 who/list/health/selfwake/tray/UI)
│   ├── core/            消息任务流:send/wait/done/request/peek/replies/claim
│   ├── presence/        hello/serve/whoami/selfwake/guard/park
│   ├── roles/           register/retire/revive/caps/roster/route
│   ├── health/          reap/health/sweep/supervise/prune/webui-guard/mem-cap/metrics
│   ├── pool/            join/leave/send-group/fanout/pool_dispatcher/group_metrics
│   ├── exec/            dispatch/worker_daemon/claude_run
│   ├── edge/            edge-acquire/release + clouddoc_×4 收编
│   ├── tasks/           tasklines/todos/ask-user/answer/longrange_net/complexity
│   └── notify/          toast/notice
├── ui/                  server.py → routes/api/panels/render 子模块(MasterUI lane)
├── tray/                bus_tray
└── state/              (可选)状态 JSON 归拢一处;消费者不同者保持独立文件
```
- **agentbus.py 变薄**:只做 CLI 分发,cmd_* 迁进各 topical 模块。
- **旧命令零破坏**:`agentbus.py wait/done/hello/...` 全部保留为别名;新增 `busctl <组> <命令>` 分组视图让人看着不散。

---

## 2. 分阶段执行(每阶段一个回归闸 + git commit + canonical 恒可跑)

### Phase 0 — 安全网 + 行为基线(动任何刀之前)
- 快照 canonical + agent_fleet 镜像(git tag `v1.10-pre2.0` + zip)。
- **建冒烟回归**:一个 throwaway 测试 role 端到端跑 ~55 命令(hello/wait/done/send/who/health/selfwake/caps/roster/route/reap/supervise/park/join-group/send-group/fanout/edge/dispatch/ask-user/answer/tasklines…),抓输出存快照 = 回归金标准。**每阶段后必须仍绿**。
- 冻结契约:入口路径 + 全子命令名/flag 不变。
- **验收(数分)**:基线可复跑、输出稳定。

### Phase 1 — 包骨架 + 共享原语抽取(行为不变)
- 建 `bus/` 包,先抽 io/config/audit/proc/status 共享层。
- cmd_* 按主题迁进子模块;agentbus.py 改薄分发器 import 注册。
- **闸**:每抽一块跑基线冒烟,绿了才下一块;canonical 恒可跑。

### Phase 2 — 机制优化(B 的核心 · 每项独立 review+闸)
- **2a 状态源收敛**:建 `bus/status.py` 单一 `_who_data`,who/list/health/selfwake/tray/WebUI 全改吃它 → 灭 4 处分叉。(数分逐视图核 count 不变 = 铁律,记忆 `role-verdict-computed-in-4-places`)
- **2b 待办/任务/决策数据模型**:先出子设计(归一 or 分层),**数分口径签字**后再改;todos/tasklines/longrange 的 ref 关联保语义。
- **2c fleet/fleet_status 合并 + daemon 死路径清理**;**2d health-sweep 去重 + edge/clouddoc 收编 + 死机制退役**。
- 每项:数分验证「优化前后行为/计数一致」。

### Phase 3 — CLI 一体化
- `busctl <组> <命令>` 分组 help;旧扁平命令保留别名。
- **闸**:基线冒烟(走别名)+ 新分组命令抽样验证。

### Phase 4 — UI 重构(MasterUI lane)
- server.py 4930 → `ui/` 子模块(routes/api/panels/render)。**契约:端点 + 面板 + 口径不变**。
- MasterUI 按我给的目标结构+契约做;数分验口径不变;我收口。

### Phase 5 — 文档收敛
- 15 md → 一套:ARCHITECTURE / OPERATIONS / ONBOARDING / RECOVERY(+ bus_mechanisms.json 作机制 SSoT 更新到 2.0)。清陈旧/重复。

### Phase 6 — 切换 + 版本戳
- canonical 翻 2.0,跑全量基线;**给 Master 发「怎么用」简报**(busctl 分组 + 任何变动命令);tag `v2.0`;刷镜像(sync_code.ps1)。

---

## 3. 分工契约(② · 三兄弟)
| 角色 | 负责 |
|------|------|
| **MasterAgent(我)** | 总架构 + 契约;Phase 0/1/2/3/5/6;core/presence/roles/health/pool/exec/edge/tasks/notify 重构 + aux 收编 |
| **MasterUI** | Phase 4 UI 层重构(按我给的目标结构 + 端点/面板契约) |
| **Master数分** | 全程验证闸:基线冒烟、状态 count 逐视图核平、待办数据模型口径签字 |

## 4. 风险控制
- 每阶段:回归冒烟绿 + git commit;**canonical 恒可跑**;旧命令全别名兼容;无大爆炸切换。
- 数据不出本机(全本地);凭据不进 git。
- 2b(数据模型)是最高风险项 → 强制数分口径签字 + 独立子设计,不与结构重构混在一步。
