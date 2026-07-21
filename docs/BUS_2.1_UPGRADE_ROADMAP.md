# BUS 2.1 升级路线图(联合分析衍生 · 2026-07-21)

> 源:`docs/BUS_2.1_REVIEW.md`(MasterAgent × Master数分 全方位联合分析)。核心思路=**把手动发现/清理的问题「机制化」,让它不复发**。
> 状态图例:✅ 已落地 · 📨 已实现待验 · ⏳ 待推 · 数据不出本机。

## 已落地(2026-07-21)

### A1 ✅ 自醒 idle timeout 日/夜策略(commit `9d81ff2`)
- **做了什么**:`ab wait --timeout 0` → 自动 日间 2h(07:00–23:00)/ 夜间 6h;`bus/config.py::_idle_timeout_policy()`,边界+时长走 `config.json` 的 `idle_timeout_policy`(可调)。
- **原理**:watchdog Observer 对 inbox 事件的响应与 timeout 无关 → **真消息即时接活不变**,只砍"没活时的 idle 空唤醒重读"(Master数分测出的最大 token 杠杆)。
- **效果**:闲置协调位 idle 空唤醒频率降 6~24×,接活零损失。`park` 因停接活排除。
- **兼容**:显式 `--timeout N` 仍覆盖;presence 存 `wait_timeout=实际长timeout` → guard/freshness 自动适配不误报掉线。re-arm 指引(hello/守卫/hook/AGENTS/BUS_ONBOARDING)全改 `--timeout 0`。
- **验**:hour 11→7200(日)·dogfood(MasterAgent+培训师 wait_timeout=7200 实测在岗)。

### B2 ✅ 定期 reap(via `start_maint`)
- **做了什么**:MasterUI 在 `ui/server.py` 加 `start_maint(1800)` 维护心跳(8799 always-on 驱动),每 30min 跑 `ab reap`。
- **效果**:卡死/孤儿 processing 30min 内自动退回 inbox(requeue+1·毒任务→`failed/`),不再累积数周(本轮手动清了 25 条攒了 5–8 天的)。
- **安全**:只动真卡死(owner 死 / age>4000s ttl),不碰 alive 长任务,无人值守幂等。

### B3 ✅ todos.json 完成态 auto-archive(commit `071d10a`)
- **做了什么**:`ab todos-archive`(默认 dry / `--apply` / `--older-min N`)把完成态(完成/已完成)移到 `todos_archive.json`(留痕),先落归档再瘦池,原子+备份。
- **根因**:Master数分定位 todos.json 膨胀=完成态从不清出(191/235 是完成态)→ 文件胖 + "待办停滞"看板虚高。
- **效果**:已 apply 一次 **235→44**(归档 191·167KB→29KB);周期化进 `start_maint`(`--older-min 1440` 每日归档完成>24h,近期完成态留用户过目)。WebUI `/api/todos` 仍 200。

### C5 ✅ bus_smoke 补 re-export 完整性闸(commit `1c91115`)
- **做了什么**:`check_reexports()` 动态扫外部导入方(agent_bus/ + ui/ 的 `import agentbus`)的 `ab.<name>` 引用 + `hasattr` 断言 → 新用例 `reexport.completeness`。
- **盲点**:命令面盘点测不到"被外部导入方用的 helper re-export 缺口"——`_load_caps_pool` 那类(P1 抽层漏 re-export,只在 server.py 真跑那条路径 500 炸)。
- **效果**:抽层漏 re-export 冒烟直接红,不靠人肉双抓(P1 期 psutil/time/Path 那批 RED 的自动化闸)。基线 39→41 用例、命令面 46→47。Master数分 bless。

### ★自愈成果
`start_maint(1800)` 维护心跳把上面 B2+B3 全自动化:**卡死/孤儿/完成态待办从此 30min 内自愈,不再累积到需要人工大扫除**。Master数分定位的三个数据侧问题(idle 重读 / todos 膨胀 / re-export 盲点)全部落地 + partner 验证闭环。

## 待推(低优 / 他 lane)

| # | 项 | Lane | 说明 |
|---|---|---|---|
| D6 | 历史 cost 全模型口径 | MasterUI 渲染 + Master数分口径 | 现漏 opus-4-7 ~$3k(undercount);面板改 token 为主轴、$ 全模型聚合仅参考([[evaluate-by-token-not-cost]])|
| E7 | 长会话压前轮换 + 落档 | 各会话 + 管理层 | 某长会话(1858 msgs)等近 65% 压闸会话;风险在记忆继承非 compact 本身 |
| F8 | `ui/page.py` 2320 前端拆分 | MasterUI | 唯一单体(INDEX_HTML 内联);raw 常量,低风险低优 |

## 收益量化
A1 idle 重读节省 + B2/B3 卫生自愈的 token/backlog 收益,待新策略跑够数据后由Master数分出前后对比(cache_read 绝对量降幅 / backlog 稳态)。
