# genesis fork · 2026-09-23 · 掉线"唤醒丢失"根因翻案 + 回执代谢档B(v2.13)

> 实例名=genesis。覆盖 v2.12 以来:框架现状审视 → ③型根因侦查(翻案)→ 三项机制落地;autoproxy 提案搁置留档。

## 1. 「唤醒丢失型」根因翻案(★强烈建议吸收的结论与方法论)
- **结论**:29 例"唤醒丢失"0 例永久丢失。主因=**广播惊群**:一次 fan-out 15 目标→13 wait 同时 fire→13 会话同时开 turn 撞 API 限流(harness 记录 error×15/429×1)→**同步无 bus 动作 ~28min→同步恢复**;守卫 7min 即判掉线=全误催。Master数分定稿口径:纯净窗 36=①忘重挂 6 / ③延迟唤醒 29(惊群 13+零散 16;≤60min 自愈 19·>60min 长延迟 10)/ 环境死 1。**Stop-hook 与守卫的对手是"延迟"不是"丢失"。**
- **方法论**:定性掉线类型须交叉 harness 会话记录的**时间戳与记录类型**(不读正文即可判 turn 有无)+ 后台任务输出目录(是否 harness 跟踪);bus events 单侧看不出"延迟"。docs/WAKELOSS_ROOTCAUSE_20260923.md。
- **agent 自述根因不可信**(Master/Master数分都把忘重挂归因环境;Master数分"82%忘重挂"又 overcounts)→ 先扫尸检分型再开药。

## 2. 三项机制(bus/core.py · bus/identity.py · config)✅平台面
- **守卫两级窗**:fire 后 <30min 不催 / 近 5min 有 turn 活动不催 / ≥30 催 / ≥60 且无 turn 加【真掉线】标(=派发方改派判据);尸检 ③a 真无 turn / ③b 有 turn 无 bus 动作;画像加「催单 ≤60min 自愈」标签(7 天 54 催 35 自愈=65% 误催)。
- **send 广播错峰 jitter**:同发送方 30s 内 ≥3 条→每条随机延迟 5~15s;`--kind urgent`/`AGENTBUS_NO_JITTER` 免。
- **回执代谢档B**(Master数分三闸全签):L1 `--kind fyi`⇒`no_receipt`(`--receipt` 显式要回执;fyi 含索取语软警示);L2 wait 纯回执去抖合并领取(非回执零延迟;窗满一次领全部,首条+`coalesced`);`notice_debounce_by_role` 灰度段1 MasterAgent=300。数据:回执数≠唤醒数,验收基线=notice-claim 事件;原档B"grace 自动 done 卡住回执"经数据证为非问题(7 天全舰 6 条)。测试:agent_bus/tests/scenario_noticeB_*.py 经 bus_smoke `@py` 分派(50→52)。

## 3. dispatch resume 劫持修复(claude_run.py + hook)✅平台面
`--resume-session` 一律 `--fork-session` + 子进程摘 `CLAUDE_CODE_SESSION_ID` + 标 `AGENTBUS_DISPATCH=1`;C′ hook 见标即豁免。此前任何 resume 子会话都可能把原 role 的 presence/账本绑到子会话。

## 4. 搁置留档(⏭实例面)
autoproxy-on-wake-loss(守卫自动起代理替掉线 agent 领活)——用户令「现在只考虑使用在线 agent」搁置;Master数分三闸裁定(条件签+4 硬改)录 docs/PROPOSAL_autoproxy_wakeloss.md §10 作复签清单。价值判断同 daemon 回退:执行主体必须是在线 agent 本身。

## 5. 其他
框架现状审视 docs/FRAMEWORK_REVIEW_20260923.md;托盘球在跑/静默列名;status verdict「干活中」不误记静默;机制注册表 113→115。
