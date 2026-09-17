#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js_tabs —— page_js 拆分子模块 TABS(自我迭代波·案①·纯搬运字节一致·2026-09-16)。"""
TABS = r"""// ===== tab 分页 =====
const TAB_NAMES={fleet:'🛰 舰队/运维',guide:'📖 操作说明',bi:'📊 运营/资源','usage':'📈 历史用量',caps:'🧬 能力池',ctx:'🧠 上下文',waitreg:'⏱ wait 注册表',prompts:'📝 提示词审核',tasklines:'🎯 任务线看板',groups:'🧩 工作组',mechanisms:'🔧 机制',roles:'👤 角色',bizroles:'🏢 业务角色',longrange:'🗂️ 长程任务',agentprofile:'📇 数据画像',tasks:'📋 任务/协作',live:'🔴 实时',paths:'📁 路径',fleetlog:'📋 舰队日志',asr:'🎙 语音转写'};
let curTab='fleet'; try{ curTab=localStorage.getItem('busTab')||'fleet'; }catch(e){}
if(!TAB_NAMES[curTab]) curTab='fleet';
function showTab(t){
  if(!TAB_NAMES[t]) t='fleet';
  curTab=t; try{ localStorage.setItem('busTab',t); }catch(e){}
  document.querySelectorAll('#tabbar .tab').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
  document.querySelectorAll('main > [data-tab]').forEach(el=>el.classList.toggle('tabhide',el.getAttribute('data-tab')!==t));
  window.scrollTo({top:0});
  // 刷新门控(P1):只在进入对应 tab 时拉它的重数据 —— 不再无视可见性后台全刷(省 psutil 子进程+token)
  if(t==='bi'){ loadBI(); loadDispatch(); }
  else if(t==='usage') loadUsage(true);
  else if(t==='caps') loadCaps();
  else if(t==='ctx') loadCtx();
  else if(t==='waitreg') loadWaitreg();
  else if(t==='prompts'){ if(!_promptsLoaded) loadPrompts(); }
  else if(t==='tasklines') loadTasklines();
  else if(t==='groups') loadGroups();
  else if(t==='mechanisms') loadMechanisms();
  else if(t==='roles') loadRoles();
  else if(t==='bizroles') loadBizRoles();
  else if(t==='longrange') loadLongrange();
  else if(t==='fleet') load();
  else if(t==='guide') loadGuide();
  else if(t==='agentprofile') loadAgentProfile();
  else if(t==='paths'){ loadPaths(); loadStorage(); }
  else if(t==='fleetlog') loadFleetLog();
  else if(t==='asr') loadASR();
}
// 🙋 用户确认点/缺席面板(用户侧自醒对偶·MasterAgent 后端 ab user-absence-check·commit 36bd525)——谁在等用户拍板+用户离席多久
function _uaAge(m){ m=Math.round(m||0); if(m<60) return m+'min'; const h=Math.floor(m/60); if(h<24) return h+'h'; return Math.floor(h/24)+'d'; }
let _uaLast=0;
async function loadUserAbsence(force){
  const box=document.getElementById('uaPanel'); if(!box) return;
  const now=+new Date();
  if(!force && now-_uaLast<11000) return;   // 节流:子进程别太频
  _uaLast=now;
  // 加载态(正常态安静):仅首次未 populated 时显骨架·已有内容的刷新不闪(保留上次·隔离验证:不改 loaded 态)
  if(!box.querySelector('.ua-list')){ box.style.display=''; box.innerHTML='<div class="ua-head"><span class="ua-dot" style="background:var(--none);box-shadow:none"></span><span class="ua-sub">加载确认点…</span></div>'; }
  try{
    const d=await (await fetch('/api/user-absence')).json();
    if(!d || !d.ok){ if(!box.querySelector('.ua-list')){ box.style.display=''; box.innerHTML='<div class="ua-head away"><span class="ua-dot"></span><span class="ua-sub">⚠ 确认点数据拉取失败'+((d&&d.error)?'('+esc(d.error)+')':'')+'·将自动重试</span></div>'; } return; }
    const cps=d.checkpoints||[];
    const present=!!d.user_present, idle=Math.round(d.user_idle_min||0);
    const hasEsc=cps.some(c=>c.state==='escalate');
    let head='<div class="ua-head'+(present?'':' away')+(hasEsc?' alert':'')+'">';
    head+='<span class="ua-dot"></span>';
    head+=present?('<b>用户在场</b> <span class="ua-sub">present · idle '+idle+'min</span>')
                :('<b>用户离席</b> <span class="ua-sub">away · idle '+idle+'min</span>');
    if(d.night) head+=' <span class="ua-night">🌙 夜间</span>';
    // 计数两栏(Master数分口径·某栏0则隐藏=正常态安静·不显'…0'噪音;两栏皆0显'✓无待确认')
    const nDec=(d.pending_decision!=null?d.pending_decision:cps.filter(c=>c.kind!=='fyi').length);
    const nFyi=(d.pending_fyi!=null?d.pending_fyi:cps.filter(c=>c.kind==='fyi').length);
    const _seg=[]; if(nDec>0) _seg.push('🗳️待你决策 '+nDec); if(nFyi>0) _seg.push('📄待你知悉 '+nFyi);
    head+='<span class="ua-count">'+(_seg.length?_seg.join(' · '):'✓ 无待确认')+'</span></div>';
    const ICON={escalate:'🔴',nudge:'🟡',fresh:'·',expireable:'⏰'}, KICON={decision:'🗳️',fyi:'📄'};
    // 排序:escalate>high-urgency>nudge>fresh 再 age desc(Master数分#2:让 away 时高优浮顶·别把今天高优埋底)
    const _uaRank=(c)=>c.state==='escalate'?0:(c.urgency==='high'?1:((c.state==='nudge'||c.state==='expireable')?2:3));
    let rows;
    if(!cps.length){ rows='<div class="ua-empty">✓ 无待用户确认点</div>'; }
    else{
      const sorted=cps.slice().sort((a,b)=>_uaRank(a)-_uaRank(b)||(b.age_min-a.age_min));
      rows=sorted.map(c=>{
        const hi=c.urgency==='high'; const id=esc(c.id||''); const fyi=c.kind==='fyi';
        const act=fyi
          ? '<button class="ua-ack-btn" title="知悉并批关(轻量ack·路由回提问方)" onclick="uaAck(event,\''+id+'\')">✓知悉</button>'
          : '<button class="ua-ans-btn" onclick="uaAnsToggle(event,\''+id+'\')">✍答复</button>'
            +'<div class="ua-ans" id="uaans-'+id+'" style="display:none"><input class="ua-ans-in" id="uaansin-'+id+'" placeholder="答复该确认点(回车发送)…" onkeydown="if(event.key===\'Enter\')uaAnsSend(\''+id+'\')"><button class="ua-ans-send" onclick="uaAnsSend(\''+id+'\')">发送</button></div>';
        return '<div class="ua-cp st-'+esc(c.state||'')+' k-'+esc(c.kind||'')+(hi?' hi':'')+'">'
          +'<span class="ua-ico">'+(ICON[c.state]||'·')+'</span>'
          +'<span class="ua-kind" title="'+(fyi?'待你知悉(结论·轻量ack)':'待你决策(需拍板)')+'">'+(KICON[c.kind]||'')+'</span>'
          +'<span class="ua-cp-title" title="点击去待办池" onclick="showTab(\'tasks\')">'+esc(c.title||'(无标题)')+'</span>'
          +'<span class="ua-cp-meta">'+(hi?'<span class="ua-urg">高</span>':'')+esc(c.from||'')+' · '+_uaAge(c.age_min)+'</span>'
          +act
          +'</div>';
      }).join('');
    }
    box.innerHTML=head+'<div class="ua-list">'+rows+'</div>';
    box.style.display='';
  }catch(e){ if(!box.querySelector('.ua-list')){ box.style.display=''; box.innerHTML='<div class="ua-head away"><span class="ua-dot"></span><span class="ua-sub">⚠ 确认点面板请求异常·将自动重试</span></div>'; } /* 有上次内容则保留不 blank */ }
}
// 内联答复确认点(MasterAgent·P1·复用 /api/todos/answer→路由回提问方 inbox+标待办完成+盖 user_last_seen=答了即在场)——用户侧闭环:看到+答一步到位不用跳
function uaAnsToggle(e,id){ e.stopPropagation(); const el=document.getElementById('uaans-'+id); if(!el) return; const show=el.style.display==='none'; el.style.display=show?'':'none'; if(show){ const inp=document.getElementById('uaansin-'+id); if(inp) inp.focus(); } }
async function uaAnsSend(id){
  const inp=document.getElementById('uaansin-'+id); if(!inp) return;
  const reply=(inp.value||'').trim(); if(!reply){ inp.focus(); return; }
  try{
    const r=await (await fetch('/api/todos/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,reply:reply})})).json();
    if(r && r.ok){ toast('已答复确认点·路由回提问方'); loadUserAbsence(true); load(); }
    else toast('答复失败:'+((r&&r.error)||'?'));
  }catch(e){ toast('答复异常:'+e); }
}
// fyi类(待你知悉结论)一键知悉批关(Master数分口径·轻量ack·区别decision的显式答复框)——复用 answer 端点 reply=已知悉·路由回提问方+批关
async function uaAck(e,id){ e.stopPropagation();
  try{
    const r=await (await fetch('/api/todos/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,reply:'已知悉'})})).json();
    if(r && r.ok){ toast('已知悉·批关该结论'); loadUserAbsence(true); load(); }
    else toast('批关失败:'+((r&&r.error)||'?'));
  }catch(e){ toast('批关异常:'+e); }
}
// 📖 操作说明 —— 渲染 repo 根 BUS_GUIDE.md(单一源·内容 MasterAgent 维护·MasterUI 只渲染)。左目录右正文·锚点跳转·静态文档加载一次
let _guideLoaded=false;
async function loadGuide(force){
  const body=document.getElementById('guideBody'), toc=document.getElementById('guideToc'), meta=document.getElementById('guideMeta');
  if(!body) return;
  if(_guideLoaded && !force) return;
  try{
    const d=await (await fetch('/api/guide')).json();
    if(!d.ok){ body.innerHTML='<div class="muted">加载失败:'+esc(d.error||'?')+'</div>'; return; }
    body.innerHTML=d.html||'';
    if(toc){
      toc.innerHTML=d.toc||'<div class="muted">(无目录)</div>';
      toc.querySelectorAll('a[href^="#"]').forEach(a=>{
        a.onclick=(e)=>{ e.preventDefault();
          const el=document.getElementById(a.getAttribute('href').slice(1));
          if(el){ el.scrollIntoView({behavior:'smooth',block:'start'});
            toc.querySelectorAll('a').forEach(x=>x.classList.remove('active')); a.classList.add('active'); } };
      });
    }
    if(meta){ let s='源 BUS_GUIDE.md';
      if(d.mtime){ try{ s+=' · 更新 '+new Date(d.mtime*1000).toLocaleString(); }catch(e){} }
      if(d.degraded) s+=' · ⚠降级渲染(markdown 库缺失)';
      meta.textContent=s; }
    _guideLoaded=true;
  }catch(e){ body.innerHTML='<div class="muted">请求异常:'+esc(''+e)+'</div>'; }
}
const _guideRefreshBtn=document.getElementById('guideRefresh');
if(_guideRefreshBtn) _guideRefreshBtn.onclick=()=>loadGuide(true);
// 📇 per-agent 数据画像(L3·MasterUI 接·2026-09-17)——中性自我发现工具·非KPI/非排名(Master数分红线)
// A 自省非排行:单agent drill-in(左选择器仅名字·右单卡)·无并排横比grid·无极值高亮·无排序
// B 原始计数不跨agent比:掉线/回执旁带活跃度caveat  C 无阈值/target/基准线  D 窗口+每指标定义透明
let _apData=null, _apSel=null;
function _apN(v){ return (v==null)?'—':(typeof v==='number'?v.toLocaleString():esc(''+v)); }
function _apRow(label,def,val){ return '<div class="ap-row"><span class="ap-rk">'+esc(label)+'<span class="ap-def">'+esc(def)+'</span></span><span class="ap-rv">'+val+'</span></div>'; }
async function loadAgentProfile(force){
  const list=document.getElementById('apList'); if(!list) return;
  const days=(document.getElementById('apDays')||{}).value||'30';
  try{
    const d=await (await fetch('/api/agent-profile?days='+days)).json();
    if(!d||!d.ok){ list.innerHTML='<div class="muted">加载失败:'+esc((d&&d.error)||'?')+'</div>'; return; }
    _apData=d; apRender();
    const meta=document.getElementById('apMeta'); if(meta) meta.textContent='as_of '+esc((d.as_of||'').replace('T',' '));
  }catch(e){ list.innerHTML='<div class="muted">请求异常:'+esc(''+e)+'</div>'; }
}
function apPick(role){ _apSel=role; apRender(); }
function apRender(){
  const d=_apData; if(!d) return;
  const fr=document.getElementById('apFraming');
  if(fr) fr.innerHTML='🔍 <b>自我发现工具</b> · 非考核 / 非排名 · 硬指标只读旁路 · 数字自证不评判。<span class="ap-win">近 '+esc(''+d.window_days)+' 天窗</span>';
  // 左列:后端稳定序(roster口径)·仅名字无内联metric(防从列表横比)·未归属独立首项不摊派
  const list=document.getElementById('apList');
  let items='<div class="ap-item ap-unattr'+(_apSel==='__unattr__'?' on':'')+'" onclick="apPick(\'__unattr__\')">📦 未归属 usage<span class="ap-hint">不摊派</span></div>';
  items+=(d.agents||[]).map(a=>'<div class="ap-item'+(_apSel===a.role?' on':'')+'" onclick="apPick('+JSON.stringify(a.role)+')">'+esc(a.role)+'</div>').join('');
  list.innerHTML=items;
  // 右:单卡 drill-in
  const card=document.getElementById('apCard');
  if(!_apSel){ card.innerHTML='<div class="muted" style="padding:22px">← 选一个角色看它的画像(单卡自省 · 非横向对比)</div>'; return; }
  if(_apSel==='__unattr__'){
    const u=d.unattributed_usage||{};
    card.innerHTML='<div class="ap-cardh">📦 未归属 usage <span class="ap-badge">不摊派各 agent</span> <span class="ap-win">近 '+esc(''+d.window_days)+' 天</span></div>'
      +'<div class="ap-note">无归属会话的用量(不分派到任一 role·避免虚增个别 agent)。</div>'
      +'<div class="ap-grp">产出</div>'
      +_apRow('calls','usage 调用数',_apN(u.calls))
      +_apRow('output_tokens','输出 token',_apN(u.output))
      +_apRow('input_tokens','输入 token',_apN(u.input))
      +_apRow('cache_read','缓存读 token',_apN(u.cache_read));
    return;
  }
  const a=(d.agents||[]).find(x=>x.role===_apSel);
  if(!a){ card.innerHTML='<div class="muted">无此角色</div>'; return; }
  const out=a['维度4_产出'], disc=a['维度2_纪律']||{};
  let h='<div class="ap-cardh">'+esc(a.role)+' <span class="ap-win">近 '+esc(''+(a.window_days||d.window_days))+' 天窗</span></div>';
  h+='<div class="ap-grp">产出 <span class="ap-gdef">硬指标 · 无目标值 · 数字自证</span></div>';
  if(typeof out==='string'){ h+='<div class="ap-nodata">'+esc(out)+'</div>'; }
  else if(out){ h+=_apRow('calls','usage 调用数',_apN(out.calls))
      +_apRow('output_tokens','输出 token',_apN(out.output_tokens))
      +_apRow('input_tokens','输入 token',_apN(out.input_tokens))
      +_apRow('cache_read','缓存读 token',_apN(out.cache_read)); }
  else { h+='<div class="ap-nodata">无数据</div>'; }
  h+='<div class="ap-grp">纪律 <span class="ap-gdef">原始计数 · 随活跃度而变 · 不可跨 agent 直接比</span></div>';
  h+=_apRow('done','完成回填数',_apN(disc.done))
    +_apRow('claim','领取数',_apN(disc.claim))
    +_apRow('send','发送数',_apN(disc.send))
    +_apRow('掉线催单','guard 催重挂数(随活跃度)',_apN(disc['掉线催单']))
    +_apRow('stop_hook_block','stop-hook 拦截数',_apN(disc.stop_hook_block))
    +_apRow('回执时延·中位','notice created→done 秒',_apN(disc['回执时延中位秒']))
    +_apRow('回执时延·P90','notice created→done 秒',_apN(disc['回执时延P90秒']))
    +_apRow('回执样本数','参与时延统计的回执数',_apN(disc['回执样本数']));
  h+='<div class="ap-caveat">⚠ 原始计数无法跨 agent 直接比较——活跃度不同(如 calls 多的天然掉线机会多);此处仅供该角色自我观察趋势·非评价。</div>';
  card.innerHTML=h;
}
const _apRefreshBtn=document.getElementById('apRefresh'); if(_apRefreshBtn) _apRefreshBtn.onclick=()=>loadAgentProfile(true);
const _apDaysSel=document.getElementById('apDays'); if(_apDaysSel) _apDaysSel.onchange=()=>loadAgentProfile(true);
// 🎙 语音转写(本地 ASR·实时)——设备选择 + 启停 + 转录行轮询滚动
let _asrTimer=null, _asrDevInit=false;
async function loadASR(){
  if(!_asrDevInit){ await asrLoadDevices(); _asrDevInit=true; }
  await asrPollStatus();
  asrStartPolling();
}
async function asrLoadDevices(){
  const box=document.getElementById('asrDeviceBox'); if(!box) return;
  box.innerHTML='<div class="muted">读取设备…</div>';
  try{
    const d=await (await fetch('/api/asr/devices')).json();
    if(!d.ok){ box.innerHTML='<div class="muted">设备读取失败:'+esc(d.error||'?')+'</div>'; return; }
    const models=(d.models||[]).map(m=>'<option value="'+esc(m)+'"'+(m===d.default_model?' selected':'')+'>'+esc(m)+'</option>').join('')||'<option value="">(无模型)</option>';
    const spk=(d.speakers||[]).map(s=>esc(s.name)).join('、')||'(无)';
    const mics=(d.mics||[]).map(m=>'<option value="'+esc(m.name)+'">'+esc(m.name)+'</option>').join('');
    box.innerHTML=
      '<div style="display:flex;gap:18px;flex-wrap:wrap;align-items:center">'+
        '<label>音频源:<select id="asrSource">'+
          '<option value="loopback" selected>🔊 系统声音(线上会议对方)</option>'+
          '<option value="mic">🎤 麦克风(现场/自己说话)</option>'+
        '</select></label>'+
        '<label>模型:<select id="asrModel">'+models+'</select></label>'+
        '<span class="muted" id="asrSrcHint" style="font-size:11.5px">系统声=默认扬声器回环:'+esc(spk)+'</span>'+
      '</div>'+
      '<div id="asrMicWrap" style="display:none;margin-top:6px"><label>麦克风:<select id="asrMic">'+mics+'</select></label></div>';
    const ss=document.getElementById('asrSource');
    ss.onchange=()=>{ document.getElementById('asrMicWrap').style.display=ss.value==='mic'?'':'none'; };
  }catch(e){ box.innerHTML='<div class="muted">设备读取异常:'+esc(e)+'</div>'; }
}
async function asrPollStatus(){
  try{
    const d=await (await fetch('/api/asr/status')).json();
    const running=!!d.running, det=d.detail||{}, sess=d.session||{};
    const sB=document.getElementById('asrStartBtn'), pB=document.getElementById('asrStopBtn'), st=document.getElementById('asrStatus');
    if(sB) sB.style.display=running?'none':'';
    if(pB) pB.style.display=running?'':'none';
    if(st){
      if(running){ st.textContent='● 转写中 · '+(det.device_name||sess.source||'')+' · 模型 '+(sess.model||''); st.style.color='#3ecf8e'; }
      else if(det.state==='error'){ st.textContent='⚠ '+(det.msg||'出错'); st.style.color='var(--warn)'; }
      else { st.textContent='就绪'; st.style.color=''; }
    }
    await asrLoadLines(running);
  }catch(e){}
}
async function asrLoadLines(running){
  const box=document.getElementById('asrLinesBox'); if(!box) return;
  try{
    const d=await (await fetch('/api/asr/lines?since=0')).json();
    if(!d.ok) return;
    if(!d.lines||!d.lines.length){ box.innerHTML='<div class="muted">'+(running?'监听中…开口说话即出字':'尚无转写。选好音频源点“启动转写”')+'</div>'; return; }
    const atBottom = box.scrollTop+box.clientHeight >= box.scrollHeight-30;
    box.innerHTML=d.lines.map(l=>{
      const t=new Date((l.ts||0)*1000).toLocaleTimeString();
      const play=l.audio?('<button class="btn ghost mini" style="flex:0 0 auto;font-size:11px;padding:0 6px" title="回放原声" onclick="asrPlay(this,\''+esc(l.audio)+'\')">▶</button>'):'';
      return '<div style="padding:5px 8px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center">'+play+'<span class="muted mono" style="font-size:11px;flex:0 0 auto">'+t+'</span><span style="flex:1">'+esc(l.text||'')+'</span></div>';
    }).join('');
    if(atBottom) box.scrollTop=box.scrollHeight;
  }catch(e){}
}
function asrStartPolling(){ if(_asrTimer) return; _asrTimer=setInterval(()=>{ if(curTab==='asr') asrPollStatus(); else { clearInterval(_asrTimer); _asrTimer=null; } },1500); }
let _asrAudio=null;
function asrPlay(btn,rel){
  try{ if(_asrAudio){ _asrAudio.pause(); _asrAudio=null; } }catch(e){}
  _asrAudio=new Audio('/api/asr/audio?f='+encodeURIComponent(rel));
  _asrAudio.play().catch(e=>toast('回放失败:'+e,true));
  if(btn){ btn.textContent='♪'; _asrAudio.onended=()=>{ btn.textContent='▶'; }; }
}
document.getElementById('asrRefresh').onclick=()=>{ _asrDevInit=false; loadASR(); };
document.getElementById('asrStartBtn').onclick=async()=>{
  const source=(document.getElementById('asrSource')||{}).value||'loopback';
  const model=(document.getElementById('asrModel')||{}).value||'';
  const name=source==='mic'?((document.getElementById('asrMic')||{}).value||''):'';
  try{
    const r=await (await fetch('/api/asr/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source,model,name})})).json();
    if(r.ok){ toast('✅ 转写已启动'); asrPollStatus(); asrStartPolling(); }
    else toast('启动失败:'+(r.error||'?'),true);
  }catch(e){ toast('启动异常:'+e,true); }
};
document.getElementById('asrStopBtn').onclick=async()=>{
  try{
    const r=await (await fetch('/api/asr/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
    if(r.ok) toast('✅ 已停止'); else toast('停止失败:'+(r.error||'?'),true);
    asrPollStatus();
  }catch(e){ toast('停止异常:'+e,true); }
};
// 🎯 长期任务线看板:卡片 + ⧗待决策徽标 + 展开 current/deps/decisions(待用户决策红/黄高亮)
async function loadTasklines(){
  const box=document.getElementById('tlBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/tasklines'); renderTasklines(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderTasklines(d){
  const box=document.getElementById('tlBox'); const meta=document.getElementById('tlMeta'); if(!box) return;
  if(!d||!d.ok){ box.innerHTML='<div class="muted" style="padding:8px 16px">tasklines 读取失败:'+esc((d&&d.error)||'?')+'</div>'; if(meta)meta.textContent=''; return; }
  const tls=d.tasklines||[];
  if(meta) meta.textContent='刷新 '+esc(d.now||'')+' · '+(d.count||0)+' 条任务线 · ⧗待决策 '+(d.pending_total||0)+' · 数据更新 '+esc(d.updated||'');
  morphInto(box, tls.map(t=>{
    const pend=t.pending_decisions||0, tot=t.total_decisions||0;
    const badge = tot? ('<span class="cap-chip" style="color:var(--'+(pend?'warn':'live')+')" title="待用户决策/总决策">'+(pend?'⧗待决策 '+pend+'/'+tot:'✓决策全清 '+tot)+'</span>') : '';
    const decRows=(t.decisions||[]).map(de=>{
      const cls=de.answered?'live':'warn';
      const tag=de.answered?'✓已答':'⧗待你拍板';
      return '<div class="tl-dec" style="border-left:3px solid var(--'+cls+');padding:4px 8px;margin:4px 0;background:rgba(255,255,255,.02)">'
        +'<span class="cap-chip" style="color:var(--'+cls+')">'+tag+'</span> '
        +'<span style="font-size:12px">'+esc(de.q)+'</span> '
        +'<span class="muted" style="font-size:11px">· 待办池 ref='+esc(de.ref||'?')+' status='+esc(de.status)+'</span></div>';
    }).join('')||'<div class="muted" style="font-size:11.5px;padding:2px 8px">无决策点</div>';
    const deps=(t.deps||[]).length? ('<div style="font-size:11.5px;margin-top:4px"><b class="muted">依赖:</b> '+(t.deps.map(x=>'<span class="cap-chip">'+esc(typeof x==="string"?x:(x.title||x.id||JSON.stringify(x)))+'</span>').join(''))+'</div>') : '';
    return '<div class="tl-card" data-key="tl-'+esc(t.id)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px;'+(pend?'border-left:3px solid var(--warn)':'')+'">'
      +'<div class="tl-head" style="cursor:pointer;display:flex;gap:10px;align-items:center;flex-wrap:wrap" onclick="var b=this.parentNode.querySelector(\'.tl-body\');b.style.display=b.style.display===\'none\'?\'block\':\'none\';">'
      +'<b style="color:var(--accent)">'+esc(t.id)+'</b> <b>'+esc(t.title)+'</b> '+badge
      +'<span class="muted" style="font-size:11.5px">owner '+esc(t.owner)+' · '+esc(t.status)+'</span>'
      +'<span class="spacer" style="flex:1"></span><span class="muted" style="font-size:11px">▸ '+esc(t.cadence||'')+' · 更新 '+esc(t.updated||'')+'</span></div>'
      +'<div class="tl-body" style="display:none;margin-top:8px">'
      +(t.current?'<div style="font-size:12.5px;margin-bottom:4px"><b class="muted">当前:</b> '+esc(t.current)+'</div>':'')
      +deps
      +'<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">决策点(交叉待办池):</b>'+decRows+'</div>'
      +(t.truth_source?'<div class="muted" style="font-size:11px;margin-top:6px">truth_source: '+esc(t.truth_source)+'</div>':'')
      +'</div></div>';
  }).join('')||'<div class="muted" style="padding:8px 16px">无任务线</div>');
}"""
