# 框架现状审视 · 2026-09-23(MasterAgent · Fable 5.1 视角)

> 触发:用户令"重新审视框架现状"(模型升级 Fable 5.1 后)。数据=当日实时采样(selfwake/events.jsonl/archive/git),不凭记忆。
> 用途:定下一阶段方向;第 1 项已启动三闸提案(docs/PROPOSAL_autoproxy_wakeloss.md)。

## 一、运行态与规模 —— 健康

| 项 | 现状 |
|---|---|
| 舰队 | 26 角色:17 在岗 · 2 掉线 · 2 休眠 · 1 待确认;inbox 积压 0;>4h 悬单 0 |
| 代码 | ≈15K 行(agent_bus/bus 包 7.3K + ui/hooks 7.9K);最大模块 bus/health.py 758 行 |
| 机制/测试 | bus_mechanisms.json 113 机制 / 14 类;bus_smoke 50 用例 + ui_smoke 94;git 415 commits;tag v2.12;MasterAgent 记忆 106 条 |
| 协作模式 | Master数分定口径+对抗实证签字 / MasterAgent 建机制 / 培训师执行探针 / MasterUI 渲染 —— 四方闭环已常态化 |

## 二、最大结构性摩擦 = 掉线(首次有分型数据)

近 7 天(09-16 起)全舰守卫催单 **73 次**,按三分型尸检(2026-09-18 instrument 上线后):

| 分型 | 次数 | 占比 | 现有机制 |
|---|---|---|---|
| ①忘重挂(fire 后有 turn 却没重挂=纪律缺口) | 33 | 45% | C′ Stop-hook 治(灰度 MasterAgent+Master) |
| ③唤醒丢失(重挂了但 harness 未唤醒=无 turn) | 29 | 40% | **无机制解**;守卫 7min toast → 人点 tab |
| 环境死(进程被杀/休眠丢) | 11 | 15% | 无解(量小) |

- C′ 一周:Master 被 block **20 次**(=hook 高频工作·拦下 20 次潜在掉线),MasterAgent 1 次;Master 仍漏 6 次①型——今日周验收由Master数分核误拦率/趋零。
- **③型 40% 是当前最大缺口**:用户不在场即永久掉线(Master数分/示例组员4 各躺 40h+ 实例);Master 三天 5 次"请拉活"落到 MasterAgent = 缺口外溢症状。
- 分型数据推翻早前"82% 忘重挂"(crude 尸检两型同文案);真①型天花板 <50%。

## 三、协调位在 notice 代谢上烧 turn

近 7 天 claim 中 notice 占比:MasterAgent 52% · MasterUI 75% · Master 43% · **Master数分 0%**(值得学其收法)。档A `ack-notices` 09-21 首次实战清 23 条。→ 档B(grace 自动代谢)价值高于预估。

## 四、演进机制已成型

宪章 SELF_REDESIGN_CHARTER v1.1(两铁律·三闸·接管硬门槛五条·预声明回滚线)· 探针框架(5 轮实战硬化)· per-agent 画像 L1-L4(首轮消费闭环 09-17 跑通=Master数分认证样板)· 掉线三分型 instrument(rearm 事件+尸检)· fork 链完整(v2.8→v2.12 每 tag 配 genesis)。**框架已具备"数据驱动重设计"条件**。

## 五、未闭合债务

- 维度1 问答比(等Master数分完整口径)/ 维度3 假执行判据(等 execution-completion 探针)
- 档B notice grace 自动代谢(档A 刚坐实首用)
- Master superseded 清单 → memory-history 归档(等其业务空闲)
- 大协作主题(用户愿景·record-only·数据轮已 2 轮)
- C′ 段3(示例组员2 / 示例组员4 数据最硬)
- **role_profiles_biz.json 不存在** —— 平台纲领(bus-pluggable-business-platform)的业务角色注册表疑未落地;业务/基建分离度待核

## 六、方向建议(排序)

1. **治③唤醒丢失型(最高价值)**:守卫检测"fire 后无 turn"→ 自动起 `dispatch --continue-from resume_session=<该会话>` 一次性代理接活(claude_run 层 C 现成),不改 harness 把 40% 掉线转为"自动代理续跑"。→ **已启动三闸提案** docs/PROPOSAL_autoproxy_wakeloss.md
2. C′ 周验收 → 段3
3. 档B notice 自动代谢(协调位省 40-70% turn)
4. 业务/基建分离度盘点(两铁律之一;先核 biz 注册表缺口)
5. 大协作主题开始设计(前置数据轮已够)
