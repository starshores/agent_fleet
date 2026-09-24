# genesis fork · 2026-09-24 · 忙而聋可见化 + ④型悬单真修 + resume 守卫 + 分离 B1/A4 收口(v2.15)

> 实例名=genesis。覆盖 v2.14 以来 7 个提交(含 MasterUI 读方切换 1ffac70)。主题:「进程活着 ≠ 协作可达」——两种新形态被实证并机制化;分离整改 B1/A4 收口。

## 1. 忙而聋(busy-but-deaf)可见化 ✅平台面
- **现象**:turn 活跃(用户在其 tab 工作)但 inbox 积压未领消息 15h;selfwake/status 按「turn 活跃=干活期正常态」放行;守卫催单投进 inbox 同样无人读=白投。
- **机制**:`bus.selfwake._inbox_backlog(role)`(≥`deaf_inbox_min`=30min 未领条数·最老小时)→ selfwake detail / who verdict 标 `⚠忙而聋:inbox积压N·最老Xh`(state 仍 recent·守卫 busy 逻辑不变·纯可见化);守卫对聋态**不投 inbox 催单只 toast**(给人·请其在该 tab `ab peek`)。
- C′ Stop-hook 灰度白名单出代码进 config `stop_hook_gray_roles`(list;null=全员)。**注意**:我曾据此把两协调位扩进段③·被Master数分驳回(见 §2·根因是④非①·且与 harness「勿自重启」指令冲突)·已回退段②。

## 2. 第④型掉线:harness 内存回收 → 孤儿 wait 仍 claim → 悬单 ✅平台面(★强烈建议吸收)
- **实锤**:Claude Code 低内存时把后台 wait 的 bash 包装壳标 killed(会话 jsonl task-notification 原文),python wait **不一定死**(Master数分样本:bash+python 都活·只是 CC 不再通知),之后照常 claim 消息并写输出文件·无 turn 处理 → 消息烂 processing。harness 通知明令"勿自行重启"·agent 未重挂=当时正确行为·**不计 C′ 失败·不计纪律**。四型口径:①忘重挂 / ②C′误拦 / ③延迟唤醒 a·b / ④harness 回收(孤儿 claim)。
- **真修**:`_claim` 记 `claim_pid/claim_ppid`(仅排障);wait 心跳线程父壳 create_time 自检→父亡自退不再 claim;**每次 wait 起手 `_requeue_orphans`** 把悬单退回 inbox → 新 wait 立刻领到;reap 同判据;selfwake/who 标 `⚠悬单N`。
- **★判据反例(热修·Master数分代码级独立核抓到)**:首版用「claim 者进程存活」判悬单——`claim_pid` 是 wait 自身 pid、正常 claim 后即退=恒亡 → 每条在办消息都会被退回=双处理;smoke 全绿是因为只造了"活 pid"样本、漏了"正常 claim 后 wait 退出"这一真实生命周期态。**正确判据**=listening 角色 processing 消息【claimed 后该会话无任何 turn 活动 ≥ `orphan_no_turn_min`(30)】=输出未送达;daemon 不判(reap ttl 兜)。smoke `deaf.busy` / `deaf.orphan`(含"claimed 2h 但 turn 更新更晚→不得退回"对照态)。
- 方法论:给别人定掉线型前先看其会话 jsonl 末段有无 harness 回收通知(时间戳级·不读正文);环境因被误归纪律与纪律被误归环境**两向都要核**。

## 3. resume 守卫(停机恢复面板)✅平台面
- presence.json 解析失败(实例:含 317 个 NUL)原静默 continue → 角色从面板**消失**;改列 `⚠presence损坏` 行(置顶·note 含异常类型+`ab hello` 重写自愈)。
- 同一 session_id 被 ≥2 role 登记(实例:休眠角色 presence 冒用了 MasterAgent 的 sid)→ 文本尾部告警行;`--json` rows 结构不变·告警走 stderr。smoke `resume.guard`。

## 4. 分离整改收口 ✅平台面
- B1:WebUI 读方切 `ab.agents_view()` 后剥 agents.json 16 业务角色 desc(2 无页角色自动建最小页·14 存 `legacy_desc`·`agents_view` 剥前后 byte-identical);余 10 业务词=角色名键+caps 引用(机制态·归 B2)。
- A4:摘 `edge_lock` 旧字段·只出 `locks[]`;health 文本改「资源锁」行(smoke 抓到残留读方=回归价值)。UI 删回退分支(1ffac70/566ea00e)。

## 5. 实例面(⏭跳过)
具体角色名/阈值取值(30min 与守卫 grace 同口径可调)/备份目录/业务记忆路径;机制注册表 118→120;bus_smoke 59→62;ui_smoke 94。
