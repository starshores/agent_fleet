# BUS 2.0 变更清单（v1.10 → v2.0）

> 用户 2026-07-14 令:对 v1.10 全部功能重组 + 机制优化(方案 B),模块一体化「看着不散、内部仍模块」,列为 2.0。
> 基线 `v1.10-pre2.0` → 检查点 `v2.0-p1` → 定版 **`v2.0`**(2026-07-20)。共 50+ commit,回归闸 `bus_smoke.py` 全程守(46 命令面 / 39 用例 / 无回归),三会话协作验证(MasterAgent 实现 / 使用方验口径 / 培训师 roster+onboarding / MasterUI UI)。

## 一、结构一体化(P0/P1)
- `agentbus.py` **3267 → 175 行(-95%)**,拆成 **20 个 `bus/` 模块**(依赖分层无环)。
- 模块:config·io·roster·proc·presence·selfwake·notify·mailbox·pool·**core**(命脉)·edge·roles·exec·sessions·tasks·health·status·identity·refs·__init__。
- 铁律:canonical `agentbus.py` 全程可跑 + re-export 保 `ab.*` 公共面(5 个外部导入方零影响)。
- P0 建回归冒烟金标准 `bus_smoke.py` + 基线,每增量四件套自验(compile+import+分支实测+smoke)+ 使用方独立复验。

## 二、机制优化(P2)
- **role-verdict 4 处归一**:状态判定原散在 cmd_who 文本 / who --json / selfwake / WebUI 各算 → 收敛单一源 `_who_data`。**根治长期病根**(加新态漏改致显示不一致,自 2026-07-13)。
- **跨引用契约化**:新 `bus/refs.py`,三 store(todos/tasklines/longrange)10 条跨引用契约单一真相源 + `ab refs --check` 只读校验 + `ab refs --purge-smoke`。
- **守卫回执降噪**:`no_receipt` fire-and-forget,退役「守卫回执洪水」。
- **轮询模式豁免**:`ab selfwake-exempt`,轮询态 agent 免巡检误报。
- **数据卫生**:3 类 purge 口径;清 66 条 `__smoke` 测试污染。

## 三、Bug 修复
- **归档式退役失明**:`_retired_roles()` 原只读 agents.json 标志位、对「归档式退役」(11 条全是)完全失明 → 改 archive-aware,并加「活跃优先」避开复用基名误伤现役(如现役 Master数分)。
- **大小写碰撞防护**:新角色注册防 `Masteragent`/`MasterAgent` 幽灵重复 + 清幽灵。

## 四、使用体验
- **`ab` 短命令入口**:固化 env python + 真实路径 + UTF-8,消除裸 python 崩 / 长路径 / 反斜杠 / 编码坑。
- **分组入口**:`ab` / `ab commands` 按一体化模块分组呈现(治 43 命令扁平难发现)。
- 恢复文档 + 3 个 hook 文案全统一到 `ab` 短命令。

## 五、架构 / 健壮性
- **junction 化**:`~/.claude/agent_bus` → 指向项目根的 junction;项目根 = 唯一物理家(换机复制项目根即带走全部含 `usage.db` 方舟)。
- **wait 中断 / 自恢复**:维护哨兵 + 异常自恢复。
- **目录整理**:根 .md 14→7,设计/评审文档归 `docs/` + README 工程地图 + `docs/DIRECTORY_MAP.md` 逐文件详版。

## 六、UI 渲染(MasterUI lane)
- `compute_fleet` 删本地自算 verdict(~80 行)→ 吃 `ab._who_data()` 权威源(2a 步骤3,灭 WebUI 侧分叉)。
- verdict badge 限宽防撑 UI(长 note 截断 + hover 看全文);工作组面板文案口径修正。

---

## 后续小项(post-2.0·非阻塞)
- **P4**:`ui/server.py`(~4930 行)拆成 `ui/` 子模块(routes/api/panels/render)——MasterUI lane,契约=端点+面板+口径不变。
- **P5**:剩余文档收敛(BUS_ONBOARDING / SESSION_RELIABILITY / SPECIALIZATION 等被 hook/server 运行时引用,移动需连改引用,随 P4 一起做)。
