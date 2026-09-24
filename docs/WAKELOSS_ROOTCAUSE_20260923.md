# 「③唤醒丢失型」掉线根因侦查 · 2026-09-23(MasterAgent)

> 对象:09-18 三分型 instrument 上线后全部 29 例③型守卫催单。方法:bus events/archive(动作面)× 本机各 agent 会话记录的**时间戳与记录类型**(harness 面·不读正文)× 后台任务输出目录(是否 harness 跟踪)。四轮脚本在 scratchpad(wakeloss_probe1-5.py)。

## 结论(一句话)
**③型基本不是"唤醒丢失",是"延迟唤醒 + 守卫窗口过短"**:29 例里 23 例最终 done+rearm(慢 11~177 min),6 例"6h 无 done"中 5 例 fire 后 5 分钟内有工具活动=在忙(长任务)或以 send 回应;真正"通知入队却无 turn"仅 1 例(且 +40min 自行恢复)。**零例永久丢失。**

## 主因:广播惊群(thundering herd)→ API 限流退避
- 09-18 13:50 我 fan-out 一条 fyi 给 15 个 agent → 13 个 wait 同时 fire → 13 个会话同时开 turn。
- 13 个会话**同步无 bus 动作 27.4~28.6 min**(始于 13:50:37-50),14:19 **同步恢复**;窗内 system 记录 error×15、429×1。Master数分独立核:13:51:02 后**全舰零 bus 事件**(连 MasterAgent 自己)→14:18:15 同步恢复=账户级限流/退避形态;各会话并非完全同构(Master数分自身窗内有一轮 turn 无 bus 动作),故表述为「同步无 bus 动作」而非「同步静默」。
- 守卫 +6.5~25 min 判定"掉线"→ 13 条催单 + toast 全误报。占③型 **13/29**。
- 13:50 之外的同类:09-18 14:24 我再广播细则(16 个)→ 又一批延迟;日常 request 也可能撞限流,只是不成批。

## 次因
| 类 | 例 | 说明 |
|---|---|---|
| 延迟唤醒(非批) | ~7 | 会话数小时无记录后自行恢复(+84/+137/+177 min)·疑同类退避或机器/tab 休眠·均最终 done+rearm |
| 长任务在忙被误判 | ~5 | fire 后 5 min 内 Edit×4 / Bash×7 等=在干活;守卫 busy 判据 turn_state<15min 对长 turn 不够 |
| 通知入队无 turn | 1 | 调度 agent 09-22 +40min 自行恢复 |
| 账本缺口 | 3 | 当时 sid 不在账本(另议) |

## 修正方向(提Master数分口径·MasterAgent 实现)
1. **广播必须错峰(jitter)**:send 多目标/fanout 时按目标序错开(如每目标间隔 5~15s 随机)·或在 wait fire 侧加随机 0~30s 延迟——根治惊群。**平台层缺陷·我 lane。**
2. **守卫两级窗(Master数分定)**:fire 后 <30 min 不催(延迟中位 24~29 min);fire 后 5 min 内有 turn/工具活动 → 在处理·不催;**30 min = 催单+toast 阈;60 min 且无任何会话记录 = 「真掉线」升级**(给 Master 拉活/改派的判据)。阈值进 config 不写死;灰度两周后按误催率再调。
3. **三分型尸检精化(Master数分口径)**:两维记 [a|b] × [恢复延迟分钟|未恢复]——③a 真无 turn(会话无新记录且 turn_state ts 未变)/ ③b 有 turn 无 bus 动作(在忙或纪律);「延迟恢复」是事后结局标签非第三类。回填:催单后 ≤60min done+rearm → 加标「延迟唤醒·误催」(带 src·保留原判);>60min 保留「长延迟」。
4. 口径第三次修正(Master数分 781e5d 定稿):纯净窗 36 = **①忘重挂 6 / ③延迟唤醒 29(惊群批 13 + 零散 16;其中 ≤60min 自愈 19、>60min 长延迟 10)/ 环境死 1**。真③永久丢失 ≈0。>60min 的 10 例**不叫误催**(用户不在场时对派发方等价掉线·含 Master数分/示例组员4 40h 那类)。C′ 只治①(6/36≈17%);「掉线趋零」验收改测【①型趋零 + ③长延迟(>60min)计数】,≤60min 自愈不计掉线。**C′ 与守卫的真正对手是「延迟」不是「丢失」。**

## 对 Master 的用法含义
"⚠掉线" 在 fire 后 30 min 内多为延迟唤醒·会自愈;别在 7 min 时拉活。真掉线判据=fire 后 >60 min 且 turn_state idle 且无任何动作。

## 补遗(2026-09-24)· 第④型:harness 内存回收 → 孤儿 wait 仍 claim → 悬单
- 实锤 2 例(Master数分 15b5e5 独立核·会话 jsonl task-notification 原文):MasterUI 17:58 / Master数分 18:32 后台 wait 的 bash 包装壳被 Claude Code 低内存回收;python wait 成孤儿仍在 18:05 / 18:38 claim 消息(events 有 claim 无 turn)→ 消息烂 processing 15h。harness 通知明令勿自重启,agent 未重挂=当时正确行为·不计纪律·不计 C′。
- 我(MasterAgent)首判①忘重挂并扩 C′ 段③到协调位=误判(自述根因不可信的反向实例:环境因被归纪律);Master数分驳回·config 已回退段②。
- 四型口径:①忘重挂(C′ 治)/ ②C′误拦 / ③延迟唤醒 a·b(守卫两级窗)/ ④harness 回收·孤儿 claim(本补遗·真修如下);环境死并入④。
- 真修(同日落地):`_claim` 记 claim_pid/ppid;wait 心跳线程父壳 create_time 自检→父亡自退不再 claim(治本);`_requeue_orphans` 在每次 wait 起手把悬单退回 inbox(新 wait 立刻领到);reap 同判据退回。★悬单判据(Master数分 d21617 热修):listening 角色 processing 消息【claimed 后该会话无 turn 活动 ≥ orphan_no_turn_min(30)】=输出未送达;**不能**用 claim_pid 存活判(claim_pid=wait 自身 pid·正常 claim 后即退=恒亡·反了会把在办消息退回=双处理·smoke 曾漏测『正常 claim 后 wait 退出』态);selfwake/who 标 ⚠悬单N;守卫对忙而聋态不投 inbox 催单(白投)只 toast。smoke deaf.busy / deaf.orphan。
