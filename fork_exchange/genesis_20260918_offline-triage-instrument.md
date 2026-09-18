# genesis fork · 2026-09-18 · 掉线三分型 instrument + selfwake 抗误报(v2.12)

> 实例名=genesis。覆盖 v2.11.1 以来:L4 第二轮自我发现(C′ day1 数据侦查)产出掉线口径的一次结构性精化。

## 1. 掉线三分型口径(Master数分 R1·强烈建议吸收的方法论)✅平台面
**发现路径**:C′ Stop-hook 灰度 day1 数据侦查——Master 被 block 后仍出一条催单,时间线钉死(events:fire→处理→done→**重挂了**→新 wait fire→此后无任何 turn 事件→7min 后守卫催)= 新分型【**fire 后唤醒丢失型**】:wait 尽职、agent 纪律尽了、hook 逻辑没错,丢在 harness 唤醒层。
**三分型验收公式**(Master数分裁定):
- ①忘重挂型(fire 后无重挂=真纪律缺口)→ Stop-hook 应治,"掉线趋零"只测这型;
- ②hook 误拦(armed 却被 block)→ 误拦率 <5% 指标;
- ③唤醒丢失型(重挂了但 harness 未唤醒=无 turn,hook 零机会)→ **不算 hook 失效**,守卫周期兜底是该层正确答案。
**诚实修正**:此前"82% 忘重挂"overcounts——crude 尸检文案对①③同写"正常退出后未重挂",两型混计。含义:Stop-hook 类机制的天花板 = 治①型 < raw 比例;③型残量永在,靠守卫,别误判为机制失败。

## 2. rearm 事件 instrument(bus/core.py)✅平台面
- `_serve_wait` reg 登记成功即落 events `{"event":"rearm","role","wait_pid","ts"}`(append-only 可回溯;reg 是覆盖写不留史)。
- 从此掉线可自动三分型:fire→rearm?→action?(无 rearm=①;有 rearm 无 turn=③)。

## 3. 守卫尸检三分型(bus/identity.py `_wait_autopsy`)✅平台面
- exit 正常后交叉 events 尾 512KB 查 turn 代理动作(done/send/rearm;**claim 不算**=可能是 wait 进程 _try_claim 所为非 turn)——零动作=「疑唤醒丢失型(非纪律·守卫兜底层)」/有动作=「忘重挂型」。催单文案自带分型=验收 archive 扫描直接分桶。
- fail-conservative:events 读失败按忘重挂催纪律(宁催不假报)。

## 4. selfwake 抗误报:busy 交叉 turn_state(bus/selfwake.py)✅平台面
- 原判定只看心跳+wait 进程 → turn=tool 活跃干活中被误标 ⚠掉线 → 误导协调位发拉活/催单(实证:Master 对 cpu 6% 活跃中的 示例组员2 误发拉活)。
- 修:stale 判定前交叉 turn_state.json——state∈{tool,thinking} 且 ts<15min → recent「turn 活跃干活中(非掉线)」;turn 陈旧/idle/读失败回落 stale。
- Master数分复核:此版=其双条件口径 superset,顺带补了「死在 tool 态漏抓」盲点(15min 宽限后仍活跃态=疑 hang=判 stale)。

## 5. 其他(⏭实例面为主)
- bus_mechanisms.json 111→113(补登 agent-profile 画像/session_role_map 账本;更新 selfwake/C′ 条目);BUS_GUIDE §3 同步。
- C′ day1(Master 段):block 1 真拦·误拦 0·无连环 block=好开局。
- 记忆:selfwake-drop-misattributed-to-env(agent 系统性把忘重挂误归因环境——真相=mix,crude 尸检切不开,须 events 交叉)+ test-blindspot-three-series(测试盲区三系列合订)。
