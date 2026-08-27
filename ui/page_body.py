#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_body —— WebUI HTML 骨架段(UI2.0 U4 拆自 page.py·纯搬运字节不改)。"""
BODY = r"""<header>
  <h1>🛰 Agent Bus 控制台</h1>
  <span class="pill"><span id="connDot" class="dot"></span><span id="connTxt">连接中…</span></span>
  <span class="pill" title="在跑 claude=机器上所有活着的 claude 进程;在岗=已认领到某 role(有 presence)的 claude 进程。两者都【不是】fleet.json 里的 daemon 数 —— daemon 看 🩺 健康徽标/舰队页。">在跑 claude <b id="aliveN" style="color:var(--txt)">–</b> · 在岗 <b id="fleetN" style="color:var(--txt)">–</b></span>
  <span id="gConfirm" class="gconfirm" style="display:none" title="点击跳到待办池查看待确认">🙋 <b>0</b> 项待确认</span>
  <span id="healthWrap" class="hwrap">
    <span id="healthBadge" class="hbadge loading" title="bus 健康(点击展开)">🩺 …</span>
    <div id="healthPanel" class="hpanel" style="display:none"></div>
  </span>
  <span id="ctxBadge" class="hbadge loading" title="上下文预算(点击看看板)·≥65%红=将被 auto-compact,建议先主动落档重置">🧠 …</span>
  <span class="spacer"></span>
  <span class="ctrl">兜底轮询
    <select id="pollSel">
      <option value="0">关(仅事件)</option>
      <option value="3000">3s</option>
      <option value="5000">5s</option>
      <option value="10000" selected>10s</option>
      <option value="30000">30s</option>
    </select>
  </span>
  <button class="btn ghost" id="refreshBtn" style="margin:0;padding:6px 14px">手动刷新</button>
  <span class="pill muted" id="updT">–</span>
</header>
<nav class="tabbar" id="tabbar">
  <button class="tab" data-t="fleet">🛰 舰队/运维</button>
  <button class="tab" data-t="bi">📊 运营/资源</button>
  <button class="tab" data-t="usage">📈 历史用量</button>
  <button class="tab" data-t="caps">🧬 能力池</button>
  <button class="tab" data-t="ctx">🧠 上下文</button>
  <button class="tab" data-t="waitreg">⏱ wait 注册表</button>
  <button class="tab" data-t="prompts">📝 提示词审核</button>
  <button class="tab" data-t="tasklines">🎯 任务线看板</button>
  <button class="tab" data-t="groups">🧩 工作组</button>
  <button class="tab" data-t="mechanisms">🔧 机制</button>
  <button class="tab" data-t="roles">👤 角色</button>
  <button class="tab" data-t="bizroles">🏢 业务角色</button>
  <button class="tab" data-t="longrange">🗂️ 长程任务</button>
  <button class="tab" data-t="tasks">📋 任务/协作</button>
  <button class="tab" data-t="live">🔴 实时</button>
  <button class="tab" data-t="paths">📁 路径</button>
  <button class="tab" data-t="fleetlog">📋 舰队日志</button>
  <button class="tab" data-t="asr">🎙 语音转写</button>
  <span class="spacer" style="flex:1"></span>
  <span class="muted" style="font-size:11.5px;align-self:center" id="tabHint"></span>
</nav>
<div id="fleetStateBar" style="display:none;padding:5px 16px;font-size:12.5px;background:var(--card);border-bottom:1px solid var(--line);gap:12px;align-items:center;flex-wrap:wrap">
  <span id="fsIcon" style="font-size:15px"></span>
  <b id="fsState"></b>
  <span class="muted">|</span>
  <span>守卫: <span id="fsGuard"></span></span>
  <span class="muted">|</span>
  <span>在岗 <span id="fsArmed"></span>/<span id="fsExpected"></span></span>
  <span id="fsStaleWrap" style="display:none">
    <span class="muted">|</span>
    <span class="badge warn" id="fsStaleBtn" style="cursor:pointer" onclick="toggleFleetStale(this)">⚠ 掉线 <span id="fsStaleN"></span> ▾</span>
    <span id="fsStaleDetail" style="display:none;font-size:11px;color:var(--warn);margin-left:4px"></span>
  </span>
  <span id="fsBootWrap" style="display:none">
    <span class="muted">|</span>
    <span class="badge dead" id="fsBootBtn" style="cursor:pointer" onclick="toggleFleetBoot(this)">⛔ 未装载 <span id="fsBootN"></span> ▾</span>
    <span id="fsBootDetail" style="display:none;font-size:11px;color:var(--dead);margin-left:4px"></span>
  </span>
</div>

<main>
  <section data-tab="fleet">
    <div class="sec-h">A · 舰队总览 <span class="sub">role 身份 / 心跳 / 队列(同 <span class="mono">who</span>+<span class="mono">list</span>)— 点行展开任务</span></div>
    <div id="maintBanner" style="display:none;background:rgba(255,165,0,.12);border-bottom:1px solid rgba(255,165,0,.3);padding:7px 16px;font-size:13px">🔧 <b>维修模式启动中</b> <span id="maintDetail" class="muted"></span></div>
    <div class="sec-b">
      <table>
        <thead><tr>
          <th style="width:1%;white-space:nowrap">Role</th>
          <th style="width:120px;max-width:120px">模型</th>
          <th style="width:200px;max-width:200px">判定</th>
          <th style="width:1%;white-space:nowrap">turn 状态</th>
          <th style="width:1%;white-space:nowrap">pid<span class="sub" style="font-weight:400"> (d:=daemon)</span></th>
          <th style="width:1%;white-space:nowrap">心跳</th>
          <th style="width:1%;white-space:nowrap">inbox</th>
          <th style="width:1%;white-space:nowrap">proc</th>
          <th style="width:1%;white-space:nowrap">done</th>
          <th style="width:1%;white-space:nowrap">held</th>
          <th style="width:1%;white-space:nowrap">failed</th>
          <th>cwd</th>
        </tr></thead>
        <tbody id="fleetBody"></tbody>
      </table>
    </div>
    <div class="foot" id="procFoot"></div>
  </section>

  <section data-tab="bi" style="scroll-margin-top:64px">
    <div class="sec-h">📊 运营 / 资源 <span class="sub">今日消耗 = <span class="mono">usage.db</span>(全量·含前台 cli·Shanghai 日,统一运营口径)· 在跑任务 + 系统资源 <span class="mono">psutil</span> 实时</span>
      <span class="spacer" style="flex:1"></span>
      <span class="pill muted" id="biUpd">–</span>
    </div>
    <div class="sec-b">
      <div class="bi-kpis" id="biKpis"></div>
      <div id="biRunning" style="padding:0 16px 8px"></div>
      <div id="biModels" style="padding:2px 16px 10px"></div>
      <div class="sub" style="padding:12px 16px 0">在岗资源占用(当前进程 · <span class="mono">psutil</span> 实时 · 进程此刻的 CPU/内存 · 按内存降序)</div>
      <table class="bi-tbl">
        <thead><tr><th>角色</th><th>CPU%</th><th>内存MB</th></tr></thead>
        <tbody id="biSysBody"></tbody>
      </table>
    </div>
  </section>

  <section data-tab="bi">
    <div class="sec-h">🚚 派工/协作效率 <span class="sub"><span class="mono">metrics.jsonl</span> · 仅自动化派工(daemon/dispatch)口径,非总账</span>
      <span class="spacer" style="flex:1"></span>
      <span class="pill muted" id="dmMeta">–</span>
    </div>
    <div class="sec-b">
      <div class="bi-kpis" id="dmKpis"></div>
      <div class="sub" style="padding:6px 16px 0">各角色成本(降序 · $/turn 效率)</div>
      <div id="dmRoles" style="padding:4px 16px 6px"></div>
      <div class="sub" style="padding:6px 16px 0">daemon vs dispatch 拆分</div>
      <div id="dmKind" style="padding:4px 16px 6px"></div>
      <div class="sub" style="padding:6px 16px 0">Top 耗时任务</div>
      <div id="dmTop" style="padding:4px 16px 12px"></div>
    </div>
  </section>

  <section data-tab="tasks">
    <div class="sec-h">B · 待办池 TODO Pool <span class="sub">待确认 + 待办统一队列 · 存 <span class="mono">agent_bus/todos.json</span></span>
      <span id="confirmAlert" class="confirm-alert" style="display:none"></span>
      <span class="spacer" style="flex:1"></span>
      <span class="filt">
        <button class="btn ghost tview-btn active" id="tviewActive" data-view="active">待办池 <b id="tcountActive">0</b></button>
        <button class="btn ghost tview-btn" id="tviewHistory" data-view="history">历史 <b id="tcountHistory">0</b></button>
        <span style="width:8px;display:inline-block"></span>
        优先级
        <select id="filtPri"><option value="">全部</option><option>高</option><option>中</option><option>低</option></select>
        状态
        <select id="filtStatus"><option value="">全部</option><option>待办</option><option>进行中</option><option>搁置</option><option>完成</option></select>
      </span>
    </div>
    <div class="todo-add">
      <input type="text" id="tAddTitle" class="full" placeholder="标题(必填)">
      <textarea id="tAddDetail" class="full" placeholder="详情(可空)"></textarea>
      <input type="text" id="tAddSource" placeholder="来源(谁/哪个群)">
      <input type="text" id="tAddDeadline" placeholder="截止(如 2026-06-23 09:00,可空)">
      <select id="tAddPri"><option>高</option><option selected>中</option><option>低</option></select>
      <select id="tAddStatus"><option selected>待办</option><option>进行中</option><option>搁置</option><option>完成</option></select>
      <button class="btn mini full" id="tAddBtn" style="justify-self:start">+ 新增待办</button>
    </div>
    <div id="todoBox" style="padding:4px 0"></div>
  </section>

  <div class="grid2" data-tab="fleet">
    <section>
      <div class="sec-h">F · daemon 自愈 <span class="sub">fleet.json 纳管的 daemon × 存活(复用 <span class="mono">supervise</span>)</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" id="superviseBtn" style="margin:0;padding:5px 12px">supervise 一次</button>
      </div>
      <div id="daemonBox" style="padding:4px 0"></div>
    </section>

    <section>
      <div class="sec-h">G · 孤儿巡检 <span class="sub">复用 <span class="mono">reap --dry-run</span>(只读,不改队列)</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" id="reapBtn" style="margin:0;padding:5px 12px">巡检一次</button>
      </div>
      <div id="reapBox" style="padding:8px 16px;color:var(--dim);font-size:12px">点「巡检一次」查看 owner 已死/超期的 processing 任务。</div>
    </section>
  </div>

  <section data-tab="fleet">
    <div class="sec-h">H · 最近事件 <span class="sub">审计流 <span class="mono">events.jsonl</span> 末 40 行 · 倒序(send/claim/done)</span></div>
    <div id="eventBox" style="max-height:300px;overflow:auto"></div>
  </section>

  <div class="grid2" data-tab="tasks">
    <section>
      <div class="sec-h">C · 派活 <span class="sub">复用 <span class="mono">agentbus send</span> · from=Master</span></div>
      <label>派给(to role)<span class="muted" style="font-weight:normal;font-size:11px"> · 输入标题/任务后自动给出建议对口(ab route 同源·防误派)</span></label>
      <div id="routeChips" style="display:none;margin:2px 0 6px"></div>
      <select id="sendTo"></select>
      <label>标题(title)</label>
      <input type="text" id="sendTitle" placeholder="一句话任务名">
      <label>任务(task — 自包含指令,含产出位置+约束)</label>
      <textarea id="sendTask" placeholder="详细任务描述…"></textarea>
      <label style="margin-bottom:0">完成通知(worker done --notify)</label>
      <select id="sendNotify">
        <option value="auto" selected>auto(error 必弹 / Master 完成弹·节流)</option>
        <option value="always">always(总弹 toast)</option>
        <option value="never">never(不弹)</option>
      </select>
      <label class="chk"><input type="checkbox" id="sendApproval">
        高风险 requires_approval(留 inbox 不被自动领取,需人工 <span class="mono">wait --include-approval</span>)</label>
      <button class="btn" id="sendBtn">发送任务</button>
    </section>

    <section>
      <div class="sec-h">D · 回填结果 <span class="sub">读 <span class="mono">&lt;role&gt;/done</span></span>
        <span class="spacer" style="flex:1"></span>
        <select id="replyRole" style="width:auto;margin:0;padding:3px 8px"></select>
        <button class="btn danger" id="clearAllBtn" style="margin:0;padding:5px 12px">清空</button>
      </div>
      <div id="replyBox" style="padding:8px 0"></div>
    </section>
  </div>

  <section data-tab="live">
    <div class="sec-h">I · 实时视图 <span class="mono">live.log</span> <span class="sub">看 headless daemon/dispatch 实时执行流(🔧工具 / 💬回复 / ■完成)— 选 role 后每 1.5s 轮询</span>
      <span class="spacer" style="flex:1"></span>
      <span class="filt">role
        <select id="liveRole"></select>
        <button class="btn ghost mini" id="liveBottom">滚到底</button>
      </span>
    </div>
    <div class="live-meta"><span id="liveDot" class="live-dot"></span><span id="liveStat">未选 role</span></div>
    <pre id="liveBox" class="livebox">（选一个 role 看它的 live.log）</pre>
  </section>

  <!-- 历史用量 BI (P0) —— 独立 tab(账单级全量,与运营/资源口径分开) -->
  <section data-tab="usage">
    <div class="sec-h">📈 历史用量 <span class="sub">全量 Claude Code 用量 · <span class="mono">token_stats.py</span> · 已去重 · <b>token 消耗为主</b>($ 仅 API 等价参考)· Shanghai 日</span>
      <span class="spacer" style="flex:1"></span>
      <span id="usageMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="usageRefresh">刷新</button>
    </div>
    <div class="kpis" id="usageKpis"></div>
    <div class="grid2">
      <div>
        <div class="uchart-cap">每日 token 趋势 · io <span class="muted">(input+output · 新增有效工作量)</span>
          <span class="urange" id="uRange">
            <button type="button" data-r="7">7d</button><button type="button" data-r="30" class="on">30d</button><button type="button" data-r="90">90d</button><button type="button" data-r="0">全部</button>
          </span>
          <span class="muted" id="trendPeak"></span>
        </div>
        <div class="uchart-wrap" id="uTrendWrap"><canvas id="uTrend" height="190"></canvas><div class="uchart-tip" id="uTrendTip"></div></div>
        <div class="trend-legend" id="trendLegend"></div>
        <div class="uchart-cap" style="margin-top:12px">每日 token 趋势 · 总 <span class="muted">(io+cache读写 · 固定物理量 · 独立纵轴)</span>
          <span class="muted" id="trendPeakTotal"></span>
        </div>
        <div class="uchart-wrap" id="uTrendTotalWrap"><canvas id="uTrendTotal" height="190"></canvas><div class="uchart-tip" id="uTrendTotalTip"></div></div>
      </div>
      <div>
        <div class="uchart-cap">项目 token 消耗 Top N <span class="muted" id="uProjUnit">(全历史)</span></div>
        <div class="bars" id="uProj"></div>
      </div>
    </div>
    <div class="uchart-cap" style="border-top:1px solid var(--line);margin-top:6px;padding-top:12px">模型迁移进度 <span class="muted">(按月堆叠 · 标 4.8 占比)</span></div>
    <canvas id="uModels" height="200"></canvas>
    <div class="legend" id="uModelLegend"></div>
  </section>

  <!-- 能力池 caps —— capabilities.json + agents.json 实时只读(最小授权审视)-->
  <section data-tab="caps">
    <div class="sec-h">🧬 能力池 / caps
      <span class="sub">最小授权:每 agent 只持需要的能力 · 数据源 <span class="mono">capabilities.json</span> + <span class="mono">agents.json</span> 实时只读(不改 caps)</span>
      <span class="spacer" style="flex:1"></span>
      <span id="capsMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="capsRefresh">刷新</button>
    </div>
    <div class="sub" style="padding:8px 16px 0">⓪ Agent 详情 · <b>各 agent 会哪些内容</b> <span style="font-weight:400">— 名 / <b>职责 desc(会什么)</b> / caps / 状态(<span style="color:var(--live)">前台cli</span>·<span style="color:var(--idle)">daemon</span>·<span style="color:var(--none)">离线</span>·<span style="color:var(--dead)">退役→接替者</span>)· 数据源 <span class="mono">agents.json.desc</span> + <span class="mono">retired_roster.json</span> 实时只读</span></div>
    <div class="ag-grid" id="agDetail"></div>
  </section>

  <section data-tab="caps">
    <div class="sub" style="padding:8px 16px 0">① 能力池清单 <span style="font-weight:400">— 6 个 cap · desc / 默认给谁 / 风险标注 · 现持有人数(<b style="color:var(--dead)">红=无持有者</b>)</span></div>
    <div class="cap-pool" id="capPool"></div>
  </section>

  <section data-tab="caps">
    <div class="sec-h">② caps 矩阵 <span class="sub">agent × cap(✓=持有)· 现行 agent 按 caps 数排;退役角色单列在后(<span class="mono">删除线</span>)</span></div>
    <div class="sec-b" style="overflow:auto">
      <table class="capmx" id="capMatrix"></table>
    </div>
    <div class="mx-foot" id="capMxFoot"></div>
  </section>

  <div class="sec-grid" data-tab="caps">
    <section>
      <div class="sec-h">③ 能力分布 <span class="sub">每个 cap 被几个现行 agent 持有(降序)</span></div>
      <div class="bars" id="capDist"></div>
    </section>
    <section>
      <div class="sec-h">⑤ 最小授权 · 高风险能力持有者 <span class="sub">谁握着对外/高风险闸 — 一眼审授权面</span></div>
      <div id="capRisk" style="padding:10px 16px"></div>
    </section>
  </div>

  <section data-tab="caps">
    <div class="sec-h">④ 缺口高亮 · 数据治理 <span class="sub">空 caps 在岗 agent(待补)/ 无持有者能力 / 已过滤的测试角色</span></div>
    <div id="capGaps" style="padding:8px 0"></div>
  </section>

  <!-- 上下文模块 · 1M 预算/auto-compact 预警 -->
  <section data-tab="ctx">
    <div class="sec-h">🧠 上下文 · 1M 预算预警
      <span class="sub">每个活跃会话(窗口)的上下文预算:<span class="mono">ctx=input+cache_read+cache_creation</span> / 模型窗口 — <b>≥65% 将被 auto-compact</b>(实测真阈·有损摘要→降智)、≥50%黄。role 仅标"谁的窗口"(who 校验)。<b>谁活着/在岗</b>看 🛰 运维面板</span>
      <span class="spacer" style="flex:1"></span>
      <span id="ctxMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="ctxRefresh">刷新</button>
    </div>
    <div class="sec-b">
      <div class="ctx-kpis" id="ctxKpis"></div>
      <table>
        <thead><tr><th>占用%</th><th>距自动压(65%)余量</th><th>ctx / 窗口</th><th>role(谁的窗口)</th><th>model</th><th>最后活动 UTC</th></tr></thead>
        <tbody id="ctxBody"></tbody>
      </table>
      <div class="foot muted" id="ctxFoot"></div>
    </div>
  </section>

  <!-- ⏱ wait 注册表观测(P3·2026-08-10):每个 role 的后台 wait 生命周期 -->
  <section data-tab="waitreg">
    <div class="sec-h">⏱ wait 注册表观测
      <span class="sub">每个 role 在册的后台 wait 进程生命周期:<span class="mono">wait_pid / 宿主 claude / 代数 / 在册时长 / 心跳 / 状态</span>。宿主 claude 死而 wait 仍活=<b>孤儿</b>(P1 宿主自检 10s 内自杀)。数据源 <span class="mono">&lt;role&gt;/wait.reg.json</span></span>
      <span class="spacer" style="flex:1"></span>
      <span id="wrMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="wrRefresh">刷新</button>
    </div>
    <div class="sec-b">
      <table>
        <thead><tr><th>role</th><th>wait_pid</th><th>宿主 claude</th><th>代数</th><th>在册时长</th><th>心跳 age</th><th>状态</th></tr></thead>
        <tbody id="wrBody"></tbody>
      </table>
      <div class="foot muted" id="wrFoot"></div>
    </div>
  </section>

  <!-- 提示词审核(只读·手动刷新)-->
  <section data-tab="prompts">
    <div class="sec-h">📝 提示词审核
      <span class="sub">当前喂给 agent 的提示词<b>原文</b>(只读·供审核措辞)— hello onboarding / 全局+项目 CLAUDE.md / 基座层文档。手动刷新才重读,每节标源路径+mtime</span>
      <span class="spacer" style="flex:1"></span>
      <span id="promptsMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="promptsRefresh">刷新</button>
    </div>
    <div class="sec-b">
      <div id="promptsBox" style="padding:8px 16px 16px"></div>
    </div>
  </section>

  <!-- 可恢复会话 (resume) -->
  <section data-tab="fleet">
    <div class="sec-h">🗂 可恢复会话 <span class="sub">Claude Code 历史会话 · <span class="mono">ab sessions</span> · 仅元数据</span>
      <span class="spacer" style="flex:1"></span>
      <span class="filt-grp"><span class="filt-lbl">归属</span>
        <button class="btn ghost mini sess-own active" data-o="all">全部</button>
        <button class="btn ghost mini sess-own" data-o="bus">bus</button>
        <button class="btn ghost mini sess-own" data-o="nonbus">个人</button></span>
      <span class="filt-grp"><span class="filt-lbl">状态</span>
        <button class="btn ghost mini sess-filt active" data-f="all" id="sessFAll">全部</button>
        <button class="btn ghost mini sess-filt" data-f="live" id="sessFLive">▶ 运行中</button>
        <button class="btn ghost mini sess-filt" data-f="dead" id="sessFDead">· 死(可 resume)</button>
        <button class="btn ghost mini sess-filt" data-f="tagged" id="sessFTag">🏷 已标记</button></span>
      <span class="filt-grp"><span class="filt-lbl">排序</span>
        <button class="btn ghost mini sess-sort active" data-s="recent">最近</button>
        <button class="btn ghost mini sess-sort" data-s="size" title="按会话 jsonl 大小降序:大=越吃上下文/越接近 compact/越该归档清">大小▼</button></span>
      <button class="btn ghost mini" id="sessRefresh">刷新</button>
    </div>
    <div class="hint" style="padding:8px 16px 0;color:var(--dim)">⚠ 运行中的会话正在跑、别 resume。<b>死的才是真·可恢复</b> —— 点【复制】拿到 <span class="mono">claude --resume &lt;id&gt;</span>,到终端执行续会话(WebUI 跑不了交互式 resume)。</div>
    <div id="sessCount" class="muted" style="font-size:11.5px;padding:6px 16px 0"></div>
    <div id="sessBox" style="padding:8px 0;max-height:560px;overflow:auto"></div>
  </section>

  <!-- 🎯 长期任务线看板(阶段2·Master拍板·面板侧呈现/数据侧供数·决策点交叉todos高亮) -->
  <section data-tab="tasklines">
    <div class="sec-h">🎯 长期任务线看板
      <span class="sub">长期任务线全景 · 数据 <span class="mono">tasklines.json</span>(MasterAgent维护)· ⧗=待用户决策(交叉待办池)· 点卡展开详情</span>
      <span class="spacer" style="flex:1"></span>
      <span id="tlMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="tlRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="tlBox" style="padding:6px 0"></div></div>
  </section>

  <!-- 🧩 工作组看板(池化 bus·使用方出口径/MasterUI出可视化) -->
  <section data-tab="groups">
    <div class="sec-h">🧩 工作组看板
      <span class="sub">池化 bus 的组视图 · 成员激活/组inbox深度/负载分布/并行系数 · 数据 <span class="mono">_groups/</span>+<span class="mono">events.jsonl</span>(使用方口径·只读元数据)</span>
      <span class="spacer" style="flex:1"></span>
      <span id="grpMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="grpRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="grpBox" style="padding:6px 0"></div></div>
  </section>

  <!-- 🔧 机制注册表(bus 提供什么·使用方出数据/MasterUI render) -->
  <section data-tab="mechanisms">
    <div class="sec-h">🔧 机制注册表
      <span class="sub">bus <b>提供哪些功能</b>的单一视图 · 数据 <span class="mono">bus_mechanisms.json</span> · <b>机制≠能力池</b>:caps=谁被授权做什么(粗·授权)、机制=bus提供什么(细·基建);related_cap 只是交叉链</span>
      <span class="spacer" style="flex:1"></span>
      <span id="mechMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="mechRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="mechBox" style="padding:6px 0"></div></div>
  </section>

  <section data-tab="roles">
    <div class="sec-h">👤 角色注册表
      <span class="sub">管理/基建角色的<b>单一真相源</b> · 数据 <span class="mono">role_profiles.json</span> · 每角色:层级/一句话/cwd·mode·caps + 启动流程 + <b>明式输入</b>(用户/Master 显式派)/ <b>隐式输入</b>(自举时读的锚点) + 边界</span>
      <span class="spacer" style="flex:1"></span>
      <span id="rolesMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="rolesRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="rolesBox" style="padding:6px 0"></div></div>
    <!-- ⏱ 生命周期总览(懒加载·点展开) -->
    <div style="border-top:1px solid var(--line)">
      <div id="lcToggle" onclick="toggleLifecycle()" style="padding:7px 16px;font-size:12.5px;cursor:pointer;display:flex;align-items:center;gap:8px;color:var(--dim);user-select:none"><span id="lcArrow">▸</span> 角色全生命周期总览 <span id="lcMeta" class="muted" style="font-size:11px"></span></div>
      <div id="lcBox" style="display:none;padding:0 0 8px"></div>
    </div>
  </section>

  <section data-tab="bizroles">
    <div class="sec-h">🏢 业务角色
      <span class="sub">业务执行层角色档案 · 数据 <span class="mono">role_profiles_biz.json</span>(业务数据·不进 git)</span>
      <span class="spacer" style="flex:1"></span>
      <span id="bizRolesMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="bizRolesRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="bizRolesBox" style="padding:6px 0"></div></div>
  </section>

  <!-- 🗂️ 长程任务·锚定生长网(P0:脊柱+当前段+进度·使用方简报/MasterAgent引擎+数据/MasterUI render) -->
  <section data-tab="longrange">
    <div class="sec-h">🗂️ 长程任务 · 锚定生长网
      <span class="sub">用户钉里程碑脊柱 → 网只长到下一锚点收敛 → 塌缩归档 · <b>P0</b>:脊柱+当前活跃段+进度 · 数据 <span class="mono">longrange_tasks.json</span>(MasterAgent引擎)· 进度分母会生长/可倒退=<b>诚实信号非bug</b></span>
      <span class="spacer" style="flex:1"></span>
      <span id="lrMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="lrRefresh">刷新</button>
    </div>
    <div class="sec-b"><div id="lrBox" style="padding:6px 0"></div></div>
  </section>

  <section data-tab="paths">
    <div class="sec-h">📁 路径注册表 <span class="sub">特殊路径地图(记忆库/存档/外链/配置/运行态方舟) · 单一源 <span class="mono">path_registry.json</span> · 健康校验 · 点路径复制</span>
      <span class="spacer" style="flex:1"></span>
      <span id="pathsMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="pathsRefresh">刷新</button>
    </div>
    <div class="sec-h" style="font-size:13px;padding:5px 16px;background:var(--bg2);border-top:1px solid var(--line)">💾 存储全景 <span class="sub">三分格局 · 项目根内分布 · 违规候选</span>
      <span class="spacer" style="flex:1"></span>
      <span id="storageMeta" class="muted" style="font-size:11px"></span>
      <button class="btn ghost mini" id="storageRefresh">刷新存储</button>
    </div>
    <div id="storagePanel" style="padding:8px 16px 12px"><div class="muted" style="font-size:12px">加载中…</div></div>
    <div class="sec-h" style="font-size:13px;padding:5px 16px;background:var(--bg2);border-top:1px solid var(--line)">📁 路径注册表 <span class="sub">特殊路径地图 · 健康校验 · 点路径复制</span>
    </div>
    <div class="sec-b" id="pathsBox"><div class="muted" style="padding:8px 16px">加载中…</div></div>
  </section>

  <!-- 📋 舰队日志(fleet_log.jsonl 编年史·倒序) -->
  <section data-tab="fleetlog">
    <div class="sec-h">📋 舰队日志
      <span class="sub">bus 编年史 · <span class="mono">fleet_log.jsonl</span> · 里程碑/事件/维修/退役 · 倒序</span>
      <span class="spacer" style="flex:1"></span>
      <span id="flMeta" class="muted" style="font-size:11.5px"></span>
      <button class="btn ghost mini" id="flRefresh">刷新</button>
    </div>
    <div class="sec-b" id="flBox"><div class="muted" style="padding:8px 16px">加载中…</div></div>
  </section>

  <section data-tab="asr">
    <div class="sec-h">🎙 语音转写
      <span class="sub">本地实时语音转文字(faster-whisper·全程不上云)· 系统声/麦克风 → 断句 → 简体</span>
      <span class="spacer" style="flex:1"></span>
      <button class="btn ghost mini" id="asrRefresh">刷新设备</button>
    </div>
    <div class="sec-b">
      <div id="asrDeviceBox" style="padding:10px 16px"><div class="muted">加载中…</div></div>
      <div style="padding:6px 16px 10px;display:flex;gap:12px;align-items:center;border-bottom:1px solid var(--line)">
        <button class="btn" id="asrStartBtn">▶ 启动转写</button>
        <button class="btn ghost" id="asrStopBtn" style="display:none">■ 停止</button>
        <span id="asrStatus" class="muted">就绪</span>
      </div>
      <div id="asrLinesBox" style="padding:8px 16px;max-height:60vh;overflow-y:auto"><div class="muted">尚无转写。选好音频源点“启动转写”</div></div>
    </div>
  </section>
</main>

<div id="toast" class="toast"></div>

"""
