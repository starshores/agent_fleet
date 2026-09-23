# 三闸提案 · 分离整改 A:Edge/外部 IM 互斥 → 通用共享资源互斥(+ 外部 IM 工具族外迁)

> 提案方:MasterAgent · 2026-09-23 · 依 SELF_REDESIGN_CHARTER + SEPARATION_AUDIT_20260923 项 1(最重:业务专用协调机制烤进核心)。
> 签字方:Master数分(三闸)· 消费方知会:**外部 IM**(external_im_*.py 调用方)/ MasterUI(health/status `edge_lock` 读方)/ 培训师(onboarding 文案 edge-acquire)/ Master(派活 prompt)。状态:提案中(先广播 → 动刀 → 确认·迁移特化铁律)。

## 0. 盘点补遗(审计漏项)
审计项 1 只列了 `bus/edge.py` + 2 CLI + health/status + daemon/dispatch prompt 外部 IM 段。**漏了 `agent_bus/external_im_{common,canvas,msg,read,sheet_read}.py` 5 个外部 IM CDP 工具本体**(业务工具住在基建目录·随 agent_fleet git 演进)。归属问✗(外部 IM CDP 操作=业务对外能力·非协调机制);清除问✗(卸业务后 5 文件仍在核心);换域问✗(换域这 5 文件是死代码)。纳入本闸 A3。

## 1. 目标形态
- 核心只有一个**通用互斥原语**:`ab lock acquire --resource <name> [--who --timeout --ttl]` / `ab lock release --resource <name> [--force]` / `ab lock status`。锁文件 `BUS/locks/<name>.lock`(O_EXCL·stale 回收·watchdog 等释放——**逐字复用 edge.py 现算法**,只把路径参数化)。
- `edge-acquire / edge-release` = `--resource edge` 的**别名**(命令面不破·输出 JSON byte-identical);过渡期 resource `edge` 特判映射旧路径 `BUS/edge.lock`(health/status/UI 的 `edge_lock` 读方零改动),第二阶段再迁 `locks/edge.lock` 并让 status 输出通用 `locks` 列表(MasterUI 切读方后)。
- daemon/dispatch prompt 的外部 IM 段 → **通用措辞**「若本任务要独占某共享资源(见角色页 shared_resources)→ 先 `ab lock acquire --resource <名>` 再操作、用完 release」;具体"Edge/外部 IM/写单元格/connect_over_cdp"文案迁 `role_profiles_biz.json` 各业务角色 `shared_resources:[{"resource":"edge","hint":"…外部 IM CDP…"}]`,prompt 拼装时按角色页注入(无页/无字段=不注入=空载干净)。
- 5 个 `external_im_*.py` → 业务侧 `C:\apphome\agent_fleet_data\biz_tools\external_im\`(自带 git·同 biz_registry 模式),`agent_bus/external_im_tools` 留 junction 过渡(agent_fleet git 忽略);`external_im_common._lock` 改调 `ab lock acquire --resource edge`(经 `ab` 启动器·不依赖相对路径)。
- 机制注册表类 7「Edge互斥与外部 IM 协调」→ 改名「共享资源互斥」;外部 IM 专属 4 条移注为业务侧机制(biz_registry/README 记)。

## 2. 分步(在线增量·每步可独立回滚)
| 步 | 内容 | 风险 | 回滚 |
|---|---|---|---|
| A1 | 新增 `bus/lock.py`(参数化 edge.py)+ `ab lock` 子命令;`edge-*` 改为调 lock(resource=edge→旧路径) | 低(纯增量+别名) | 还原 edge.py 入口 |
| A2 | prompt 通用化 + 角色页 `shared_resources`(外部 IM/示例成员2/文档管理…凡碰 Edge 的角色)| 中(改派活 prompt·Master/daemon 消费)| prompt 段回旧文案 |
| A3 | external_im_*.py 外迁 biz_tools(先广播→复制校验→junction→删源→确认)| 中(外部 IM agent 路径依赖·迁移窗瞬时 No-such-file)| 复制回 agent_bus |
| A4 | health/status `edge_lock` → `locks[]`;MasterUI 切读方;`edge` 锁文件迁 `locks/` | 中(MasterUI 契约)| 特判映射保留即可 |

## 3. 三闸自评
- 闸①核心价值(协作):强化——任何域的独占资源(Edge/某设备/某远端会话)都能用同一原语协调;不再"只有外部 IM 能互斥"。
- 闸②分离三问:归属✓(核心=互斥原语)· 清除✓(卸 biz_tools+角色页字段后 prompt 无外部 IM·`ab lock` 仍可用)· 换域✓(换域只加角色页 shared_resources)。
- 闸③在线增量:别名保命令面;旧路径特判保读方;每步 smoke(bus_smoke edge.* 用例 + 新 lock.* 用例·**N争1恰1胜并发 gate**·os-replace-not-atomic 记忆)。

## 4. 回滚触发线
① `edge-acquire/release` 输出与基线 diff≠0 → 停;② 并发 gate 出现 >1 胜者 → 停;③ 外部 IM agent 任一工具调用报路径错超过迁移窗 5 分钟 → 复制回源;④ bus_smoke/ui_smoke 转红。

## 5. 验收(Master数分)
a `ab lock` 并发 N=20 争 1 恰 1 胜(3 轮);b edge-* 别名 JSON byte-identical(acquire/release/timeout/steal 四态);c 空载:移走 biz_tools + 角色页 shared_resources → daemon/dispatch prompt 零外部 IM 词(grep)·bus_smoke 全绿;d 有载:外部 IM agent 真跑一次 external_im_msg(经新路径·持锁)成功;e agent_fleet git 核心目录业务词:edge.py 24 → 0(lock.py)、worker_daemon/dispatch prompt 外部 IM 词 → 0。

## 6. 消费方知会清单(动刀前)
- 外部 IM:A3 路径变更 + `_lock` 改 `ab lock`;迁移窗预告(时间由外部 IM 在岗时定·先确认它 idle)。
- MasterUI:A4 `edge_lock` → `locks[]`(先双发一版·后摘旧字段)。
- 培训师:BUS_ONBOARDING / AGENTS.md 中 edge-acquire 文案 → `ab lock`(别名仍可用·不急)。
- Master:派活 prompt 通用化后·业务任务的"要碰 Edge"提示来自角色页·请在角色页补齐(归培训师建档/Master 口径)。
