# genesis fork · 2026-09-17 · C′ Stop-hook 自醒硬拦 + 探针实战迭代(v2.10)

> 实例名=genesis。覆盖 v2.9.1 以来:**掉线根治的数据驱动全弧** + 探针框架首波实战迭代。

## 1. C′ Stop-hook 自醒硬拦 ✅平台面·本波核心
**问题(数据实证)**:3 个月 351 次掉线催单·20 role 全中=系统性;分型 **82%=忘重挂**(done 后没挂·提醒穿透 284 次=程序约束衰减)/15%=会话 teardown;守卫兜底长尾差(P90=63h·31%>1h=掉线期消息躺 inbox)。→ 结论:durable 自醒(只治 15%)降级观察,**复活 Stop-hook 治 82%**。
**机制**:Stop hook 回合结束查 armed→无则 block 令重挂。当年 SHELVED 根因(psutil 扫进程启动竞态+无法区分刚 fire→误拦)已修:
- 判据=**读 wait 注册表双文件**:armed = `wait.reg.json` present+status==listening+wait_pid 活;兜底=`wait_trace.json` exit==null 且 pid 活;grace=trace.exited<15s(fire 唤醒窗口放行);
- **宽限重查 2s**(灰度首日实抓的新竞态:回合末 run_in_background 挂 wait→回合秒结束→hook 查在 reg 写出前 1-2s=误拦。no-armed 时 sleep 2s 二查·armed 路径零开销);
- fail-open(任何异常放行绝不卡回合)·GUARD_WINDOW 120s 防死循环·block 写 events 留痕(`enforce_selfwake_block`)·回滚=settings 一行摘除。
**灰度纪律(可吸收方法论)**:白名单二段灰度(先拦机制 owner 自己=误拦成本自担→+最忙 role 代表性验→全员);验收指标预定(误拦率<5%=block⋈trace.exit·基线掉线数趋零·连环 block 即回滚);**pre-fix/post-fix 分开计**(首日误拦记录在案透明·回滚线适用 post-fix steady-state)。
**吸收单元**:`hooks/enforce_selfwake.py` + settings Stop 注册。⚙适配:GRAY_ROLES 白名单/reg 字段名。

## 2. 探针框架首波实战 + 精度迭代 ✅平台面
- **首波 4 探针 4/4 行为 PASS·0 真 FAIL**=舰队方向性健康首个实证基线(会问纪律经 8-28 强化后真落地)。
- 执行方(培训师)反馈驱动两改进:①`--expected` 对口覆盖+lane 自核提醒(治「场景标的换 lane 而对口模板残留→auto 误报」);②route_to **三态细分**:PASS / **FAIL 候选(execution gap)**=回派活方未真 fire(会说≠会做)/ FAIL 候选(疑自揽)——两种方向性错误分开报。
- **三方工具闭环模式(可吸收)**:执行方实战反馈→机制 owner 迭代工具→口径 owner 终判语义——探针框架自增长。

## 3. 治理/质量面 ✅平台面
- **bus_smoke 收编质检仪器 7 用例**(43→50):核心=**guard/绕行成对用例纪律**(加防护 guard 必配「guard 拦住+合法绕行真跑」两条·防 guard 挡住自己测试路径致真分支零覆盖=ImportError 潜伏教训)。
- 机制注册表 96/13 类→**111/14 类**(新增类14 实证质检);BUS_GUIDE 操作说明面追平 + 自我迭代闭环节。
- 纪律沉淀:CJK 文本经 bash 传递时换行符一律写字面「换行符」(转义断行连犯两次)。

## 4. 运行经验(实例面)⏭
- notice 代谢档A(ack-notices)投产采纳(Master 启用);per-agent 数据收集立案(Master数分 4 维口径·L1-L4 架构·等 schema 实核);大协作主题构想记档(用户令:先数据+几轮自我发现再建)。
