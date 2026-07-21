# BUS 2.1 全方位联合分析(MasterAgent × Master数分 · 2026-07-21)

> 用户令:基于当前版本(v2.1)与 Master数分 联合分析,采集 UI 图像联同分析,全方位(本体/内核/骨架/数据/UI)。
> 分工:MasterAgent = 内核·骨架·本体(代码+架构);Master数分 = 数据·口径·monitoring;UI 两侧共看。
> **数据不出本机**:WebUI 图像 headless 采集于本机 127.0.0.1、只本机分析、未外发(红线)。

## 采集
WebUI 13 tab headless 截图(chromium·不碰用户 Edge)→ 本机 scratchpad;Master数分 按绝对路径读图联同 usage.db 分析。

---

## 本体
v2.1 · 自基线 60 commit · **~11,099 行代码** · **92 机制 / 13 类**(bus_mechanisms.json)· tag 序列 v1.10-pre2.0→v2.0-p1→v2.0→v2.1。结构一体化 + 机制优化已定版,partner 全绿。

## 内核(MasterAgent)
- `agentbus.py` **175 行薄分发器**;命脉 `core.py` 419 行;re-export 契约保持(P4 后 UI 侧单一入口 `ui/common.py`)。
- 判:无怪物内核,分发/命脉清晰、契约稳。

## 骨架(MasterAgent)
- **bus/ 20 模块 3,722 行,分布均衡**:health 594 / core 419 / pool 411 / identity 317 / sessions 312 …(无单点臃肿),依赖分层无环 + junction 架构。
- **ui/ 拆分后 4,978 行**:server.py 135(薄装配)/ routes 193 / api 2,211 / **page.py 2,320**。
- ⚠ 唯一单体 = `ui/page.py` 2,320(INDEX_HTML 前端常量 HTML+JS+CSS 内联)——未来可拆前端资源,raw string 常量,**低优**。

## 数据(Master数分)
- **核心方舟健康**:`usage.db` 33.6MB / 78,451 行(无膨胀)、ETL lag **~7min = 准实时**;`refs` 真悬挂率 **0/41**(契约稳)。
- **cache_read 98.6% = 健康非浪费**:cache-read $0.50/M vs input $5/M(省 10×),读:写 79:1 = 高复用稳态。
- **★真效率杠杆 = cache_read 绝对量(27,700M)**:长会话每轮重读大 ctx,**含 idle 自醒空转重读**(闲置 self-wake fire 把整个 ctx 当 cache_read 重读换零产出)。
- **★膨胀点精确定位**:`todos.json` 235 条中 **168 条完成态从未清出**,真活的仅 待办 60 + 进行中 7 →「待办停滞 55」看板虚高全由此。
- **backlog 归因(良性)**:孤儿回执 39 + 卡死 23 = 07-14 管理层轮换后残留(旧 from 已归档 / claimed 未 done),一轮 `ab reap` 即清;久滞/掉线 4 = 周末闲置掉 wait(运维)。
- **events.jsonl** 2.4MB / 18,716 = work-correlated append-only,现健康,>10MB 再议轮换。
- **cost 口径**:历史面板 ~$15,497 ≈ 仅 opus-4-8,漏 opus-4-7 ~$3k(DB 全模型 $18,557)= undercount。

## UI(两侧)
- P4 拆分后 13 tab 渲染整洁、verdict 单一源、口径没漂(ctx 面板 63/59/55 与 DB ctx-proxy 64/63/59 吻合)。
- ⚠ `tasks` tab 截图 5.3MB = 任务/待办列表膨胀(与 todos.json 168 完成态同源)。
- ⚠ 历史用量面板 cost 需全模型聚合 + "$参考·token 等价" 标注。

---

## 🎯 联合建议(按杠杆排序 · 标 lane)

| # | 建议 | 依据 | Lane | 状态 |
|---|---|---|---|---|
| 1 | **砍 idle 自醒重读**(最大 token 杠杆):闲置协调位一律 `park`/`selfwake-exempt` | Master数分:cache_read 绝对量可砍;某协调位已 park、sql_agent/云文档已 exempt | MasterAgent 机制(推广用法) | 运营习惯 |
| 2 | **长会话压前轮换 + 落档**:某长会话(1858 msgs)等近 65% 压闸会话 | Master数分:风险在记忆继承非 compact 本身 | 各会话+管理层 | 待排 |
| 3 | **清 `todos.json` 168 完成态** | Master数分:文件瘦身 + backlog 看板不再虚高(真 backlog 仅 60) | Master数分起 `ab` 清理·需点头 | 待用户 |
| 4 | **`ab reap`**(孤儿 39 + 卡死 23 轮换残留) | health 自检 + Master数分归因 | MasterAgent·安全 | ✅ 本次执行 |
| 5 | **`ab refs --purge-smoke`**(5 smoke) | Master数分 refs 检查 | MasterAgent·安全 | ✅ 本次执行 |
| 6 | **修历史 cost 口径**(全模型聚合 + token 门面) | Master数分口径 + evaluate-by-token-not-cost | MasterUI+Master数分 | 待 MasterUI |
| 7 | (低优)`ui/page.py` 2,320 前端常量未来拆资源 | MasterAgent 骨架 | MasterUI | 低优 |

## 全方位总判
**BUS 2.1 全维度健康、无结构性隐患。** 内核/骨架一体化达成;数据层核心方舟+契约+ETL 全健康;唯一实质膨胀=todos.json 完成态未清(易修)。看着吓人的 backlog(孤儿 39/卡死 23/停滞 55)实为轮换/周末的良性残留,一轮 reap + 清完成态即清爽。最大的**非结构性**优化在运营习惯——#1 砍 idle 自醒重读(讽刺:本次分析的几十轮 idle 重挂即活例)。
