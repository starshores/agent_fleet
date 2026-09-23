# 业务/基建分离度盘点 · 2026-09-23(MasterAgent · 铁律1 审计)

> 依据:CLAUDE.md 基准②(bus 核心只放协调机制·业务=可插拔负载·基建 git 零业务残留)+ 平台三问(归属/清除/换域)。方法:核心代码/UI/注册表/配置 grep 业务词(业务系统名·数据平台·外部IM·业务仓·客户库·作业调度·报表域·外部工具…)+ 逐项归属判定。**只审计不动刀**;整改按三闸走Master数分。

## 0. 结论
分仓**已做对的**:role_profiles 两表(基建 7 角色 git / 业务 14 角色 gitignore·`load_role_profiles` 合并视图)·fleet.json 零业务·运行态队列=过境·path_registry biz-data 清单 18 项。
**未分离的四处(按换域代价排序)**:
1. **Edge/外部IM互斥 = 业务专用协调机制烤进核心**(最重):`bus/edge.py` 整模块(24 处业务词)+ 2 条 CLI(`edge-acquire/release`)+ health/status 输出 `edge_lock` + worker_daemon/dispatch **prompt 内嵌外部IM操作流程**(写单元格/读群/connect_over_cdp)。注册表类7「Edge互斥与外部IM协调」5 机制整类业务色。三问:归属✗(机制是通用"共享资源互斥"·但命名/prompt/文档全绑外部IM)· 清除✗(清业务后 CLI 与 prompt 仍讲外部IM)· 换域✗(换域要改平台代码)。
2. **agents.json 16 业务角色 desc 进 bus git**(白名单跟踪):desc 含数据集群/运维通道/数据返仓/模型代码仓等业务口径 15/16。三问:归属✗(角色描述=业务内容;基建只需"角色存在+caps 键")· 清除✗(git 史带业务)。与 role_profiles_biz 重复描述。
3. **capabilities.json 8/14 caps 业务专用**(外部IM通道 / 数据取数 / 网页操作 / 用量ETL / 业务审计A / 业务审计B / 代理维护…)进 bus git;`ui/api.py:863` 又硬编外部IM通道分级。caps 登记归培训师。三问:归属✗(能力池=业务能力清单)。
4. **UI/配置硬编业务路径与种子**:`ui/api.py` `_AGENT_CWD_HINTS=("biz_wh","biz_wh_et",…)` / `projset` 硬编业务路径 / `1442` todos 种子来自外部IM某群;`config.json` `repo`/`default_worker_cwd`/`memory_index` 指业务库(配置集中=设计允许·但 memory_index 指业务记忆=混)。三问:换域△(config 一处可改;api.py 硬编须改代码)。

另:核心代码零散业务词(status/probe/refs/hooks 各 1-9 处)多为注释/示例·低优先。

## 1. 整改方向(提Master数分定口径·三闸后动)
| # | 动作 | 增量绞杀方案 | 换域后效果 |
|---|---|---|---|
| A | Edge 互斥 → **通用资源互斥** `ab lock acquire/release --resource <name>`;`edge-acquire/release` 保留为 `--resource edge` 别名(命令面不破) | 新命令+别名·byte-identical 行为;prompt 中外部IM流程段迁到 role_profiles_biz 对应角色 boundary(daemon/dispatch prompt 改为读 profile 的「资源互斥提示」字段) | 核心只剩"资源互斥"·外部IM成业务侧配置 |
| B | agents.json 只留 `registered/trained/caps 键/succeeded_by`;desc 迁 role_profiles_biz(已有同义字段) | 迁移脚本一次·读方(roster/hello/WebUI)desc 回退读 profile | bus git 零业务 desc |
| C | capabilities.json 拆 caps_infra(bus-comms/self-wake/confidentiality/webui/high-risk…)+ caps_biz(gitignore·登记 path_registry) | 读方 `_load_caps_pool` 合并视图(同 role_profiles 模式) | 换域换 caps_biz 文件即可 |
| D | api.py 硬编 → config(`project_domains`·`todo_seed` 外置);memory_index 语义改名 `biz_memory_index` | 纯配置化 | 换域改 config 一处 |

**顺序建议**:C→B→D→A(A 最重·涉及 prompt 契约与培训)。每项独立三闸;A 需培训师(caps/onboarding)与 Master(业务 agent 用法)同步。
