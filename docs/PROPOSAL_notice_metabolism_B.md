# 三闸提案 · 回执代谢档B 重定义:fyi 免回执 + 回执去抖合并领取

> 提案方:MasterAgent · 2026-09-23 · 依 SELF_REDESIGN_CHARTER(两铁律·三闸·预声明回滚线)。签字方:Master数分(kind 维度口径 owner·档A/档B 原口径作者)。消费方:全员(wait 行为契约变化)·重点 Master/MasterUI/培训师(协调位)。
> 状态:**三闸全签(Master数分 aef87e·2026-09-23)→ 动工**。闸①附两条件:C1 `--receipt` 显式开回执(fyi 默认免回执但广播类「谁读了」可拉查 `ab replies`/或显式推);C2 fyi 文本含索取语 stderr 软警示(不拦)。出处:FRAMEWORK_REVIEW_20260923 方向 3。

## 0. 数据(近 7 天·kind=notice 且 notice_of 硬签名)

| 协调位 | 回执数 | 源 kind(task/fyi/request) | claim→done 中位 | 卡 processing>30min | 去抖 90s 省唤醒 | 去抖 300s 省唤醒 |
|---|---|---|---|---|---|---|
| MasterAgent | 82 | 30 / **45** / 7 | 0.3 min | 0 | 44% | 65% |
| Master | 235 | 200 / 29 / 6 | 0.4 min | 4 | 35% | 64% |
| MasterUI | 25 | 25 / 0 / 0 | 0.5 min | 2 | 24% | 64% |
| 培训师 | 27 | 27 / 0 / 0 | 0.2 min | 0 | 15% | 60% |

**结论**:① 原档B 设想「grace 自动 done 卡住的回执」治的是非问题(全舰 7 天仅 6 条卡 >30min);② 真消耗 = 回执引发的唤醒 turn;**口径修正(Master数分)**:回执数≠唤醒数——notice 类 claim 事件才是唤醒代理:**MasterAgent 33 / Master 149 / MasterUI 14 / 培训师 16**(MasterAgent 73 条回执只 33 次 claim,其余被 peek/ack-notices/同 turn 顺带清);上表「省唤醒%」按回执条数算偏乐观,**验收基线用 notice-claim 事件数**;③ Master数分 0% 无秘诀=它 7 天只外发 1 条。

## 1. 机制(两层·都不动消息内容)

**L1 源头:`--kind fyi` ⇒ 自动 `no_receipt: True`**。fyi 语义本就是"知悉即可·不需回执";完成事件仍进 events.jsonl 审计。MasterAgent 的 45/82(55%)回执直接消失;顺带把"该标 fyi 却没标"的纪律显性化(不标 fyi 就会收回执=自带反馈)。
**L2 接收端:回执去抖合并领取(debounce + coalesce)**。wait 的 `_try_claim` 遇到最老可领消息是 **纯回执**(kind==notice 且 notice_of)时,不立即 fire:开一个 `notice_debounce_s` 窗(config·默认 90s·per-role 可覆盖·协调位可设 300s);窗内 **任何非回执消息到达 → 立即 fire**(任务零延迟不变);窗满 → **一次领取 inbox 里全部纯回执**进 processing,wait 输出一条 digest(N 条回执·各 id/from/status·提示 `ab ack-notices` 一键清)+ 逐条 JSON。
**L3 已 claim 卡住的纯回执(原档B)**:数据显示非问题 → 不做自动 done;保留档A 人工 `ack-notices`。

## 2. 三闸自评(待Master数分独立签字)
- **闸① 核心价值**:强化协作——协调位 turn 从"搬回执"释放给真协作;不削弱可打断——非回执消息零延迟、回执延迟上限=窗长(默认 90s)且 `ab request` 同步等待读 done 文件不经 notice、不受影响。风险=回执晚 ≤90s 才被读到;request 类结果若急,发起方本就用 `ab request` 同步。
- **闸② 分离三问**:纯协调机制·零业务内容·阈值走 config·换域照用。✓
- **闸③ 在线增量**:纯新增分支(仅当最老可领是纯回执时进入去抖;否则旧路径 byte-identical);回滚= `notice_debounce_s=0`(一行)+ fyi no_receipt 开关 `fyi_no_receipt=false`。

## 3. 预声明回滚触发线
1. 任何非回执任务被延迟 fire(events: send.ts→claim.ts >5s,**限该 role wait armed 期间**;jitter 在 _deliver sleep 后才写 inbox+audit·send.ts 不受污染)≥1 例 → 停(设计上不可能·出现即 bug)。
2. 回执被合并领取后 ≥1h 未 done 的数量 > 旧基线(6 条/周)2 倍 → 说明 digest 没被处理·停。
3. `ab request` 同步路径 P90 时延上升 >10% → 停(理论无关·作监控)。
4. **notice-claim / 非 notice-claim 比值**未下降 ≥20%(两周窗·归一化排除工作量波动)→ 收益不成立·撤。

## 4. 验收指标(Master数分 L级B·两周·aef87e 定)
a 协调位 notice-claim 事件数 vs 前 7 天基线(MasterAgent33/Master149/MasterUI14/培训师16)降 ≥40%;b fyi 源回执归零(`--receipt` 显式开的单列);c 非回执任务 send→claim 时延(限 armed 期)P50/P90 与基线差 <5s;d 回执 claim→done 中位不劣化;e 误标 fyi 软警示触发数(观察项)。另:守卫不得把「inbox 有未领纯回执 + wait armed」判为卡住(smoke 加例)。

## 5. 实现点
bus/core.py `_wait_core._try_claim`:最老可领为纯回执 → 记 `debounce_until`;`arm_watch` 事件循环超时改为 min(剩余窗, 原超时);窗内新文件事件重评估(非回执 → 立即领);窗满 → `_oldest_claimable` 循环领所有 notice(共用 `_claim` O_EXCL)→ 输出 digest。cmd_send:`kind==fyi` → `task["no_receipt"]=True`(config `fyi_no_receipt` 默认 true)。config:`notice_debounce_s`(默认 90)、`notice_debounce_by_role`({role: s})。smoke:①纯回执去抖·②窗内任务即时 fire·③窗满合并领取 N 条·④fyi 无回执·⑤debounce=0 回旧路径 byte-identical。

## 6. 灰度
段1:MasterAgent 自己(notice_debounce_by_role.MasterAgent=300)+ fyi 免回执全员(源头·风险最低);1 周 → 段2 Master(90)/MasterUI/培训师 → 全员默认 90。
