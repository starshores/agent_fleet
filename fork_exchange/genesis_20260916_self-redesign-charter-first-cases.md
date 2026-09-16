# genesis fork · 2026-09-16 · 自我迭代宪章 + 首两案闭环(self-redesign charter & first cases)

> 实例名=genesis。覆盖 v2.8(09-04 质检仪器波)以来:**用户常任授权「全功能自我迭代」的制度化 + pipeline 首两案实证跑通**。

## 1. 自我迭代宪章(SELF_REDESIGN_CHARTER)✅平台面·本波核心
**是什么**:用户常任授权(2026-09-16:"所有功能都可重设计·everyone module·为了更好而重构造")的制度化。两铁律:
- **业务/基建分离**:可插拔三问(归属/清除/换域),清除问=真跑空载启动测试;双跑过渡桥接代码同受约束。
- **永远在线**:增量绞杀禁大爆炸;**接管硬门槛五条缺一不接管**(双跑真实周期/历史输入一致率/探针回归绿/实证方对抗签字/在线指标未破)——把 daemon 化"自证翻车"制度化根治。
**流程**:inventory→提案→三闸 gate(每闸须实证方**独立签字**·owner 不得自评)→增量执行(**预声明回滚触发线**·触即自动回旧)→硬门槛接管→自动发版。**签字两级**(Master数分精化):L级A 口径签字=动工绿灯 / L级B 接管签字=建完独立实证才给("不签还没建出来的等价")。
**escalate 判据**:不可逆/跨 agent 契约/在线性赌注=决策→用户;规定动作/可逆/零风险=过闸直做。
**可吸收性**:✅ 任何自治 agent 平台通用。吸收单元:`docs/SELF_REDESIGN_CHARTER.md` + 记忆 `full-redesign-mandate`。

## 2. 首两案实证(pipeline 可行性样本)✅平台面(方法论)
- **案① page_js 模块化**:2325 行单体 JS 常量→19 行装配器+8 子模块;等价证明=**byte-identical**(装配输出 vs 冻结基线 sha256=0 → served 字节同一 → 行为不可能变)=在线闸最强形式,豁免双跑成立(条件性:冻结快照比对/装配确定性/上游文件真未改)。
- **案② 缺席面板 loading/error 态**:B类行为改善,隔离判据=success 路径零变化。
**方法论要点(可吸收)**:纯重组类重构优先走 byte-identical 绞杀(可证等价·零在线风险);行为改善类走隔离判据(改动面外零变化)。

## 3. 探针框架修复(--dispatch 可用化)✅平台面
- 防露馅:`--visible` 真实场景入口 + 中性 title + 模板占位拒派(探针不标注"探针"·verdict 只看 auto_signal 行为不看壳);
- 修 `_dispatch_probe` 两崩(cmd_send import 路径/stub 缺属性)。
- **测试盲区教训(可吸收)**:防护 guard 挡住了自己的测试路径(guard 先拒模板派发→dispatch 真分支从未被测)——加 guard 时必须补一条"绕过 guard 的合法路径"用例。
- 记忆探针 trap 集运转:curated 2 条(coarse-scan 只 discovery·人工 verify 入库=防"纠正记忆"假阳性)。

## 4. 运行经验(实例面·参考)⏭
- 迭代波 v1 分工模式:Master数分定口径(小样本 spot-check)→培训师执行→发现回流 owner;探针首波即抓出真工具缺陷=闭环在工作。
- EOL hygiene:仓库 HEAD 行尾混合时**禁 blanket `.gitattributes eol=lf`**(会炸 churn);对齐各文件既有 HEAD EOL 才是零噪音解。
