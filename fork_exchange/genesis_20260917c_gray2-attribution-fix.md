# genesis fork · 2026-09-17c · C′灰度段2 + 归属账本缺口修(v2.11.1)

> 实例名=genesis。覆盖 v2.11 以来(同日第三波):C′ Stop-hook 灰度扩员 Master + L4 画像第一轮消费闭环实战(发现并根治归属账本机制缺口)。

## 1. C′ 灰度段2:Master 纳入(hooks/enforce_selfwake.py)✅平台面
- **驱动**:Master 请求 durable 自醒("wait 必死于 turn 边界·用户持续看我掉线")→ **按分型数据开药不按自述**:扫其全史守卫催单尸检 = 42 次掉线里 40 次忘重挂型(正常退出后未重挂)+ 仅 2 次进程/边界型,且请求当时它正有跨 turn 活 wait——自述与数据不符,C′(turn-end armed 检查)正对症。
- **口径方(Master数分)APPROVE 要点(可吸收)**:①灰度一次只加一个 busy role=误拦可归因;②user-facing role 误拦可见性最高=最好的段2 代表性 case;③例外触发预声明(误拦率>5% 或连环 block→只摘该 role 不牵连他段)。
- **★跨-agent 洞察(强烈建议吸收)**:两例实证(Master+Master数分本人)——**agent 系统性把「忘重挂」掉线误归因为「环境 teardown/turn 边界」**,连自己掉线根因都诊断不准 → 人肉自醒结构性靠不住 = 重挂必须机制化(Stop-hook)。规则:**agent 自述掉线/失能根因,先扫它的尸检分型再开药**。记忆 selfwake-drop-misattributed-to-env。

## 2. 归属账本机制缺口修(bus/presence.py + worker_daemon + dispatch_agent)✅平台面·L4 消费闭环第一轮实战
- **发现方式=画像自我发现**(用户任务"利用数据自我迭代"的首轮兑现):agent-profile 的 `_unattributed`(429 calls/669K tok)深查 → 分型 = 个人非 bus 会话 + daemon 试验批。
- **根因**:sid→role 账本唯一写入点 = hello/心跳(靠 `CLAUDE_CODE_SESSION_ID` env);`claude -p` 子会话(daemon/dispatch)两者皆无 → 结构性漏记。
- **修**:`presence.record_session_role` 升公共名;worker_daemon/dispatch_agent 在 run_streaming 返回 meta.session_id 后落账(尽力而为·不影响主流程)。⚙适配:在跑 daemon 下轮重启生效。
- **历史回填方法论(可吸收)**:无归属会话的 jsonl 首条 prompt 明写「你是 agent bus 上的【X】角色执行体」= 可溯源客观证据 → 回填带 `src=backfill-jsonl-prompt-<date>` 审计标记;退册 role 也回填(消耗真实发生)带 note。个人/测试会话**不回填**(缺显缺)。效果:_unattributed 降 33%,两个"有 done 无 usage"謎闭环。

## 3. 测试盲区三号 + 三系列纪律(bus_smoke.py 3a216a3)✅平台面
- 三号=**setup 自毁测试条件**:smoke"非成员被硬拒"用例先 register 了测试角色→它成了成员→真投垃圾 todo 到用户待办池。修=用真不在册的 `__smokeq__` + teardown 防御清扫。
- **三系列合订(纪律·记忆 test-blindspot-three-series)**:一号 guard 挡自己测试路径 / 二号造数照实现假设非真实源格式 / 三号 setup 自毁前置。同根=测试没走真实路径;写测试前三问。

## 4. WebUI 📇数据画像 tab(MasterUI·c0cab28)✅平台面
- L3 画像的渲染面:per-agent 卡 drill-in(防横比=非 KPI 红线③的 UI 落地);契约=build_profile 纯函数(零 print)。
