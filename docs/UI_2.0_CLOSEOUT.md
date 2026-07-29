# UI 2.0 收口决议(2026-07-29 · MasterAgent 主实现 · Master数分逐阶段验证)

> 立项:用户 2026-07-29「下一个版本是 UI 优化改造,同 2.0 的策略」;当日「一直到完成,我明天来看结果」。
> 方案 `docs/UI_2.0_PLAN.md`;分工变更:U1-U4 实施=MasterAgent(用户令·模型等级考量),MasterUI 保留 WebUI 运行位。

## 交付总览(全部当日闭环)

| 阶段 | 交付 | commit | 机器验 | Master数分验 |
|---|---|---|---|---|
| U0 金标准 | ui_smoke.py 38→69 用例(Master数分审计三缺口补齐) | 5cc98c4/565c133 | 69/69 | ✅绿(逐项复验) |
| U1 morphdom 收尾 | uProj 钻取/prompts 展开【状态驱动化】+ ctxKpis/procFoot/archive 汇总 morph 化 | b39350d | 69/69 | ✅绿(白盒·结构保证级) |
| U2 一致性 | 派工效率死码面板复活(dm* 容器从未落·git -S 佐证)+ 审计实况记录 | 95b6e65 | 73/73 | ✅绿(数据源核实) |
| U3 route 防误派 | route_scores 单一源 + GET /api/route + 派活表单建议对口 chip | be3aa07 | 77/77 | ✅绿(CLI/API 直接复现一致) |
| U4 拆分 | page.py 2875 行 → 装配层 25 行 + css/body/js 三分(sha256 字节级一致) | 3eee64e | 77/77+43/43 | 见 U4 验单 |

## 关键决议与实况澄清(防后人按提案误判)

1. **提案 vs 实况**:提案(WEBUI_UPGRADE_PROPOSAL.md)成文早于现状——舰队/daemon/事件/回填/BI/派工JS/caps/ctx/archive/sessions 的 morph 化、历史用量独立 tab、可见性门控(showTab 进 tab 拉数 + bi/caps 循环按 curTab)在本版本前已落地。本版本做的是**残留清障**,审计结论如实记录、不虚报工作量(data-truthfulness)。
2. **状态驱动教义落地**:命令式注入子树(uProj 钻取、prompts 展开)一律先改 state(openProjRaws/openPrompts Set)再 morph——展开态是渲染的纯函数输入,刷新不丢是**构造保证**(Master数分 U1 白盒定性)。
3. **innerHTML 合法残留**:morph 异常兜底、一次性 error/loading 占位、canvas 旁 legend/tooltip 小元素、health 点击弹层——无态无周期刷新,不闪,不改(改=无收益风险)。
4. **主 load() 不门控**:/api/state 喂全局顶栏徽标(连接/在岗/待确认/健康),设计上跨 tab 常刷;门控只针对重面板(psutil/账单类)。
5. **派工效率面板**:renderDispatch/loadDispatch JS 全套存在而 dm* 容器 HTML 从未进过 git(P4 拆分丢失或从未落)——本版本补容器 + showTab('bi') 接线,**不加轮询**(metrics.jsonl 只在任务完成时变,进 tab 拉足够)。
6. **U4 拆分验收标准**:INDEX_HTML 拆前后 **sha256 字节级一致**(比"冒烟绿"更强的纯搬运证明);morphdom vendor blob 留在 page_js(单行、极少改动,单拆无收益)。
7. **B2/D6 不在本版本**:B2(start_maint 周期 reap+todos-archive)/D6(cost 口径)属 MasterUI 运行位遗留,与 UI 代码改造无耦合,留 MasterUI 排期。
8. **route chip 范围**:只做派活表单(send form);待办卡"派给"下拉未加 chip(卡片空间窄 + roleSel 已防冲选),需要时以 U3 同源端点扩展。

## 铁律执行记录

- 每阶段 ui_smoke + bus_smoke 双绿才进下一阶段(全过,无一例外);基线只增不减(38→69→73→77)。
- 每次改 page.py:py_compile + 抽 JS node --check + WebUI 重启(pythonw 常驻,改码必须重启生效)+ 双冒烟。
- Master数分逐阶段异步验证,不阻塞推进;其白盒验证口径(Edge CDP 未起时以代码路径结构验证替代盯屏,诚实标注边界)被采纳。
- MasterUI↔MasterAgent 对称律:实施改派与各阶段交付均已 send 知会。
