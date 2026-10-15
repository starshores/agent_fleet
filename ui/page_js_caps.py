#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js_caps —— page_js 拆分子模块 CAPS(自我迭代波·案①·纯搬运字节一致·2026-09-16)。"""
CAPS = r"""// ===== 能力池 caps =====
let _caps=null;
function renderCaps(d){
  _caps=d; if(!d) return;
  const s=d.summary||{};
  const meta=document.getElementById('capsMeta');
  if(meta) meta.textContent=(d.now||'')+' · '+(s.n_caps||0)+' 能力 / '+(s.n_active||0)+' 现行 agent / '+(s.n_empty||0)+' 待补'+(s.n_retired?(' / '+s.n_retired+' 退役'):'');
  // ⓪ Agent 详情 — 各 agent 会哪些内容(desc)+ caps + 状态
  const agCap=(c)=>'<span class="cap-chip">'+esc(c)+'</span>';
  const agCard=(a)=>{
    const fcls=esc(a.form_cls||'off');
    const form='<span class="ag-form '+fcls+'">'+esc(a.form||'?')+'</span>';
    const verdict=a.retired?'':('<span class="ag-verdict" title="'+esc(a.verdict||'')+'">'+esc(a.verdict||'')+'</span>');
    const succ=(a.retired&&a.succeeded_by)?('<span class="ag-succ">→ 接替:<b>'+esc(a.succeeded_by)+'</b></span>'):'';
    const caps=(a.caps&&a.caps.length)?('<div class="ag-caps">'+a.caps.map(agCap).join('')+'</div>'):'<div class="ag-caps"><span class="muted" style="font-size:11px">(无 caps)</span></div>';
    const desc=a.desc?esc(a.desc):'<span class="muted">（未填 desc / 职责）</span>';
    let metaLine='';
    if(a.retired){ metaLine='<div class="ag-meta">退役 '+esc(a.retired_at||'')+(a.reason?(' · '+esc(a.reason)):'')+'</div>'; }
    else if(a.registered){ metaLine='<div class="ag-meta">注册 '+esc(a.registered)+'</div>'; }
    return '<div class="ag-card '+fcls+'" data-key="ag-'+esc(a.role)+'">'+
      '<div class="ag-head"><span class="ag-name">'+esc(a.role)+'</span>'+form+succ+'</div>'+
      (verdict?('<div>'+verdict+'</div>'):'')+
      '<div class="ag-desc clampable">'+desc+'</div>'+caps+metaLine+'</div>';
  };
  const agAll=(d.agents||[]).map(agCard).join('')+(d.agents_retired||[]).map(agCard).join('');
  morphInto(document.getElementById('agDetail'), agAll||'<div class="muted" style="padding:8px">（无 agent）</div>');
  // ① 能力池清单卡片
  const pool=(d.caps||[]).map(c=>{
    const rk=c.risk?('<span class="rb '+esc(c.risk_cls)+'">'+esc(c.risk)+'</span>'):'';
    const cls='cap-card'+(c.risk_cls?(' '+c.risk_cls):'')+(c.count===0?' zero':'');
    const hold=c.count?('<span class="muted" style="font-size:11px"> · '+c.holders.map(esc).join('、')+'</span>'):'<span class="muted" style="font-size:11px"> · 无持有者</span>';
    return '<div class="'+cls+'"><div class="cn">'+esc(c.name)+rk+'</div>'+
      '<div class="cd clampable">'+esc(c.desc)+'</div>'+
      '<div class="cf">默认给:'+esc(c.default_for||'-')+'</div>'+
      '<div class="ch">持有 <b>'+c.count+'</b>'+hold+'</div></div>';
  }).join('');
  morphInto(document.getElementById('capPool'), pool);
  // ② caps 矩阵
  const order=d.cap_order||[];
  const riskCls={}; (d.caps||[]).forEach(c=>{ if(c.risk_cls) riskCls[c.name]=c.risk_cls; });
  let head='<thead><tr><th>agent ＼ cap</th>'+order.map(n=>'<th class="'+(riskCls[n]||'')+'" title="'+esc(n)+'">'+esc(n)+'</th>').join('')+'<th>计</th></tr></thead>';
  const rowHtml=(r,ret)=>{
    const set=new Set(r.caps||[]);
    const cells=order.map(n=>set.has(n)?'<td class="yes">✓</td>':'<td class="no">·</td>').join('');
    const rcls='mx-role'+(ret?' ret':'')+(r.empty?'':'');
    const tail=ret&&r.succeeded_by?'<span class="muted" style="font-size:10.5px"> →'+esc(r.succeeded_by)+'</span>':'';
    return '<tr><td><span class="'+rcls+'">'+esc(r.role)+'</span>'+tail+(r.empty?' <span class="rb edge" title="caps 为空,待补">待补</span>':'')+'</td>'+cells+'<td class="cnt">'+(r.caps||[]).length+'</td></tr>';
  };
  let body='<tbody>'+(d.matrix||[]).map(r=>rowHtml(r,false)).join('');
  body+=(d.retired||[]).map(r=>rowHtml({role:r.role,caps:r.caps,succeeded_by:r.succeeded_by,empty:false},true)).join('')+'</tbody>';
  morphInto(document.getElementById('capMatrix'), head+body);
  document.getElementById('capMxFoot').textContent='✓=持有该能力 · 现行 '+(d.matrix||[]).length+' 个 agent · 退役 '+(d.retired||[]).length+' 个(存档 caps,不计入分布)';
  // ③ 能力分布(条形)
  const dist=(d.caps||[]).slice().sort((a,b)=>b.count-a.count);
  const maxc=Math.max(1,...dist.map(c=>c.count));
  morphInto(document.getElementById('capDist'), dist.map(c=>{
    const pct=Math.round(c.count/maxc*100);
    const fill=c.count===0?'background:var(--dead)':'';
    return '<div class="bar-row"><div class="lbl mono" title="'+esc(c.name)+'">'+esc(c.name)+'</div>'+
      '<div class="bar-track"><div class="bar-fill" style="width:'+(c.count?pct:3)+'%;'+fill+'"></div></div>'+
      '<div class="num">'+c.count+' 人</div></div>';
  }).join(''));
  // ⑤ 高风险能力持有者
  morphInto(document.getElementById('capRisk'), (d.high_risk_view||[]).map(v=>{
    const has=v.holders&&v.holders.length;
    const who=has?v.holders.map(h=>'<span class="cap-chip '+(v.cls==='hi'?'dead':'warn')+'">'+esc(h)+'</span>').join(''):'<span class="muted">无持有者(无人握此闸)</span>';
    return '<div style="margin-bottom:10px"><div class="mono" style="font-size:12.5px"><span class="rb '+esc(v.cls)+'">'+esc(v.level)+'</span> '+esc(v.name)+'</div>'+
      '<div style="margin-top:5px">'+who+'</div></div>';
  }).join('')+'<div class="muted" style="font-size:11px;margin-top:4px">⚠ 持有者为空=能力闸当前无人可执行;要写库/外发或操作外部云文档须先补授权。</div>');
  // ④ 缺口高亮
  const g=d.gaps||{};
  let gh='';
  const ec=g.empty_caps_agents||[];
  gh+='<div class="gap-box '+(ec.length?'todo':'ok')+'"><div class="gh">(a) 空 caps 的在岗 agent — 待补'+(ec.length?(' ('+ec.length+')'):'(无)')+'</div>'+
      (ec.length?ec.map(r=>'<span class="cap-chip warn">'+esc(r)+'</span>').join(''):'<span class="muted">现行 agent 均已配 caps ✓</span>')+
      (ec.length?'<div class="muted" style="font-size:11px;margin-top:4px">这些角色在册在岗但 agents.json 无 caps,培训师 onboarding 时按其专长补最小授权。</div>':'')+'</div>';
  const nh=g.no_holder_caps||[];
  gh+='<div class="gap-box '+(nh.length?'none':'ok')+'"><div class="gh">(b) 无人持有的能力 — 无持有者'+(nh.length?(' ('+nh.length+')'):'(无)')+'</div>'+
      (nh.length?nh.map(n=>'<span class="cap-chip dead">'+esc(n)+'</span>').join(''):'<span class="muted">每个能力都至少有一个持有者 ✓</span>')+'</div>';
  const tr=g.test_roles||[];
  gh+='<div class="gap-box"><div class="gh">(c) 已过滤的测试角色 ('+tr.length+')</div>'+
      (tr.length?tr.map(r=>'<span class="cap-chip">'+esc(r)+'</span>').join(''):'<span class="muted">无</span>')+
      '<div class="muted" style="font-size:11px;margin-top:4px">zz* / __* / ? 测试角色不计入分布与缺口(_is_test_role 过滤)。</div></div>';
  morphInto(document.getElementById('capGaps'), gh);
}
async function loadCaps(){
  try{ const r=await fetch('/api/caps'); renderCaps(await r.json()); }catch(e){}
}
document.getElementById('capsRefresh').onclick=()=>loadCaps();

// ===== 🧠 上下文窗口占用 · 1M 撞墙预警 =====
let _ctx=null;
function fmtWin(w){ return w>=1000000 ? (w/1000000)+'M' : (w/1000)+'K'; }
function marginCell(r){
  if(r.margin==null||r.compact_at==null) return '<span class="muted">—</span>';
  if(r.margin>0) return '<span class="ctxpct '+(r.level==='yellow'?'yellow':'green')+'">+'+fmtN(r.margin)+'</span> <span class="muted" style="font-size:11px">tok 余量</span>';
  return '<span class="ctxpct red">已超 '+fmtN(-r.margin)+'</span> <span class="muted" style="font-size:11px">tok·将被压</span>';
}
function renderCtx(){
  const d=_ctx; if(!d) return;
  const meta=document.getElementById('ctxMeta'); const kp=document.getElementById('ctxKpis');
  const body=document.getElementById('ctxBody'); const foot=document.getElementById('ctxFoot');
  if(!body) return;
  if(d.loading){ meta.textContent='后台扫描中…'; morphInto(body,'<tr><td colspan="6" class="muted">加载中（后台 jsonl 尾读）…</td></tr>'); return; }
  meta.textContent='活跃 '+d.n+' · 精确绑定 '+(d.n_bound||0)+'/'+d.n+' · Codex '+(d.n_codex||0)+' · 最近 '+d.active_min+'min · '+esc(d.as_of||'');
  morphInto(kp,'<div class="k"><div class="v ctxpct '+(d.max_source==='codex'?'info':(d.max_pct>=d.red?'red':d.max_pct>=d.yellow?'yellow':'green'))+'">'+d.max_pct+'%</div><div class="l">峰值占用</div></div>'
    +'<div class="k"><div class="v ctxpct '+(d.n_red>0?'red':'green')+'">'+d.n_red+'</div><div class="l">Claude ≥65% 自动压</div></div>'
    +'<div class="k"><div class="v ctxpct '+(d.n_yellow>0?'yellow':'green')+'">'+d.n_yellow+'</div><div class="l">Claude ≥50% 注意</div></div>'
    +'<div class="k"><div class="v">'+d.n+'</div><div class="l">活跃会话</div></div>');
  if(!d.rows||!d.rows.length){ morphInto(body,'<tr><td colspan="6" class="muted">（最近 '+d.active_min+' 分钟无活跃会话）</td></tr>'); foot.textContent=''; return; }
  morphInto(body, d.rows.map(r=>{
    const barW=Math.max(2,Math.min(100,r.pct))*0.6;
    let who;
    if(r.role) who='<b>'+esc(r.role)+'</b> <span style="color:var(--live)" title="'+(r.source==='codex'?'Codex host_session_id 精确绑定':'session_id 精确绑定')+'">✓</span>';
    else if(!r.is_agent) who='<span class="muted" title="'+esc(r.proj||'')+'">'+esc(r.proj?projShort(r.proj):'个人会话')+'</span>';
    else who='<span class="winassumed" title="who 无匹配 session_id:daemon(无此 env)/ 尚未 re-hello / 非注册会话 — 不猜">未绑定</span>';
    return '<tr class="'+(r.level==='red'?'ctx-red':'')+'" data-key="cx-'+esc(r.sid)+'">'
      +'<td><span class="ctxpct '+r.level+'">'+r.pct+'%</span><span class="ctxbar '+r.level+'" style="width:'+barW+'px"></span></td>'
      +'<td>'+marginCell(r)+'</td>'
      +'<td class="muted mono" style="font-size:11px">'+fmtN(r.ctx)+' / '+fmtWin(r.win)+(r.win_known?'':'·假定')+'</td>'
      +'<td>'+who+'</td>'
      +'<td class="mono" title="'+esc(r.model)+'">'+esc((r.model||'?').replace('claude-',''))+'</td>'
      +'<td class="muted mono">'+esc(r.ts||'')+'</td></tr>';
  }).join(''));
  foot.textContent='Claude 占用=(input+cache_read+cache_creation)/模型窗口；该来源实测自动压阈为窗口×65%。Codex 占用来自本机 rollout token_count，显示其上报窗口与当前 token；Codex 自动压缩阈值未从终端数据确认，因此不套用 65% 告警或余量。角色只按 Claude session_id / Codex host_session_id 精确绑定，不按路径猜。';
}
async function loadCtx(){
  try{ const r=await fetch('/api/ctxpanel'); _ctx=await r.json(); }catch(e){ /* 保留上次 */ }
  renderCtx(); renderCtxBadge();
}
function renderCtxBadge(){
  const b=document.getElementById('ctxBadge'); if(!b) return; const d=_ctx;
  if(!d || d.loading){ b.className='hbadge loading'; b.textContent='🧠 …'; return; }
  if(d.n_red>0){ b.className='hbadge danger'; b.textContent='🧠 ⚠ Claude '+d.n_red+' 将压'; b.title=d.n_red+' 个 Claude 会话 ≥65%；Codex 压缩阈值未确认'; }
  else if(d.n_yellow>0){ b.className='hbadge warn'; b.textContent='🧠 '+d.max_pct+'%'; b.title='峰值占用 '+d.max_pct+'%（有 Claude 会话 ≥50%）；Codex 压缩阈值未确认'; }
  else{ b.className='hbadge ok'; b.textContent='🧠 '+d.max_pct+'%'; b.title='峰值当前上下文占用 '+d.max_pct+'%; Codex 的自动压缩阈值未确认'; }
}
document.getElementById('ctxRefresh').onclick=()=>loadCtx();
document.getElementById('ctxBadge').onclick=()=>showTab('ctx');

// ⏱ wait 注册表观测(P3·2026-08-10):每 role 后台 wait 生命周期(morph 增量渲染)"""
