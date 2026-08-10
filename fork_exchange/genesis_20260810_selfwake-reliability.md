# genesis 分岔改动:自醒可靠性 + 状态真实性 + 无状态 worker 设计

> fork:genesis · 日期:2026-08-10 · 覆盖:v2.6→v2.7 及 v2.7 后的 block 语义(commit 512064a / 8b95676 / e151c03 / 28621a0 + 设计文档)。
> 主题:**任何 bus 的前台 cli + 后台 wait 自醒形态都会踩的三类坑,及其根治**——对另一个 bus 吸收价值最高的一批。

## 改动 1:wait 重挂幂等化 yield-not-kill(commit 512064a)
- **改了什么/为什么**:重挂 wait 前"杀旧 wait"会与 harness 完成通知互锁成自持振荡(杀旧→旧 wait 空输出"完成"→harness 唤醒会话→纪律重挂→又杀刚挂的→~15s 热循环)。改为 yield-not-kill:已有更老同 role 活 wait 则让位打 `already_armed` 即退、不杀(create_time+pid 严格序防互让归零);`--takeover` 强制换新;`serve` 保留接管。
- **可吸收性**:✅ 平台面机制(纯自醒逻辑,任何 bus 通用)。
- **吸收单元**:`agent_bus/bus/core.py` `_serve_wait`/`_prior_wait_procs`/`_kill_prior_waits` + agentbus.py `--takeover` 参数。
- **验收**:bus_smoke;实测双挂让位/持有者存活/takeover 换新。

## 改动 2:状态口径三修(commit 8b95676 + e151c03)
- **改了什么/为什么**:①面板 TURN 列 armed 无条件盖成"空闲·待命"→误显正在干活的会话(前台 cli+后台 wait 并存是主流,armed≠不在思考),改为新鲜活动态优先;②托盘图标口径(用户定=在跑数,0 一眼判没活);③"在跑"统计剔除 bus守卫提醒/notice 等系统消息(虚高)。
- **可吸收性**:◐ 需适配(UI 层 `ui/page_js.py` turnBadge + `ui/api.py` tray 计数;吸收方若 UI 结构不同需改;口径逻辑可迁)。
- **吸收单元**:见 commit;核心逻辑=freshActive 优先 + 系统消息剔除。
- **验收**:ui_smoke。

## 改动 3:自醒哨兵 block 语义(commit 28621a0)★最高吸收价值
- **改了什么/为什么**:`ab wait --timeout 0` 原按日2h/夜6h 空返 → 到点唤醒 turn 冷重建整窗 context(缓存 TTL 1h 已过期)→ 空唤醒白付 ~$4.2/次(实证近两周 171 次 ≈ 周 $361)。改为 `sentinel_mode=block`:返回 block_timeout_s(7天)= 实践上"阻塞到消息才醒"。用大有限值非真无限=留极限自愈(harness 清理超长后台任务时 7 天仍空返触发重挂,不永久掉聋)。
- **可吸收性**:✅ 平台面机制(任何用 idle-timeout 自醒的 bus 都在白烧冷唤醒)。
- **吸收单元**:`agent_bus/bus/config.py` `_idle_timeout_policy` + `config.json` idle_timeout_policy.sentinel_mode/block_timeout_s。调用面仅 cmd_wait 哨兵;daemon 走自己 --wait-timeout 不受影响。
- **验收**:bus_smoke;实测 --timeout 0 阻塞不秒返。
- **需适配**:cost 数字($361)是 genesis 实例的,吸收方按自己 usage 重估收益。

## 改动 4:设计文档(未实现·供吸收方参考方向)
- 重构边界确认书 / 多 bus 进化转移备忘(本机制即其落地)/ worker 无状态化分析(E7 长会话轮换 SOP + stale 三信号 + 插件式记忆用户方向)。
- **可吸收性**:✅ 设计思路通用;实现待各 bus 自定。文件:`docs/REFACTOR_BOUNDARIES.md` `docs/MULTI_BUS_EVOLUTION.md` `docs/WORKER_STATELESS_ANALYSIS.md`。

---
**整体验收**:吸收后 bus_smoke 43/43 + ui_smoke 79/79 全绿 + 平台三问(归属/清除/换域)。
**不可吸收(实例面·跳过)**:config.json 本机路径、agents.json 在册角色(genesis 舰队)、usage.db、junction 布局。
