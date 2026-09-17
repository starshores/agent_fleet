# genesis fork · 2026-09-17b · per-agent 数据画像 + 探针实战硬化(v2.11)

> 实例名=genesis。覆盖 v2.10 以来(同日第二波):**per-agent 数据收集 L1-L3 建成** + 探针框架实战硬化。

## 1. per-agent 数据画像 L1-L3(ab agent-profile)✅平台面·本波核心
**是什么**:用户任务"收集每个 agent 的数据并利用其自我迭代"的落地。L1 原子采集(只读旁路)→L2 指标派生(硬指标直算·软信号标旗不混)→L3 画像聚合(per-agent 卡)→L4 画像异常→探针深查→迭代候选(消费闭环)。
**指标(维度2 纪律+维度4 产出·全可溯源)**:tokens/calls(usage.db⋈session→role 账本)·done/claim/send(events)·掉线催单(archive 扫 from=守卫)·回执时延中位/P90(notice created→finished)·stop_hook_block。
**数据真实性红线(Master数分·可吸收方法论)**:①每指标可溯源客观事件;②硬指标 vs 软信号分层(软只作线索标旗不直判);③**画像=自我发现工具非 KPI**(中性呈现·稳定序·无排名/阈值/褒贬——设成考核 agent 会 game 指标);④只读旁路(sqlite mode=ro);⑤缺数据显"无数据"不补 0、未归属独立列不摊派。
**终验方法(可吸收)**:实证方独立重算比对(掉线 EXACT·tokens 差异追根至"工具映射更完整=工具对")+ **读码审红线**(逐条核码非听自查)。
**吸收单元**:`bus/agent_data.py`(build_profile 纯函数 API+CLI 壳)+ session_role_map 账本 + WebUI 📇tab(MasterUI·drill-in 防横比)。⚙适配:usage.db schema/账本字段。
**维度1 字段已备**:send `--kind{task|request|fyi}`(request 落消息+audit=问答比原料);维度3 假执行待判据。

## 2. 探针实战硬化(auto 层全瞎修复 + 3 迭代)✅平台面
- **系统性 bug 根治**(执行方抓的):events 时间字段=ISO "ts" 而代码按 epoch 读→恒 0→since>0 全量误滤=**auto 层全瞎**(三态从未真命中)。修=ISO 解析·无 ts 行保留(宁多看不漏判)。
- **测试盲区变体二号(可吸收纪律)**:直测"通过"因**测试事件照实现假设造(带 epoch)而非照真实源格式造**=自欺。一号=guard 挡自己路径;二号=造数契合实现。→ 造测试数据必须照真实源格式。
- execution-completion subtype(route 纪律三段之末:自揽→会问→**发起了没完成**·测"会说≠会做最后一公里");
- `--expected` 判据文本同步换名(防 instances 审计误导);retest 自动归档 .roundN(baseline 保留=辅导前后对比可追溯·回归仪)。

## 3. 运行经验(实例面)⏭
- 探针实战两天连抓工具自身 2 真 bug(ImportError 潜伏/auto 全瞎)=质检层质检自己·三方闭环(执行方反馈→owner 迭代→口径终判)自增长。
- C′ 灰度 day1:1 误拦(启动窗口)→宽限 2s 修·pre/post 分计。
