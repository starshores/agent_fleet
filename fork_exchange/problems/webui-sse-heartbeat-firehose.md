# 问题:SSE watchdog 递归监听全 bus → 心跳文件 firehose → 客户端狂刷"卡"

> 记录人 MasterAgent · 2026-08-11 · 状态 = **诊断完成·修复中**(MasterUI lane·派单 1786440692309-MasterUI)
> 可吸收性 = **平台面**(任何 genesis 派生 bus 的同款 SSE watcher 同病)

## 一句话
协调 WebUI 的 watchdog 递归监听整个 bus 目录,`on_any_event` 对任何 `.json` 变化都 `_bump()`→SSE `notify_all`→所有浏览器收 "change" 重渲染。但只排除了 `fleet_status.json`,**漏排 `presence.json` / `wait.reg.json` 两个每 10s×N 角色的心跳文件** → SSE change ~3 次/秒全是心跳噪声 → 客户端被狂刷 = "过一会打开控制台会卡"。

## 现象
用户续报:BI 周期卡修好后,"过一会再打开控制台还会卡"(与 BI 是两条独立路)。表现为界面持续/间歇卡顿,非仅 BI tab。

## 根因(实测)
- `ui/api.py :: start_watcher()`:`obs.schedule(H(), str(BUS), recursive=True)` 递归监听整个 bus 目录;`H.on_any_event` 对任何 `.json` 事件 `_bump()`。
- `_hit()` 只把 `fleet_status.json` 返 False,**漏排心跳噪声**:`presence.json`(每 10s×~20 角色写心跳)、`wait.reg.json`(wait 注册表新增·每 10s×~20 角色写心跳)。
- 每次 `_bump()` → `_change_cv.notify_all()` → 所有 SSE 连接推 "data: change" → 客户端 JS 重新拉取/重渲染当前视图。
- **实测(12s 窗口)**:36 次 `.json` 变化 = **3.0 次/秒** SSE change,其中 `presence.json`=18 + `wait.reg.json`=18,**100% 是心跳噪声、0 条真 UI 事件**。`wait.reg.json` 精确翻倍了 `presence.json` 的负载(→"现在才明显")。
- server 稳态 CPU 被顶到均值 ~40%(0–92% 抖动),空闲服务器不该有。

## 可吸收性:平台面(可吸收)
纯 WebUI SSE 机制,与本机路径无关。任何带同款 watchdog-SSE 的 genesis 派生 bus,只要有 per-role 心跳文件(presence/reg)写在被监听目录下,就同病。吸收方自查:12s 内被监听目录 `.json` 变化率是否远高于真 UI 事件率。

## 吸收单元 / 修法
`_hit()` 把 `presence.json` 和 `wait.reg.json` 也返 False(与现有 `fleet_status.json` 排除【完全同类】——都是"自己写的计时/心跳文件,不触发 SSE 否则刷屏")。
- presence 的真变更(上下线 / current_task)由 fleet 刷新器 20s 周期 + send/done 显式 `_bump()` 覆盖;wait.reg 纯自醒心跳,UI 无需实时。
- 安全网:给 `_bump()` 加去抖(coalesce ~1–2s),防将来别的高频 `.json` 再成 firehose。
- `turn_state.json` 未列入排除(真实时值·只在 agent 活跃时写·当前 0 噪声);活跃期若吵可靠去抖收敛。
- 涉及文件:`ui/api.py :: start_watcher._hit`(+ 可选 `_bump` 去抖)。
- 吸收 commit:**待 MasterUI 提交后回填**。

## 验收
- 改后同款实测:空闲期 12s 内 `.json` 变化触发的 SSE change **≈0**(心跳不再触发)。
- server 稳态 CPU 显著下降。
- SSE 真事件(send / done / 新任务 / 路由)仍即时推;`ui_smoke` 保持全过。
- 平台三问:①归属=平台机制 ✓ ②清除=无业务数据 ✓ ③换域=不需改平台代码 ✓。

## 关联
- 与 [[webui-bi-endpoint-gil-freeze]] 同为"WebUI 卡"的独立成因;BI=请求线程 GIL 冻结(已修 210b7c2),本篇=SSE 心跳 firehose 狂刷客户端。两者叠加才是用户完整体感。
- 本 bus 侧注:`wait.reg.json` 10s 心跳是 MasterAgent 的 wait 注册表设计(去语境化教训:心跳/计时文件别写进被 SSE-watch 的目录、或写了必在 watcher 排除)。
