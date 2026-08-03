#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_css —— WebUI 样式段(UI2.0 U4 拆自 page.py·纯搬运字节不改)。"""
CSS = r""":root{
  /* U5 视觉升级(v2.6·2026-08-03):GitHub Primer Dark 对标色板 + 语义分层。
     对比度实测(WCAG):txt 13.7 / dim 5.56 / muted 5.26 / none 3.52(on panel2);
     状态徽标同色底 live 5.32 / warn 5.37 / accent 5.31 / dead 4.35(bold 11px·≥3 达标)。 */
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2128;
  --line:#30363d; --line-muted:#21262d; --line-strong:#3d444d;
  --txt:#e6edf3; --dim:#9198a1; --muted:#8b949e; --none:#6e7681;
  --accent:#58a6ff; --accent2:rgba(56,139,253,.15);
  --live:#3ecf8e; --warn:#d29922; --dead:#f85149; --idle:#8b949e;
  /* 状态 RGB 通道(rgba(var(--x-rgb),α) 收编全站散落 rgba·alpha 层次保留、色相单点) */
  --live-rgb:62,207,142; --warn-rgb:210,153,34; --dead-rgb:248,81,73;
  --accent-rgb:88,166,255; --idle-rgb:139,148,158; --none-rgb:110,118,129; --purple-rgb:163,113,247;
  /* 交互/层次 */
  --hover:rgba(177,186,196,.08); --focus:#1f6feb; --shadow:0 1px 3px rgba(1,4,9,.45);
  --mono:ui-monospace,Consolas,"Courier New",monospace;
}
/* 交互反馈(业界标配):可点元素统一 120ms 过渡 + 键盘 focus ring */
.tab,.btn,button,.cap-chip,.route-chip,tr.role-row,.sess-row,select,input,textarea{
  transition:background-color .12s ease,border-color .12s ease,color .12s ease}
:focus-visible{outline:2px solid var(--focus);outline-offset:1px}
tbody tr:hover{background:var(--hover)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#2a313c;border-radius:5px;border:2px solid var(--bg)}
::-webkit-scrollbar-thumb:hover{background:var(--line-strong)}
::-webkit-scrollbar-track{background:transparent}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 "Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--txt)}
a{color:var(--accent)}
header{display:flex;align-items:center;gap:14px;padding:12px 18px;background:var(--panel);
  border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}
header h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.5px}
.spacer{flex:1}
.pill{font-size:12px;padding:3px 10px;border-radius:20px;background:var(--panel2);
  border:1px solid var(--line);color:var(--dim);display:inline-flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:var(--idle)}
.dot.on{background:var(--live);box-shadow:0 0 6px var(--live)}
.dot.off{background:var(--dead)}
main{padding:20px 18px;max-width:1500px;margin:0 auto;display:grid;gap:26px}
.grid2{display:grid;grid-template-columns:1.7fr 1fr;gap:26px}
@media(max-width:1080px){.grid2{grid-template-columns:1fr}}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:var(--shadow)}
.sec-h{padding:11px 16px;border-bottom:1px solid var(--line);font-weight:650;font-size:13.5px;box-shadow:inset 3px 0 0 var(--accent2);
  display:flex;align-items:center;gap:10px;background:var(--panel2)}
.sec-h .sub{font-weight:400;color:var(--dim);font-size:12px}
.sec-b{padding:8px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 16px;border-bottom:1px solid var(--line-muted);vertical-align:top}
thead th{border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.6px}
tr:last-child td{border-bottom:none}
tr.role-row{cursor:default}
tr.role-row.clickable{cursor:pointer}
tr.role-row.clickable:hover{background:var(--panel2)}
.badge{font-size:11px;padding:2px 9px;border-radius:6px;white-space:nowrap;font-weight:600}
.badge.vbadge{display:inline-block;max-width:120px;overflow:hidden;text-overflow:ellipsis;vertical-align:bottom}
.badge.live{background:rgba(var(--live-rgb),.15);color:var(--live)}
.badge.warn{background:rgba(var(--warn-rgb),.15);color:var(--warn)}
.badge.dead{background:rgba(var(--dead-rgb),.15);color:var(--dead)}
.badge.idle{background:rgba(var(--idle-rgb),.15);color:var(--idle)}
.badge.none{background:rgba(var(--none-rgb),.15);color:var(--none)}
.badge.parked{background:rgba(var(--purple-rgb),.16);color:#a78bfa}
.mono{font-family:var(--mono);font-size:12px}
.cwd{color:var(--dim);font-size:12px;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.qcount{display:inline-block;min-width:30px;text-align:center;padding:1px 8px;border-radius:6px;
  font-variant-numeric:tabular-nums;font-weight:600;font-size:12px}
.qcount.z{color:var(--none);background:transparent}
.qcount.ib{background:rgba(var(--accent-rgb),.16);color:var(--accent)}
.qcount.pr{background:rgba(var(--warn-rgb),.16);color:var(--warn)}
.qcount.dn{background:rgba(var(--live-rgb),.14);color:var(--live)}
.qcount.held{background:rgba(var(--warn-rgb),.22);color:var(--warn);box-shadow:0 0 0 1px rgba(var(--warn-rgb),.5)}
.qcount.failed{background:rgba(var(--dead-rgb),.22);color:var(--dead);box-shadow:0 0 0 1px rgba(var(--dead-rgb),.55)}
.badge.appr{background:rgba(var(--warn-rgb),.18);color:var(--warn);border:1px solid rgba(var(--warn-rgb),.5)}
.badge.held{background:rgba(var(--warn-rgb),.15);color:var(--warn)}
.badge.failed{background:rgba(var(--dead-rgb),.15);color:var(--dead)}
.hint{font-size:11px;color:var(--warn);margin-top:4px}
.ev{font-size:12px;padding:5px 16px;border-bottom:1px solid var(--line-muted);display:flex;gap:9px;align-items:baseline;flex-wrap:wrap}
.ev:last-child{border-bottom:none}
.ev .et{font-size:11px;padding:1px 7px;border-radius:5px;font-weight:600}
.ev .et.send{background:rgba(var(--accent-rgb),.16);color:var(--accent)}
.ev .et.claim{background:rgba(var(--warn-rgb),.16);color:var(--warn)}
.ev .et.done{background:rgba(var(--live-rgb),.16);color:var(--live)}
.ev .ets{color:var(--none);font-variant-numeric:tabular-nums}
.dmn{display:flex;align-items:center;gap:10px;padding:8px 16px;border-bottom:1px solid var(--line-muted);font-size:13px}
.dmn:last-child{border-bottom:none}
.chk{display:flex;align-items:center;gap:7px;margin:10px 16px 4px;font-size:12px;color:var(--dim);cursor:pointer}
.chk input{width:auto;margin:0}
.tasks{background:var(--bg)}
.tasks td{padding:0 16px}
.task-wrap{padding:10px 0}
.task-card{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:6px 0}
.task-card .t-h{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--dim);margin-bottom:5px}
.task-card .t-title{color:var(--txt);font-weight:600}
.task-card pre{margin:6px 0 0;white-space:pre-wrap;word-break:break-word;font-size:12px;color:#cdd4e0;
  background:var(--bg);padding:8px 10px;border-radius:6px;max-height:240px;overflow:auto}
.foot{padding:10px 16px;color:var(--dim);font-size:12px;border-top:1px solid var(--line)}
label{display:block;font-size:12px;color:var(--dim);margin:10px 16px 4px}
input[type=text],textarea,select{width:calc(100% - 32px);margin:0 16px;background:var(--bg);
  color:var(--txt);border:1px solid var(--line);border-radius:7px;padding:8px 10px;font:13px/1.4 inherit}
textarea{min-height:120px;resize:vertical;font-family:var(--mono)}
.btn{margin:14px 16px 16px;background:var(--accent);color:#06112a;border:none;border-radius:7px;
  padding:9px 18px;font-weight:600;cursor:pointer;font-size:13px}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:transparent;color:var(--dim);border:1px solid var(--line);font-weight:500}
.btn.danger{background:transparent;color:var(--dead);border:1px solid rgba(var(--dead-rgb),.4)}
.btn:disabled{opacity:.5;cursor:default}
.reply{margin:8px 16px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.reply .r-h{display:flex;gap:8px;align-items:center;font-size:12px;margin-bottom:5px;flex-wrap:wrap}
.reply pre{margin:5px 0 0;white-space:pre-wrap;word-break:break-word;font-size:12px;color:#cdd4e0;
  background:var(--bg);padding:8px 10px;border-radius:6px;max-height:200px;overflow:auto}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tag{font-size:12px;padding:3px 9px;border-radius:6px;background:var(--accent2);color:#cfe0ff;margin:2px}
.tag.lock{background:rgba(var(--warn-rgb),.16);color:var(--warn)}
.muted{color:var(--dim)}
.toast{position:fixed;right:18px;bottom:18px;background:var(--panel2);border:1px solid var(--line);
  border-left:3px solid var(--live);border-radius:8px;padding:11px 16px;max-width:380px;
  box-shadow:0 8px 30px rgba(0,0,0,.5);opacity:0;transform:translateY(10px);transition:.25s;font-size:13px}
.toast.show{opacity:1;transform:none}
.toast.err{border-left-color:var(--dead)}
.ctrl{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--dim)}
.ctrl select{width:auto;margin:0;padding:4px 8px}
.flash{animation:flash 1s ease}
@keyframes flash{0%{background:rgba(var(--accent-rgb),.25)}100%{background:transparent}}
/* tab 分页 */
.tabbar{position:sticky;top:0;z-index:9;display:flex;gap:6px;padding:9px 18px;background:var(--panel);
  border-bottom:1px solid var(--line);flex-wrap:wrap;box-shadow:0 2px 8px rgba(0,0,0,.25)}
.tab{background:var(--panel2);color:var(--dim);border:1px solid var(--line);border-radius:8px;
  padding:7px 16px;font:13px/1 "Segoe UI",system-ui,sans-serif;cursor:pointer;font-weight:500}
.tab:hover{color:var(--txt);border-color:var(--line-strong)}
.tab.active{background:var(--panel2);color:var(--txt);border-color:var(--line-strong);box-shadow:inset 0 -2px 0 var(--accent);font-weight:700}
.tabhide{display:none !important}
.gconfirm{font-size:12px;font-weight:700;padding:3px 11px;border-radius:8px;cursor:pointer;
  background:rgba(var(--purple-rgb),.2);color:#c9b3ff;border:1px solid rgba(var(--purple-rgb),.6)}
.gconfirm:hover{background:rgba(var(--purple-rgb),.34)}
/* bus 健康徽标 */
.hwrap{position:relative;display:inline-block}
.hbadge{font-size:12px;font-weight:700;padding:3px 11px;border-radius:8px;cursor:pointer;border:1px solid;white-space:nowrap}
.hbadge.ok{background:rgba(var(--live-rgb),.16);color:#7ee0a6;border-color:rgba(var(--live-rgb),.55)}
.hbadge.warn{background:rgba(var(--warn-rgb),.16);color:#f0cf7a;border-color:rgba(var(--warn-rgb),.6)}
.hbadge.loading{background:var(--bg);color:var(--muted);border-color:var(--line)}
.hbadge.danger{background:rgba(var(--dead-rgb),.18);color:#ff9b9b;border-color:rgba(var(--dead-rgb),.6)}
.pmt-sec{border:1px solid var(--line);border-radius:8px;margin:8px 0;overflow:hidden;background:var(--panel2)}
.pmt-sec.err{border-color:rgba(var(--warn-rgb),.5)}
.pmt-h{display:flex;gap:12px;align-items:baseline;padding:9px 12px;cursor:pointer;user-select:none}
.pmt-h:hover{background:rgba(var(--accent-rgb),.08)}
.pmt-t{font-weight:600;color:var(--txt)}
.pmt-meta{font-size:11px;color:var(--dim);margin-left:auto;text-align:right;word-break:break-all}
.pmt-b{margin:0;padding:12px 14px;border-top:1px solid var(--line);background:var(--bg);
  font:12px/1.55 Consolas,monospace;color:var(--txt);white-space:pre-wrap;word-break:break-word;max-height:60vh;overflow:auto}
.pmt-code{margin:0;border-top:1px solid var(--line);background:var(--bg);font:12px/1.55 Consolas,monospace;
  color:var(--txt);max-height:60vh;overflow:auto;padding:8px 0}
.pmt-code .pln{display:flex;align-items:flex-start}
.pmt-code .pln:hover{background:rgba(var(--accent-rgb),.06)}
.pmt-code .lno{flex:0 0 auto;min-width:44px;padding:0 12px 0 8px;text-align:right;color:var(--dim);
  user-select:none;opacity:.6}
.pmt-code .lco{flex:1;white-space:pre-wrap;word-break:break-word;padding-right:14px}
.pmt-open{margin-left:10px;flex:0 0 auto}
.ctx-kpis{display:flex;gap:10px;flex-wrap:wrap;padding:12px 16px 6px}
.ctx-kpis .k{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:8px 14px;min-width:96px}
.ctx-kpis .k .v{font-size:18px;font-weight:700}
.ctx-kpis .k .l{font-size:11px;color:var(--dim)}
.ctxpct{font-weight:700;font-variant-numeric:tabular-nums}
.ctxpct.green{color:var(--live)} .ctxpct.yellow{color:var(--warn)} .ctxpct.red{color:var(--dead)}
.ctxbar{display:inline-block;height:7px;border-radius:4px;vertical-align:middle;margin-left:7px;min-width:2px;background:currentColor;opacity:.55}
tr.ctx-red td{background:rgba(var(--dead-rgb),.08)}
.winassumed{color:var(--dim);font-size:10.5px}
.hpanel{position:absolute;top:30px;left:0;z-index:30;width:360px;max-height:70vh;overflow:auto;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
  box-shadow:0 10px 34px rgba(0,0,0,.5);font-size:12px;line-height:1.5;cursor:default}
.hpanel h4{margin:0 0 6px;font-size:12px;color:var(--txt)}
.hpanel .hwarn{color:#f0cf7a;background:rgba(var(--warn-rgb),.1);border-radius:5px;padding:3px 8px;margin:3px 0}
.hpanel .hblk{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-top:1px solid var(--line)}
.hpanel .hblk b{color:var(--txt);font-weight:600}
.hpanel .hk{color:var(--muted)}
.hpanel .hbad{color:var(--dead)} .hpanel .hgood{color:#7ee0a6}
/* 历史用量 BI */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;padding:14px 16px}
.kpi{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px 12px}
.kpi .k{font-size:11px;color:var(--dim)}
.kpi .v{font-size:20px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi .v.good{color:var(--live)} .kpi .v.acc{color:var(--accent)} .kpi .v.warn{color:var(--warn)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:0 4px}
@media(max-width:880px){.grid2{grid-template-columns:1fr}}
.uchart-cap{font-size:12px;color:var(--dim);padding:4px 16px 8px;font-weight:600}
canvas{display:block;width:100%}
.bars{padding:2px 16px 12px}
.bar-row{display:grid;grid-template-columns:130px 1fr 96px;gap:10px;align-items:center;margin:7px 0;font-size:12.5px}
.bar-row .lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{height:15px;background:var(--line);border:1px solid #333a47;border-radius:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,#5b9dff,#8ab8ff);box-shadow:0 0 0 1px rgba(var(--accent-rgb),.25)}
.bar-row .num{text-align:right;font-family:var(--mono);font-size:12px;color:var(--dim)}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--dim);padding:6px 16px 14px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
/* 历史用量:范围切换 + 趋势 hover + 项目下钻 */
.urange{display:inline-flex;gap:2px;margin-left:8px;vertical-align:middle}
.urange button{background:var(--panel2);color:var(--dim);border:1px solid var(--line);font:600 11px Consolas,monospace;padding:2px 8px;border-radius:4px;cursor:pointer}
.urange button:hover{color:var(--txt)}
.urange button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.uchart-wrap{position:relative}
.uchart-tip{position:absolute;pointer-events:none;display:none;background:rgba(10,12,16,.96);border:1px solid var(--line);border-radius:5px;padding:5px 8px;font:11px Consolas,monospace;color:#e6ebf2;white-space:nowrap;z-index:5;box-shadow:0 2px 8px rgba(0,0,0,.45)}
.trend-legend{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:4px 16px 2px;font-size:11px;color:var(--dim)}
.trend-legend span{display:inline-flex;align-items:center;gap:5px}
.trend-legend i{width:12px;height:8px;border-radius:2px;display:inline-block}
.trend-legend i.gapi{background:repeating-linear-gradient(45deg,rgba(var(--idle-rgb),.5),rgba(var(--idle-rgb),.5) 2px,transparent 2px,transparent 4px);border:1px solid var(--line)}
.uproj-row{cursor:pointer}
.uproj-row:hover .lbl{color:var(--accent)}
.uproj-raw{padding:3px 0 7px 10px;margin:-2px 0 5px;border-left:2px solid var(--line)}
.uproj-raw .bar-row{margin:5px 0;font-size:11.5px}
/* 派工/协作效率(仅自动化派工口径)*/
.dm-note{margin:0 16px 4px;padding:8px 12px;font-size:12px;line-height:1.5;color:#f0d9a0;
  background:rgba(var(--warn-rgb),.10);border:1px solid rgba(var(--warn-rgb),.4);border-radius:8px}
.dm-note b{color:var(--warn)}
.kind-tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;font-weight:600;vertical-align:1px;margin-right:4px}
.kind-tag.daemon{background:rgba(var(--accent-rgb),.16);color:var(--accent)}
.kind-tag.dispatch{background:rgba(var(--warn-rgb),.16);color:var(--warn)}
/* 可恢复会话 */
.sess-row{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;margin:6px 16px;padding:9px 12px;
  background:var(--panel2);border:1px solid var(--line);border-radius:8px}
.sess-row.agent{border-left:3px solid var(--accent)}
.sess-main{min-width:0}
.sess-title{font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sess-meta{font-size:11.5px;color:var(--dim);margin-top:3px;display:flex;gap:12px;flex-wrap:wrap}
.sess-meta .mono{font-size:11px}
.sess-row .copy{white-space:nowrap}
/* 状态列:运行中(绿▶+role)/ 死(灰) */
.sess-row.live{border-left:3px solid var(--live)}
.sess-row.dead{opacity:.82}
.sess-state{font-size:11px;font-weight:700;padding:2px 9px;border-radius:6px;white-space:nowrap;display:inline-flex;align-items:center;gap:5px}
.sess-state.live{background:rgba(var(--live-rgb),.16);color:var(--live)}
.sess-state.dead{background:rgba(var(--idle-rgb),.14);color:var(--idle)}
.sess-state .role{color:var(--live);font-weight:700}
/* 属于角色 chip(卡片右上·四态可靠度口径 2026-08-03):权威=实底,推断=虚线弱化,未知=灰 */
.sess-side{display:flex;flex-direction:column;align-items:flex-end;gap:5px}
.role-chip{font-size:11px;font-weight:700;padding:1px 8px;border-radius:10px;white-space:nowrap}
.role-chip.src-bound{background:rgba(var(--live-rgb),.16);color:var(--live)}
.role-chip.src-ledger,.role-chip.src-archive{background:rgba(var(--accent-rgb),.15);color:var(--accent)}
.role-chip.src-inferred{border:1px dashed var(--line);color:var(--dim);font-weight:400}
.role-chip.src-unknown{color:var(--none);font-weight:400}
/* 🏷 标签 chip */
.sess-tags{display:inline-flex;gap:5px;flex-wrap:wrap}
.tag-chip{font-size:11px;padding:1px 8px;border-radius:20px;background:rgba(var(--accent-rgb),.15);color:var(--accent);border:1px solid rgba(var(--accent-rgb),.3)}
/* 会话筛选钮 + 筛选组 */
.sess-filt,.sess-own,.sess-sort{margin:0;padding:3px 11px;font-size:12px}
.sess-filt.active,.sess-own.active,.sess-sort.active{background:var(--accent);color:#06112a;border-color:var(--accent);font-weight:600}
/* 会话 大小徽标(大=越吃上下文/越接近compact/越该归档清) */
.sz-chip{font-family:Consolas,monospace;font-size:11px;padding:0 5px;border-radius:4px;background:var(--panel2);color:var(--dim)}
.sz-chip.warn{color:var(--warn);background:rgba(var(--warn-rgb),.12)}
.sz-chip.red{color:#ff6b6b;background:rgba(var(--dead-rgb),.14);font-weight:600}
.filt-grp{display:inline-flex;align-items:center;gap:4px;margin-left:8px}
.filt-lbl{font-size:11px;color:var(--dim);margin-right:2px}
/* 归属 chip:bus / 个人 */
.own-chip{font-size:11px;padding:1px 8px;border-radius:6px;white-space:nowrap;font-weight:600}
.own-chip.bus{background:rgba(var(--accent-rgb),.15);color:var(--accent)}
.own-chip.personal{background:rgba(var(--idle-rgb),.14);color:var(--idle)}
/* 待办池 TODO Pool */
.todo-card{margin:8px 16px;background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px 12px;border-left:3px solid var(--line)}
.todo-card.pri-hi{border-left-color:var(--dead)}
.todo-card.pri-mid{border-left-color:var(--warn)}
.todo-card.pri-lo{border-left-color:var(--idle)}
.todo-card.is-done{opacity:.6}
/* 待确认:agent 在等用户拍板,醒目区分 */
.todo-card.pending-confirm{border-left-color:#9a7bff !important;background:rgba(var(--purple-rgb),.10);box-shadow:0 0 0 1px rgba(var(--purple-rgb),.4)}
.badge.confirm{background:rgba(var(--purple-rgb),.20);color:#c9b3ff;border:1px solid rgba(var(--purple-rgb),.55)}
.confirm-who{color:#c9b3ff;font-weight:600}
/* 待办池视图切换 tab */
.tview-btn{margin:0;padding:3px 11px;font-size:12px}
.tview-btn.active{background:var(--accent);color:#06112a;border-color:var(--accent);font-weight:600}
.confirm-alert{font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:5px;background:rgba(var(--purple-rgb),.18);color:#c9b3ff;border:1px solid rgba(var(--purple-rgb),.5)}
.todo-h{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.todo-title{font-weight:600;font-size:13.5px}
.todo-detail{color:#cdd4e0;font-size:12.5px;margin:6px 0 0;white-space:pre-wrap;word-break:break-word}
.todo-meta{color:var(--dim);font-size:11.5px;margin-top:6px;display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.who-pill{background:var(--accent2);color:#cfe0ff;padding:1px 8px;border-radius:5px;font-weight:600}
.dnote{color:#cdd4e0;background:var(--bg);padding:1px 7px;border-radius:5px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.note-area{margin-top:7px;display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap}
.unote{color:#cdd4e0;background:var(--bg);padding:2px 9px;border-radius:5px;font-size:12px;white-space:pre-wrap;word-break:break-word;max-width:560px}
.nbtn{margin:0;padding:2px 9px;font-size:11px}
.note-edit{width:100%;min-height:48px;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:12px/1.45 inherit;resize:vertical}
.answer-area{margin-top:7px;padding:7px 9px;background:rgba(var(--purple-rgb),.08);border:1px solid rgba(var(--purple-rgb),.35);border-radius:7px;display:flex;gap:7px;align-items:flex-start;flex-wrap:wrap}
.ans-edit{flex:1;min-width:220px;min-height:42px;background:var(--bg);color:var(--txt);border:1px solid rgba(var(--purple-rgb),.5);border-radius:6px;padding:6px 8px;font:12px/1.45 inherit;resize:vertical}
.note-btns{display:flex;gap:6px;margin-top:5px}
.todo-acts{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
.todo-acts select,.todo-acts input{width:auto;margin:0;padding:3px 7px;font-size:12px}
.badge.p-hi{background:rgba(var(--dead-rgb),.16);color:var(--dead)}
.badge.p-mid{background:rgba(var(--warn-rgb),.16);color:var(--warn)}
.badge.p-lo{background:rgba(var(--idle-rgb),.16);color:var(--idle)}
.badge.s-todo{background:rgba(var(--idle-rgb),.16);color:var(--idle)}
.badge.s-doing{background:rgba(var(--accent-rgb),.16);color:var(--accent)}
.badge.s-done{background:rgba(var(--live-rgb),.16);color:var(--live)}
.badge.s-hold{background:rgba(var(--warn-rgb),.16);color:var(--warn)}
.overdue{color:var(--dead);font-weight:600}
.linkpill{font-size:11px;padding:1px 7px;border-radius:5px;background:rgba(var(--live-rgb),.13);color:var(--live)}
.btn.mini{margin:0;padding:3px 10px;font-size:12px}
.todo-add{padding:6px 16px 12px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:1fr 1fr;gap:6px 10px}
.todo-add .full{grid-column:1 / -1}
.todo-add input,.todo-add textarea,.todo-add select{width:100%;margin:0}
.todo-add textarea{min-height:48px}
.filt{display:flex;gap:6px;align-items:center}
.filt select{width:auto;margin:0;padding:3px 8px;font-size:12px}
/* 实时视图 live.log */
.livebox{margin:0;white-space:pre-wrap;word-break:break-word;font-family:"Cascadia Code",Consolas,monospace;
  font-size:12px;line-height:1.55;color:#cdd4e0;background:var(--bg);padding:12px 16px;
  height:380px;overflow:auto;border-top:1px solid var(--line)}
.live-meta{font-size:11.5px;color:var(--dim);padding:6px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--idle);display:inline-block}
.live-dot.on{background:var(--live);box-shadow:0 0 6px var(--live);animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.bi-kpis{display:flex;gap:10px;flex-wrap:wrap;padding:12px 16px 6px}
.bi-kpi{background:var(--card,#1a1d24);border:1px solid var(--line);border-radius:8px;padding:9px 14px;min-width:118px}
.bi-kpi .k{font-size:11px;color:var(--dim);margin-bottom:3px}
.bi-kpi .v{font-size:19px;font-weight:700;color:var(--txt)}
.bi-kpi .v small{display:block;font-size:11px;font-weight:400;color:var(--dim);margin-top:3px;margin-left:0}
.bi-kpi.warn .v{color:var(--warn,#e6a23c)}
.bi-run{display:inline-flex;align-items:center;gap:6px;background:rgba(var(--live-rgb),.12);border:1px solid var(--live);border-radius:6px;padding:3px 9px;margin:3px 6px 3px 0;font-size:12px}
table.bi-tbl td,table.bi-tbl th{font-variant-numeric:tabular-nums}
table.bi-tbl td.num,table.bi-tbl thead th:not(:first-child){text-align:right}
table.bi-tbl td.num{font-family:ui-monospace,monospace}
/* 能力池 caps */
.cap-pool{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;padding:12px 16px}
.cap-card{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px 12px;border-left:3px solid var(--accent2)}
.cap-card.edge{border-left-color:var(--warn)} .cap-card.hi{border-left-color:var(--dead)}
.cap-card .cn{font-weight:700;font-size:13px;font-family:"Cascadia Code",Consolas,monospace}
.cap-card .cd{font-size:11.5px;color:var(--dim);margin-top:5px;line-height:1.45}
.cap-card .cf{font-size:11px;color:var(--idle);margin-top:5px}
.cap-card .ch{font-size:12px;margin-top:6px;font-variant-numeric:tabular-nums}
.cap-card .ch b{font-size:16px;color:var(--txt)}
.cap-card.zero .ch b{color:var(--dead)}
.rb{display:inline-block;font-size:10px;padding:1px 7px;border-radius:5px;font-weight:700;vertical-align:1px;margin-left:6px}
.rb.edge{background:rgba(var(--warn-rgb),.18);color:var(--warn);border:1px solid rgba(var(--warn-rgb),.5)}
.rb.hi{background:rgba(var(--dead-rgb),.18);color:var(--dead);border:1px solid rgba(var(--dead-rgb),.5)}
/* caps 矩阵 */
table.capmx{width:100%;border-collapse:collapse;font-size:12.5px}
table.capmx th,table.capmx td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:center}
table.capmx th:first-child,table.capmx td:first-child{text-align:left;white-space:nowrap}
table.capmx thead th{color:var(--dim);font-size:10.5px;font-weight:600;text-transform:none;letter-spacing:.3px;
  font-family:"Cascadia Code",Consolas,monospace;writing-mode:initial}
table.capmx thead th.edge{color:var(--warn)} table.capmx thead th.hi{color:var(--dead)}
table.capmx tbody tr:hover{background:var(--panel2)}
table.capmx .yes{color:var(--live);font-weight:700} table.capmx .no{color:var(--none)}
table.capmx td.cnt{color:var(--dim);font-variant-numeric:tabular-nums}
.mx-role{font-weight:600} .mx-role.ret{color:var(--idle);text-decoration:line-through}
.mx-foot{font-size:11px;color:var(--dim);padding:2px 16px 10px}
.gap-box{margin:6px 16px 4px;padding:9px 12px;border-radius:8px;font-size:12.5px;line-height:1.6}
.gap-box.todo{background:rgba(var(--warn-rgb),.10);border:1px solid rgba(var(--warn-rgb),.4)}
.gap-box.none{background:rgba(var(--dead-rgb),.08);border:1px solid rgba(var(--dead-rgb),.35)}
.gap-box.ok{background:rgba(var(--live-rgb),.08);border:1px solid rgba(var(--live-rgb),.3)}
.gap-box .gh{font-weight:700;margin-bottom:3px}
.cap-chip{display:inline-block;font-size:11px;padding:1px 8px;border-radius:5px;margin:2px;
  background:var(--accent2);color:#cfe0ff;font-family:"Cascadia Code",Consolas,monospace}
.cap-chip.warn{background:rgba(var(--warn-rgb),.18);color:var(--warn)}
.cap-chip.dead{background:rgba(var(--dead-rgb),.18);color:var(--dead)}
.cap-chip.live{background:rgba(var(--live-rgb),.16);color:var(--live)}
.cap-chip.idle{background:rgba(var(--idle-rgb),.15);color:var(--idle)}
.cap-chip.none{background:rgba(var(--none-rgb),.15);color:var(--none)}
/* 🗂️ 长程任务·锚定生长网 */
.lr-spine{display:flex;flex-wrap:wrap;align-items:center;gap:2px;margin-top:4px}
.lr-anchor{display:inline-flex;flex-direction:column;font-size:11.5px;padding:3px 9px;border-radius:6px;border:1px solid var(--line);line-height:1.3}
.lr-anchor.u{background:rgba(var(--accent-rgb),.14);border-color:rgba(var(--accent-rgb),.45)}
.lr-anchor.m{background:transparent;border-style:dashed;color:var(--dim)}
.lr-au{font-size:9px;color:var(--dim);margin-top:1px}
.lr-arrow{color:var(--dim);margin:0 3px;font-weight:700}
.lr-net{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.lr-node{font-size:11px;padding:2px 9px;border-radius:6px;border:1px solid var(--line);white-space:nowrap}
.lr-node.idle{background:rgba(var(--idle-rgb),.14);color:var(--idle)}
.lr-node.accent{background:rgba(var(--accent-rgb),.16);color:var(--accent)}
.lr-node.dead{background:rgba(var(--dead-rgb),.16);color:var(--dead)}
.lr-node.live{background:rgba(var(--live-rgb),.16);color:var(--live)}
.lr-node.warn{background:rgba(var(--warn-rgb),.18);color:var(--warn)}
/* ⓪ Agent 详情(会哪些内容)*/
.ag-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:11px;padding:12px 16px}
.ag-card{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:11px 13px;border-left:3px solid var(--accent2);display:flex;flex-direction:column;gap:7px}
.ag-card.daemon{border-left-color:var(--idle)} .ag-card.cli{border-left-color:var(--live)}
.ag-card.off{border-left-color:var(--none)} .ag-card.ret{border-left-color:var(--dead);opacity:.82}
.ag-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ag-name{font-weight:700;font-size:14px}
.ag-card.ret .ag-name{text-decoration:line-through;color:var(--idle)}
.ag-form{display:inline-block;font-size:10px;padding:1px 8px;border-radius:5px;font-weight:700;font-family:"Cascadia Code",Consolas,monospace}
.ag-form.cli{background:rgba(var(--live-rgb),.16);color:var(--live);border:1px solid rgba(var(--live-rgb),.45)}
.ag-form.daemon{background:rgba(var(--accent-rgb),.14);color:var(--idle);border:1px solid rgba(var(--accent-rgb),.4)}
.ag-form.off{background:rgba(var(--idle-rgb),.14);color:var(--none);border:1px solid rgba(var(--idle-rgb),.35)}
.ag-form.ret{background:rgba(var(--dead-rgb),.14);color:var(--dead);border:1px solid rgba(var(--dead-rgb),.4)}
.ag-verdict{font-size:11px;color:var(--dim);font-family:"Cascadia Code",Consolas,monospace}
.ag-succ{font-size:11px;color:var(--idle)}
.ag-desc{font-size:12px;color:var(--dim);line-height:1.5}
.ag-caps{display:flex;flex-wrap:wrap;gap:3px;margin-top:1px}
.ag-meta{font-size:10.5px;color:var(--none);margin-top:1px}
.sec-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:980px){.sec-grid{grid-template-columns:1fr}}
/* U5b 构图重构(用户口径 2026-08-03:块认知·突出主体):舰队表【正常态安静·异常态醒目】——
   满屏亮绿=正常在大声喊,真正的主体(异常)被吃掉;正常转低调文字,色底只留给要人看的异常。 */
#fleetBody .badge.live{background:transparent;color:var(--live);font-weight:500;opacity:.8;padding:2px 4px}
#fleetBody .badge.idle{background:transparent;color:var(--idle);font-weight:400;opacity:.75;padding:2px 4px}
#fleetBody .badge.none{background:transparent;color:var(--none);font-weight:400;padding:2px 4px}
#fleetBody .badge.vbadge.live{background:rgba(var(--live-rgb),.10)}  /* verdict 主徽标保留极淡底=可扫读 */
#fleetBody .qcount.dn{background:transparent;color:var(--dim)}       /* done 计数=历史信息,安静 */
#fleetBody .qcount.ib{font-weight:700}                                /* inbox 待领=要人处理,加重 */

/* U5b 全站原语(块认知三原则):钳行(点击展开)/折叠卡(标题即锚点·正文默认收)/chip 降噪 */
.clampable{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;cursor:s-resize}
.clampable.open{display:block;-webkit-line-clamp:unset;cursor:n-resize}
.fold>.fold-body,.fold .fold-body{display:none}
.fold.open>.fold-body,.fold.open .fold-body{display:block}
.fold-head{cursor:pointer;user-select:none}
.fold-arrow{display:inline-block;transition:transform .12s ease;color:var(--dim);font-size:11px}
.fold.open .fold-arrow{transform:rotate(90deg)}
/* 能力/标签 chip 全站降噪:次要信息安静(色底只留给状态/风险语义) */
.cap-chip{background:transparent;border:1px solid var(--line);color:var(--dim)}
.cap-chip.live{border-color:rgba(var(--live-rgb),.4);color:var(--live)}
.cap-chip.warn{border-color:rgba(var(--warn-rgb),.5);color:var(--warn)}
.cap-chip.none{border-color:var(--line-muted);color:var(--none)}
"""
