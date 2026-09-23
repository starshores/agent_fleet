# genesis fork · 2026-09-23 · 业务/基建分离整改 C + B1 + A(v2.14)

> 实例名=genesis。覆盖 v2.13 以来:分离度盘点(docs/SEPARATION_AUDIT_20260923.md)→ 三闸提案 B+C / A → 全部落地。平台纲领:bus=通用 agent 管理平台·业务=可插拔负载(装上能跑·卸掉干净·换域照用);三问验收=归属/清除/换域。

## 1. 业务注册表外迁 biz_registry(分离 C)✅平台面
- 业务角色页 `role_profiles_biz.json` + 业务 caps `caps_biz.json` 物理外迁 `<data>/biz_registry/`(**业务侧自带 git**·不进平台 git);平台根留 junction(gitignore)。
- 读方【单一解析】:`bus.roster.biz_registry_dir()`(cfg `biz_registry` > `<home>/biz_registry`·env `AGENTBUS_BIZ_REGISTRY` 可重定向)· `load_caps_pool()`(infra ⊕ biz 合并视图·同名 infra 优先)· `load_role_profiles()`;compact 重注入 hook 经同一函数(import 失败同规则内联)。业务表缺失=合法空载·绝不抛。
- caps 两表:infra 7(bus-comms/self-wake/high-risk/webui/data-analysis/bus-monitoring/token-usage-etl)/ biz 7(含 confidentiality·培训师终裁归业务)。`capabilities.json` 业务词 20→0。
- **空载清除测试固化 smoke**(`tests/scenario_sepC_empty_biz.py`):env 指空目录=业务表全清等价态·不动 live 文件·caps 14→7 / 角色页 21→7 / hook 不抛 / roster·who·caps 渲染。验收方法论:有载回归=每业务角色 hook 注入文本迁移前后 byte-identical(备份目录存快照)。

## 2. 花名册只读视图 agents_view(分离 B1 读方)✅平台面
- `roster.agents_view()`:agents.json 深拷贝 ⊕ 角色页 desc(有页=单一源·标 `desc_src`;无页沿用)。**写方(register/hello/retire/update_agents)仍读原件**——视图注入的 desc 永不经 RMW 回写基建 git(分离复发防线)。只读读方 roster/route/probe/daemon 专责 desc 已切;WebUI 读方切换后再剥 agents.json 业务 desc(脚本就位:2 无页角色自动建最小页·14 存 legacy_desc)。
- agentbus re-export 名单:`load_caps_pool / biz_registry_dir / agents_view / shared_resources_prompt`(★re-export 盲点纪律再命中一次:抽层被外部 `ab.*` 引用必进 `__all__`)。

## 3. 通用共享资源互斥 ab lock(分离 A·取代 Edge/外部 IM 专用锁)✅平台面
- `bus/lock.py`:`ab lock acquire/release/status --resource <name>`;锁文件 `BUS/locks/<name>.lock`;O_EXCL 恰 1 胜 · stale 回收(持锁 role 进程死/超 ttl → rename 原子夺取·ABA 防)· watchdog 等释放。`edge-acquire/release` = `--resource edge` 别名(stdout byte-identical·resource=edge 过渡特判旧 `edge.lock`);status/health 双发 `locks[]`(`edge_lock` 保留一版待 UI 切)。
- **并发 gate**(`tests/scenario_lock_contention.py`):20 进程争 1 ×3 轮恰 1 胜 + 四态 JSON 键集。★测试盲区三号实录:首版 20 胜——持锁 who 无 presence→被判 stale→人人夺锁;修=测试给自己的 smoke 角色写 presence 绑测试 pid(不借别人 role)。
- **派活 prompt 通用化**(A2):daemon/dispatch prompt 的"操作 Edge/外部 IM 先抢锁"段 → `roster.shared_resources_prompt(role, abpy)` 按角色页 `shared_resources:[{resource,hint}]` 注入;无字段不注入;ad-hoc 无页只给原语行。有载验收=从 events 近 30 天真用锁角色自动算清单·每个 prompt 必含锁提示(防"删了外部 IM 段但页没补"的静默丢提示);空载验收=平台段零业务词;源码零业务词。prompt 抽 `build_prompt()` 可测。
- **业务工具族外迁**(A3):5 个外部 IM CDP 工具 → `<data>/biz_tools/external/`(自带 git);`edge_lock` 改调 canonical `ab lock`(env `AGENTBUS_PY` 可覆盖入口)与迁移同一原子步(K2);旧位 `__main__`-only 转发桩过渡(import 即 ImportError)→ 业务侧记忆 4 条旧路径改完·grep=0 → 同日删桩。迁移铁律:先广播(含旧路径引用清单给消费方)→ 动刀(前置核锁空闲)→ 确认。

## 4. 方法论/纪律(★建议吸收)
- 分离验收三件:**空载清除测试**(env 重定向隔离·不扰 live)/ **有载回归 byte-identical**(迁移前快照)/ **业务词计数**(git 跟踪文件·-i)。
- Master数分独立复核发现:自报"业务词归零"漏 2 处(注释/举例文本)——自评不算·复核者按自己口径 grep。
- bash heredoc 写 python 补丁会吞 `\n` 转义生成坏代码(同日两次)→ 长脚本走 Write 工具再执行·生成后先 import/ast.parse。
- 审计漏项自报(external_*.py 工具本体不在原审计 4 项中)→ 纳入同闸。

## 5. 实例面(⏭跳过)
biz_registry / biz_tools 具体路径、外部 IM/示例成员2角色页文案、caps 分类结论、业务记忆路径清单;机制注册表 115→118;bus_smoke 52→59。
