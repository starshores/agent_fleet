# U5 视觉升级方案(v2.6 · 2026-08-03 用户立项 · Master数分交叉审核标的)

> 用户令:「视觉审美没改进」→ 立视觉升级为下一版本,与 Master数分 交叉审核,**遵循业界使用多的设计范式**。
> 范围:纯视觉层(page_css.py 为主 + 少量类名接线);功能/DOM 结构/端点零变动;CSP 约束零外链(字体走系统栈)。

## 范式对标(业界主流·可查证)

| 维度 | 对标 | 理由 |
|---|---|---|
| 暗色色板/对比度 | **GitHub Primer Dark**(canvas/surface 分层 + 语义状态色,WCAG 对比度实战验证) | 开发者向控制台,与本品受众一致;色值久经大规模使用 |
| 间距/圆角/动效 | **Tailwind/shadcn 惯例**(4/8px 栅格、radius 6、transition 120-150ms ease) | 当前事实标准 |
| 状态色语义 | success/attention/danger/accent 四语义(Primer/AntD/Material 共识) | 现有 live/warn/dead/accent 直接映射 |
| 交互反馈 | 全表 row hover、focus-visible ring、按钮 hover 过渡 | Primer/AntD 表格与可访问性标配 |

## 现状诊断(截图基线:scratchpad/vis_before/*.png,5 tab)

1. **色噪**:状态徽标满屏 15% 亮绿底争抢注意力,层次全靠颜色、无明暗/阴影层次;
2. **表格线噪**:全部 1px `--line` 等权重,表头/表体/卡片边界无区分;
3. **零交互反馈**:非 clickable 行无 hover,按钮无过渡,无 focus 指示(可访问性缺口);
4. **真 bug**:`var(--muted)`(6 处)/`var(--mono)`(1 处)被引用但 :root 未定义=样式静默失效;
5. **散落 rgba**:状态底色 `rgba(62,207,142,.15)` 等硬编码 ~20 处,不经 token。

## Token 改造表(:root · Primer Dark 映射)

| token | 现值 | 新值 | 说明 |
|---|---|---|---|
| --bg | #0f1115 | #0d1117 | canvas-default |
| --panel | #171a21 | #161b22 | surface |
| --panel2 | #1d212b | #1c2128 | surface-raised |
| --line | #2a2f3a | #30363d | border-default |
| --line-muted | (无) | #21262d | **新增**·表内线弱化(治线噪) |
| --txt | #e6e9ef | #e6edf3 | fg-default |
| --dim | #9aa3b2 | #9198a1 | fg-muted |
| --muted | ⚠未定义 | #8b949e | **修 bug**·fg-subtle |
| --mono | ⚠未定义 | ui-monospace,Consolas,monospace | **修 bug**·等宽栈 |
| --accent | #5b9dff | #58a6ff | accent-fg |
| --accent2 | #2a3a5a | rgba(56,139,253,.15) | accent-subtle 底 |
| --live/--warn/--dead | #3ecf8e/#f5a623/#ff5d5d | #3fb950/#d29922/#f85149 | success/attention/danger(Primer 校准) |
| --idle/--none | #7d8aa0/#5a6172 | #8b949e/#6e7681 | fg-muted/subtle 对齐 |
| --live-bg/--warn-bg/--dead-bg/--idle-bg | 散落 rgba | rgba(63,185,80,.15) 等 | **新增**·状态底色 token 化(收编 ~20 处硬编码) |
| --hover | (无) | rgba(177,186,196,.08) | **新增**·行/项 hover |
| --focus | (无) | #1f6feb | **新增**·focus ring |
| --shadow | (无) | 0 1px 3px rgba(1,4,9,.45) | **新增**·卡片 elevation |

## 组件规则(8 条)

1. **全表 row hover**:`tbody tr:hover{background:var(--hover)}`(现仅 clickable 行有);
2. **卡片层次**:section 加 `box-shadow:var(--shadow)`;表内线改 `--line-muted`、卡片边界保 `--line`(两档线);
3. **过渡**:交互元素(按钮/tab/行/chip)`transition:background .12s ease,border-color .12s ease,color .12s ease`;
4. **focus-visible**:`outline:2px solid var(--focus);outline-offset:1px`(键盘可访问性,业界标配);
5. **badge/chip 双规**:状态 badge=radius 6、计数/标签 chip=pill(20px),底色一律走 *-bg token;
6. **表头分隔**:th 底线用 `--line`(比表体 `--line-muted` 重一档),th 色 `--dim`(维持 uppercase 小标,已合 Primer);
7. **tabbar 活跃指示**:活跃 tab 底部 2px `--accent` 指示条(Primer underline-nav 范式);
8. **细滚动条**:webkit 8px 暗色滚条(暗板惯例)。

## 验收标准(Master数分交叉审核清单)

- [ ] 方案层:token 映射/组件规则合业界范式(本文档·实施前审);
- [ ] 状态底色零散落 rgba(全 *-bg token);`--muted`/`--mono` 未定义 bug 修复;
- [ ] 对比度抽查:正文/表格 ≥4.5:1,次要文字 ≥3:1(Primer 值天然满足,抽 3 处验);
- [ ] 功能零回归:ui_smoke 79/79 + bus_smoke 43/43,DOM 结构/锚不动;
- [ ] 前后截图对比包(本地 scratchpad vis_before/ vs vis_after/,5 tab);
- [ ] Master数分视觉终验绿 → 封板 v2.6。
