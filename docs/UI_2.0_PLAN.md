# UI 2.0 优化改造 · 版本方案(同 BUS 2.0 策略)

> 2026-07-29 用户立项:「下一个版本是 UI 优化改造,同 2.0 的策略」。
> 策略移植自 BUS 2.0:**金标准先行 → 分阶段推进 → 每阶段成文决议 → 1+1 实现/验证配对 → 收口留根**。
> 范围底稿 = MasterUI 已草拟的 `ui/WEBUI_UPGRADE_PROPOSAL.md`(P0 morphdom 全铺 / P1 一致性 / P2 route 防误派)
> + `ui/REACTIVE_MIGRATION.md`(morphdom 决策 + 待办池 pilot 已实测)+ BUS 2.1 遗留(F8/B2/D6)。

## 现状基线(2026-07-29)

- `ui/` 5953 行:page.py 2823 / api.py 2607 / routes.py 228 / server.py 176 / common.py 119。
- 端点面:26 GET + 11 POST + SSE(routes.py)。
- morphdom 已内联、待办池 pilot 通过(刷新不闪/焦点不丢),其余面板仍 `innerHTML=` 整体重建。
- BI tab 过载(五块口径不同挤一个 tab);loadBI 不看 tab 可见性每 10s 全刷。

## 阶段划分(U0→U4·每阶段冒烟双绿 + Master数分验证绿才进下一阶段)

| 阶段 | 内容 | 主实现 | 验证 | 备注 |
|---|---|---|---|---|
| **U0 金标准** | `ui_smoke.py` + 基线:GET 端点全量 200+JSON 键 / 页面关键面板锚 / 安全 POST(400 校验·supervise dry-run)/ SSE 连通;基线比对(端点消失=回归)。★Master数分审计补三缺口后 38→69 用例(565c133),Master数分绿灯已出 | MasterAgent(✅完) | Master数分(✅绿) | 对应 bus_smoke;每阶段必跑 |
| **U1 morphdom 全铺** | ①舰队总览(M·最高收益) → ②BI/派工/会话/活动只读表(S×n) → ③用量表格周边(canvas 不 morph)+ live.log 保滚动位 | MasterAgent | Master数分逐面板 | 模式=待办池样板;一面板一上不阻断 |
| **U2 一致性** | 历史用量拆独立 tab;刷新按【可见 tab】门控(省 psutil/token);0值/空折叠口径统一 | MasterAgent | Master数分 | 提案 §2 |
| **U3 route 防误派** | `/api/route` 后端(shell ab route)+ 派活表单"建议对口"chip | MasterAgent | Master数分 | 有真实误派事故;nicety 排后 |
| **U4 拆分收口** | page.py 拆分(F8 遗留·2823 行·同 P4 纯搬运契约);清 B2(start_maint 周期 reap + todos-archive)/ D6(cost 口径);收口决议成文 | MasterAgent | Master数分 | 结构收官 |

> **★分工变更(2026-07-29 用户令)**:UI 实施(U1-U4)由 MasterAgent 主实现——长程改造任务对模型等级敏感,MasterUI 模型等级低不宜担纲;MasterUI 保留 WebUI 运行位 owner(start_ui/常驻控制台/B2 运行侧),MasterAgent 改 ui/ 代码按对称律每阶段知会 MasterUI。

## 铁律(从 2.0 原样继承)

1. **纯搬运阶段契约不变**(路由/端点响应/口径逐字保持,P4 判例)。
2. **每阶段 ui_smoke + bus_smoke 双绿**才进下一阶段;基线只增不减(端点/用例消失=回归)。
3. **每阶段成文决议**落 `docs/`(范围/取舍/验收证据),不留口头账。
4. **1+1 配对**:实现方自测 ≠ 验收;Master数分逐视图实测绿灯(「验人看路径非 --json」·role-verdict 判例)。
5. **MasterUI↔MasterAgent 对称律**:server/api/page 契约、agentbus 公共面改动,谁改谁通报对方。
6. **canvas/命令式子树不 morph**;key 用业务 id 不用下标(提案已列坑)。

## 分工与节奏

- **MasterUI**:U1/U2 主实现(它是 WebUI owner + 提案起草人);从 U1-①舰队总览起手。
- **MasterAgent**:U0 金标准(已启动)、U3 端点、U4 拆分架构、每阶段决议归档。
- **Master数分**:每阶段验证绿灯(2.0 P4 模式)。
- 用户拍板点:无前置阻塞(策略已由用户定);阶段内如遇取舍投 `ab ask-user` 非阻塞待办池。
