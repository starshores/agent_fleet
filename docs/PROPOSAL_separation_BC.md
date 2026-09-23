# 三闸提案 · 分离整改 B+C 合闸:注册表业务成分外迁(caps 两表 + agents.json 去业务 desc)

> 提案方:MasterAgent · 2026-09-23 · 依 SELF_REDESIGN_CHARTER + Master数分 6f6d66 口径(卸掉全部业务角色后还该在=基建;desc/业务 caps=残留;不重写 git 历史;biz 文件须自带版本控制;验收=空载清除测试)。
> 签字方:Master数分(三闸)· **培训师(caps 分类否决权·caps-registration-owned-by-trainer)** · 消费方知会:MasterUI(角色页/能力页读方)。状态:**C 已落地并签收**(Master数分 fdbd6d·2026-09-23;空载测固化 smoke sepC.empty_biz 隔离版·caps 业务词 20→0);**B1 读方已上**(roster.agents_view·commit ea72064)·剥 desc 待 MasterUI 切 api.py 读方后执行(脚本 dry-run 就位:16 业务角色·2 无页自动建最小页·14 存 legacy_desc)。

## 1. 目标形态
```
C:\apphome\agent_fleet_data\biz_registry\        ← 业务注册表物理家(同 session-archive 三分格局:_data=业务/素材)
  .git/                                            ← 自带本地 git(Master数分要求:外迁≠丢版本)
  role_profiles_biz.json                           ← 从 agent_fleet 根迁入(现 gitignore·无版本)
  caps_biz.json                                    ← 从 capabilities.json 拆出的业务 caps
  README.md                                        ← 清单/清除说明
C:\apphome\agent_fleet\biz_registry  → junction → 上面(agent_fleet .gitignore 忽略;path_registry biz-data 登记 3 项)
```
读方全部走**合并视图**(同 `load_role_profiles` 模式):`_load_caps_pool()` = capabilities.json(infra)⊕ caps_biz.json;`load_role_profiles` 路径改指 junction。业务表缺失/坏=合法(空载)。

## 2. C · caps 两表拆分(培训师确认清单)
| 归 infra(留 capabilities.json·git) | 归 biz(caps_biz.json·业务侧 git) |
|---|---|
| bus-comms · self-wake · confidentiality(保密纪律=通用底座·default_for 是业务角色但纪律本身跨域)· high-risk(审批闸=机制)· webui · data-analysis · bus-monitoring · **token-usage-etl**(ETL 对象=Claude Code 会话/usage.db=bus 方舟·按内容判基建;Master数分提醒:若含业务项目口径再迁) | 外部IM通道 · 数据取数 · 网页操作 · 业务审计A(休眠)· 业务审计B · 代理维护 |
`ui/api.py:863` 硬编外部IM通道分级 → 改读 caps_biz 的 `level/cls` 字段(D 项顺带)。

## 3. B · agents.json 去业务 desc(B1·本闸)/ 业务条目外迁(B2·后续)
- **B1(本闸)**:agents.json 业务角色条目只留 `registered / trained / caps / succeeded_by / effort`;`desc` 迁 role_profiles_biz 对应角色(已有同义字段·去重);读方(roster/hello/WebUI 花名册)desc 缺失时回退读 profile。基建角色 desc 保留(基建内容)。
- **B2(后续单闸)**:业务角色条目整体迁 agents_biz.json + `_load_agents` 合并视图(涉 update_agents 锁写路径·register/hello/retire 双表写·风险高·单独走)。
- git 历史里的业务 desc = 已知遗留·**不重写**·登记 path_registry 说明。

## 4. 三闸自评
- 闸①核心价值:零协作影响(纯数据布局);强化换域/清除。
- 闸②分离三问:归属✓ 清除✓(空载测) 换域✓(换 biz_registry 目录)。
- 闸③在线增量:合并视图先上(读双源·byte-identical 输出)→ 数据迁移脚本(幂等·先复制后删)→ 空载测 → 切换;**回滚=恢复单表文件(git 里有)+ 合并视图对空 biz 表天然兼容**。

## 5. 回滚触发线
① bus_smoke / ui_smoke 任一转红 → 回旧表;② hello/roster/WebUI 角色页任一角色 caps 或 desc 显示缺失(对比迁移前快照 diff≠0)→ 停;③ 培训师 caps 分类否决 → 该 cap 留 infra。

## 6. 验收(Master数分)
空载清除测试:临时移走 biz_registry 全部文件 → bus_smoke + ui_smoke 全绿 + who/roster/WebUI 空载渲染;git 跟踪文件业务词计数前后对比(目标 agents.json/capabilities.json 归零);biz_registry 有 ≥1 个 git commit(版本控制实证)。

## 7. 落地记录(2026-09-23)
- C:commit 72e61f4/9a84dfa/6b689d3;biz_registry git 57ec7d2;备份 agent_fleet_data/biz_registry_backup_20260923_180734(含 reinject_snapshot.json 有载基线)。验收:d 有载回归 14/14 byte-identical(Master数分独立重跑)、c git 1 commit、b caps 业务词 20→0、a 空载改隔离 fixture(AGENTBUS_BIZ_REGISTRY 指空目录·不扰 live)进 bus_smoke 53/53。
- B1 读方:roster.agents_view()(只读·写方不走)·roles.py find/route/roster + probe 切视图·agentbus re-export(load_caps_pool/biz_registry_dir/agents_view·补 C 时漏的 re-export)。剥 desc 顺序:MasterUI 切 api.py `ab._load_agents()`→`ab.agents_view()` → 我跑剥离脚本(备份→2 最小页→14 legacy_desc→update_agents 清 desc→biz git commit→agents_view 剥前后 byte-identical 验收)。
- 培训师建档缺口:示例组员4 / 性能优化 无角色页(fyi ffc75a)。
