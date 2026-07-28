# BUS 卓越工程清单(doc-blind 四路代码审查 · 2026-07-28 · Fable)

> 方法:4 个只读代码、禁读文档的审查代理(核心架构/并发与崩溃一致性/周边进程/WebUI)独立审查,
> MasterAgent 拿疑点对文档/决议逐条销案——销不掉的进本清单。目标(用户令):让 bus 成为优秀项目。
> 状态图例:☐待办 ☑完成 ◪部分。完成一项勾一项,全清后本文件归档。

## P0 — 会造成数据错/丢/双跑/安全洞(先修)

- ☑ **fanout-collect NameError**(pool.py 缺 import threading·等待路径必崩)——e7f1244
- ☑ **两 hook GBK 炸弹**(reinject/on_memory_write 裸 json.load·CJK payload 即崩=静默失效)——e7f1244
- ☑ **claim 原语统一 O_EXCL**(mailbox._claim 加 <task>.lock O_EXCL·pool 经 locked=True 直通;闸测 120 轮×6 进程恒 1 胜·2026-07-28)——原文:claim 原语统一 O_EXCL:个人 inbox `_claim`(mailbox.py)仍用本机已实证会双胜的 os.replace 争源;
  docstring 自称支持 tab+daemon 双监听 → 同任务双执行风险。修法=与组池统一 O_EXCL 锁。
  (销案注:os-replace 记忆承认"只因单消费者没暴露"=已知带伤;按卓越标准应机制层闸死,不靠组织纪律)
- ☑ **共享 JSON 裸 RMW 加锁**(io.json_lock 公共原语+roster.update_agents;todos 五写点/agents 五写点全收口·2026-07-28)——原文:共享 JSON 裸 RMW 加锁:todos.json 四方并发读-改-写(ask-user/answer/sweep/WebUI)会丢待办丢答复;
  agents.json 同病(register/hello/retire/revive 并发覆盖)。修法=复用 route_lock 的 O_EXCL 模式。
- ☑ **_save_agents 改原子写**(_write_json·2026-07-28)(roster.py write_text 直写·崩溃截断=全花名册"消失")。
- ☑ **_write_json tmp 名去确定性**(pid+随机后缀·2026-07-28)(io.py 固定 .tmp 名·两进程同写同目标时 B 的半截文件可被 A replace 进正式位;
  加 pid+随机后缀,一行修)。
- ☑ **claude_run 超时杀进程树**(taskkill /T /F + p.kill 兜底·2026-07-28)(p.kill 只杀 .cmd 宿主·node 孤儿续跑烧 token+stdout 管道卡死 daemon;
  改 taskkill /T /F)。
- ☑ **external 四件套抢锁失败禁继续**(canvas/msg/read/sheet_read 四处 _lock 解析 acquired,失败 exit 3·2026-07-28)(edge-acquire 失败只 warn 就照操作 Edge=互斥形同虚设=串台病根;
  抢锁失败必须退出/重试)。
- ☑ **UI 安全三件**(MasterUI lane):SSE 已死 / POST 零 Origin 校验 / clear_replies 路径穿越——MasterUI 修毕,
  实测 SSE data:change 到达、跨 Origin POST 403、穿越校验生效(commit 6a516b4·2026-07-28)。

## P1 — 正确性/一致性(P0 后)

- ☐ 锁夺取 ABA(两进程同判孤儿锁各自 unlink→双持锁;pool 组锁/route 锁/edge 锁三处;改 os.replace 改名占有)
- ☐ done/reap 崩溃窗口(done 先写回执后删 processing→重跑;reap 写+删两步→双份;reap 前查回执已存在则归档)
- ☐ request 走 send 契约(现绕过审计/可达告警/培训闸→events.jsonl 不可见;抽 _deliver 共用)
- ☐ done 归属禁前缀匹配(startswith 短 id 可错配;查找应含 archive)
- ☐ presence 多写者(心跳整写丢 current_task·靠时序假设;_audit 追加未用 O_APPEND)
- ☐ 角色策略出代码进注册表(角色白名单/_MGMT_OWNER/HUMAN_ATTENDED 硬编码;守卫伪造 from=MasterAgent;
  bus-router 不在伪角色名单)
- ☐ start_worker.ps1 用 config python_bin(现裸 PATH python·缺 watchdog=隐藏 daemon 静默死)
- ☐ dispatch 路径补 requires_approval 闸(现 --dangerously-skip-permissions 直跑高风险)
- ☐ 卫生代谢扩容(_hygiene_core 补:孤儿 .tmp/*.lock、fanout manifest、pid_tags 死 pid、
  events/metrics.jsonl 轮转、done_archive//_dead_letter//.trash 终点站保留期)
- ☐ watchdog 样板 4 处抽公共 wait_for_event(NameError 正是复制丢 import 所致)
- ☐ 公共面定稳定 API 层(facade 现 re-export ~90 个 _私名无 __all__;UI 依赖 15 个私名)
- ☐ UI:todos 锁/fresh 空操作/硬编码路径/read_events 整读尾取/死代码 build_activity(MasterUI lane)

## P2 — 卫生/清晰度

- ☐ 错误通道统一(exit 码 vs stdout {"error"} 混用·机器调用方无法判败)
- ☐ 入口路径归一 config(ab/ab.cmd/vbs/bus_tray 各自硬编码·"换机只改一处"实际五处)
- ☐ enforce_selfwake.py 孤儿文件处置(SHELVED 已摘注册·git 留史即可删)
- ☐ pool_dispatcher 休眠脚本:修 unlink-先于-spawn 的丢任务序,或明标 FUTURE 不修
- ☐ external 家族:焦点策略自相矛盾说明/_RELOAD 全局改传参/四份 _lock 复制收敛
- ☐ githooks:tag 强移不刷新 zip 与注释矛盾;打包运行中 db 的一致性注记
- ☐ `--timeout 0` 哨兵语义、_gen_id 嵌 role 名(前缀匹配歧义面)——随 P1 done 精确匹配一并审

## 文档漂移清单(代码≠文档处·各 owner 自纠)

1. mailbox docstring 宣称 os.replace"只有一个成功" vs pool.py 自家实证双胜(已修:_claim 统一 O_EXCL,docstring 随改·2026-07-28)
2. sync_code.ps1 已死但 on_memory_write 仍每次触发(已修 e7f1244)
3. external_canvas 论证"不 bring_to_front 是铁律" vs external_msg/external_sheet_read 两处照用(需成文豁免条件)
4. "换机只改 config.json 一处"承诺 vs 五处硬编码入口(P2 归一后文档才成立)
5. AGENTS.md 曾三行重复(已修)/ bus-2.0 记忆索引行曾 stale(已修)——索引行与正文同步纪律
