# WebUI 轻量响应式迁移设计(morphdom 增量 DOM diff)

## 决策:morphdom(内联,~12KB),不上 petite-vue / Alpine

| 方案 | 取舍 |
|---|---|
| **morphdom ✅** | 保留现有「build HTML 字符串」的所有 render 函数;只把 `innerHTML=html` 换成 `morphdom(node, html, opts)`。增量 patch、只动变化节点 → 不闪、焦点/选择/滚动/打开的下拉天然保留。改动面最小、回归风险最低,可逐面板灰度。 |
| petite-vue / Alpine | 要把每个模板重写成指令语法(v-for/v-model/@click),blast radius 覆盖全部面板,回归风险高;且框架自身扫 DOM。 |

CSP/约束全满足:morphdom UMD 已**内联**进 server.py 的 INDEX_HTML(运行期零外部 CDN);单文件、无构建链;后端 /api/* 与所有面板不动;SSE/轮询不动——只换「渲染层」。

## 响应式模型:临时 UI 态入 JS,模板从中渲染,morphdom 对齐
卡内临时态全部存 JS,由 `cardHtml` 渲染出来 → morphdom 把 DOM patch 成新 html 与之对齐 → 刷新中天然不丢:
- `roleSel[id]` —— 用户为该卡选的「派给」role。
- `openEditors[id]` —— 补充说明编辑器草稿(打开中);`undefined`=未打开。textarea `oninput` 实时写回草稿。

`renderTodos()` 核心:
```js
morphdom(box, '<div id="todoBox">'+inner+'</div>', {
  getNodeKey: el => (el.nodeType===1 && el.id) ? el.id : null,   // 卡片 id=todocard-<id> → 按身份匹配、原地更新
  onBeforeElUpdated(fromEl){                                      // 用户正在输入/选择的控件:跳过 patch
    if(fromEl===document.activeElement &&
       /^(INPUT|TEXTAREA|SELECT)$/.test(fromEl.tagName)) return false;
    return true;
  }
});
```
**已删除**旧的 `dirtyCards / markDirty / clearDirty` 手工保护整卡 + 手动存/恢复焦点的全套补丁 —— 响应式天然得到。

## Pilot 结果(待办池面板,Playwright headless 实测)
| 测试 | 结果 |
|---|---|
| A 打开补充说明编辑器、输入草稿后强制刷新 | 编辑器仍开 ✓ / 草稿原样(含中文)✓ / **焦点光标保留** ✓ / textarea 与卡片是**同一 DOM 节点**(原地 patch 非重建)✓ |
| B 选「派给」role 后强制刷新 | 选择值保留(GIT管理)✓ / select 同一节点 ✓ |
| C 连打 6 次强制刷新 | 卡数 11→11 稳定 ✓ / 无 JS error ✓ |

截图:`scratchpad/pilot_tasks.png`。`card_same_node=true` 是关键证据:卡片节点是被增量 patch 的,不是重建 → 不闪、滚动不跳。

## 铺开计划(分阶段,不阻断现用)
1. ✅ **待办池 pilot**(本次)—— 最交互、踩坑最多,已验证。
2. 待确认 OK 后逐面板套同一手法(都是「已有 html 字符串 → 换 morphdom + 给可复用节点加 id 作 key」):
   舰队总览表(`fleetBody` 行加 `id=fleetrow-<role>`)→ 回填列表 → 事件列表 → BI/历史用量/派活表/会话列表。
   每个面板:① html 生成不变;② `innerHTML=` 换 `morphdom`;③ 列表项加稳定 id 作 key;④ 删除该面板任何 bespoke 焦点保护。
3. 全部完成后:render() 不再有整体 innerHTML 重建,全站不闪。

## 风险/备注
- morphdom 内置对 `INPUT/TEXTAREA/SELECT/OPTION` 的 value/checked/selected 特殊同步;配合 `onBeforeElUpdated` 跳过聚焦控件,表单态稳。
- key 必须稳定且唯一(用业务 id,不用数组下标),否则增删项时匹配错位。
- morphdom 会按 toNode 同步属性(多余属性会被删)→ 探针测身份要用 JS expando(`el.__x`)而非 data-* 属性。
