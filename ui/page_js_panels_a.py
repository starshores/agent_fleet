#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js_panels_a —— page_js 拆分子模块 PANELS_A(自我迭代波·案①·纯搬运字节一致·2026-09-16)。"""
PANELS_A = r"""let _wr=null;
function renderWaitreg(){
  const d=_wr; const body=document.getElementById('wrBody'); if(!body) return;
  const meta=document.getElementById('wrMeta'), foot=document.getElementById('wrFoot');
  if(!d || d.error){ morphInto(body,'<tr><td colspan="7" class="muted">读取失败:'+esc(d&&d.error||'?')+'</td></tr>'); return; }
  if(meta) meta.textContent='更新 '+esc(d.as_of||'');
  const rows=d.rows||[];
  if(!rows.length){ morphInto(body,'<tr><td colspan="7" class="muted" style="padding:8px">暂无在册 wait(reg 为空)</td></tr>'); if(foot)foot.textContent=''; return; }
  let orphans=0;
  const html=rows.map(r=>{
    const host = r.host_claude_pid!=null
      ? esc(r.host_claude_pid)+(r.host_alive===false?' <span class="badge dead">✗死</span>':(r.host_alive?' <span class="badge live">✓</span>':''))
      : '<span class="muted">–</span>';
    let stat;
    if(r.orphan){ orphans++; stat='<span class="badge dead">⚠ 孤儿(宿主死·P1将杀)</span>'; }
    else if(!r.wait_alive){ stat='<span class="badge warn">wait 进程已退(陈旧 reg)</span>'; }
    else{ stat='<span class="badge live">✓ '+esc(r.status||'listening')+'</span>'; }
    const tmo = r.timeout==null?'∞(block)':(esc(r.timeout)+'s');
    return `<tr data-key="wr-${esc(r.role)}">
      <td><b>${esc(r.role)}</b></td>
      <td class="mono">${esc(r.wait_pid!=null?r.wait_pid:'–')}</td>
      <td class="mono">${host}</td>
      <td class="mono" title="接管代数(每次 takeover +1)">${r.generation!=null?esc(r.generation):'–'}</td>
      <td class="mono" title="timeout=${tmo}">${r.age_s!=null?fmtAge(r.age_s):'–'}</td>
      <td class="mono">${r.hb_age_s!=null?fmtAge(r.hb_age_s):'–'}</td>
      <td>${stat}</td></tr>`;
  }).join('');
  morphInto(body,html);
  if(foot) foot.textContent='共 '+rows.length+' 个在册 wait'+(orphans?(' · ⚠ '+orphans+' 孤儿(宿主已死·P1 自检将自杀)'):'');
}
async function loadWaitreg(){
  try{ const r=await fetch('/api/waitreg'); _wr=await r.json(); }catch(e){ /* 保留上次 */ }
  renderWaitreg();
}
document.getElementById('wrRefresh').onclick=()=>loadWaitreg();

// ===== 📝 提示词审核(只读·手动刷新)=====
let _promptsLoaded=false, _promptsData=null;
const openPrompts=new Set();   // 展开的提示词节序号(状态驱动·morph 刷新不丢)
function renderPrompts(d){
  const box=document.getElementById('promptsBox'); const meta=document.getElementById('promptsMeta');
  if(!box) return;
  if(!d||!d.ok){ box.innerHTML='<div class="muted">读取失败:'+esc((d&&d.error)||'?')+'</div>'; return; }
  _promptsData=d;
  meta.textContent='共 '+d.sections.length+' 节 · 读取于 '+esc(d.as_of||'');
  morphInto(box, d.sections.map((s,i)=>{
    const open=openPrompts.has(i);
    // 📂开文件按钮:仅【真实文件 path】section(server 标 openable);白名单在 server 端二次校验
    const openBtn = s.openable
      ? '<button class="btn ghost mini pmt-open" onclick="event.stopPropagation();openPromptFile('+i+')" title="在本机默认程序打开该文件">📂开文件</button>'
      : '';
    const head='<div class="pmt-h" onclick="togglePrompt('+i+')"><span class="pmt-t">'+esc(s.title)+'</span>'
      +'<span class="pmt-meta mono">'+esc(s.path||'')+(s.mtime?(' · '+esc(s.mtime)):'')
      +(s.bytes!=null?(' · '+fmtN(s.bytes)+'B/'+ (s.lines||0) +'行'):'')+'</span>'+openBtn+'</div>';
    let body;
    if(s.error){
      body='<div class="pmt-b muted" id="pmt-'+i+'" style="display:'+(open?'block':'none')+'">⚠ '+esc(s.error)+'</div>';
    }else{
      // 行号 gutter:每逻辑行一 flex 行(行号顶对齐·内容 wrap 不打乱行号)
      const lines=(s.content||'').split('\n');
      const rows=lines.map((ln,n)=>'<div class="pln"><span class="lno">'+(n+1)+'</span><span class="lco">'+esc(ln)+'</span></div>').join('');
      body='<div class="pmt-code" id="pmt-'+i+'" style="display:'+(open?'block':'none')+'">'+rows+'</div>';
    }
    return '<div class="pmt-sec'+(s.error?' err':'')+'" data-key="pmt-sec-'+i+'">'+head+body+'</div>';
  }).join('')||'<div class="muted">无内容</div>');
}
function togglePrompt(i){ if(openPrompts.has(i)) openPrompts.delete(i); else openPrompts.add(i); if(_promptsData) renderPrompts(_promptsData); }
function openPromptFile(i){
  const s=_promptsData&&_promptsData.sections&&_promptsData.sections[i];
  if(!s||!s.path){ toast('无有效 path',1); return; }
  fetch('/api/open-file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:s.path})})
    .then(r=>r.json()).then(d=>{ toast(d&&d.ok?('已在默认程序打开:'+esc(s.title||'')):('打开失败:'+esc((d&&d.error)||'?')), d&&d.ok?0:1); })
    .catch(e=>toast('打开失败:'+esc(e),1));
}
async function loadPrompts(){
  const box=document.getElementById('promptsBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted">读取中…</div>';
  try{ const r=await fetch('/api/prompts'); renderPrompts(await r.json()); _promptsLoaded=true; }
  catch(e){ if(box) box.innerHTML='<div class="muted">读取失败:'+esc(e)+'</div>'; }
}
document.getElementById('promptsRefresh').onclick=()=>loadPrompts();

// ===== 可恢复会话 =====
let _sessData=null, _sessFilter='all', _sessOwner='all', _sessSort='recent';
function renderSessions(){
  const box=document.getElementById('sessBox'); if(!_sessData) return;
  if(!_sessData.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc(_sessData.error||'?')+'</div>'); return; }
  let list=_sessData.sessions.slice();
  // 归属筛选(is_bus 字段:bus agent会话 / 个人会话)
  if(_sessOwner==='bus')    list=list.filter(s=>s.is_bus);
  else if(_sessOwner==='nonbus') list=list.filter(s=>!s.is_bus);
  // 状态/标记筛选(--json 已按 运行中置顶再最近 排序,保持原序)· 与归属可组合
  if(_sessFilter==='live')   list=list.filter(s=>s.state==='运行中');
  else if(_sessFilter==='dead')   list=list.filter(s=>s.state==='死');
  else if(_sessFilter==='tagged') list=list.filter(s=>s.tags&&s.tags.length);
  // 排序:size=按 jsonl 大小降序(surfacing 最吃上下文/最该清的);recent=保持 --json 原序(运行中置顶再最近)
  if(_sessSort==='size') list.sort((a,b)=>(+b.size_kb||0)-(+a.size_kb||0));
  const oname={all:'全部',bus:'bus',nonbus:'个人'}[_sessOwner]||'全部';
  const fname={all:'全部',live:'运行中',dead:'死',tagged:'已标记'}[_sessFilter]||'全部';
  document.getElementById('sessCount').textContent=
    '显示 '+list.length+' 条(归属:'+oname+' · 状态:'+fname+')· bus '+(_sessData.bus_count||0)
    +' / 个人 '+(_sessData.nonbus_count||0)+' · ▶运行中 '+(_sessData.live_count||0)
    +' / ·死 '+(_sessData.dead_count||0)+' / 🏷已标记 '+(_sessData.tagged_count||0)
    +' · 共 '+_sessData.total;
  if(!list.length){ morphInto(box,'<div class="muted" style="padding:8px 16px">（当前筛选无会话）</div>'); return; }
  morphInto(box, list.map(s=>{
    const live = s.state==='运行中';
    const st = live
      ? `<span class="sess-state live">▶ 运行中<span class="role">·${esc(s.live_role||'?')}</span></span>`
      : `<span class="sess-state dead">· 死</span>`;
    const own = s.is_bus
      ? `<span class="own-chip bus">bus</span>`
      : `<span class="own-chip personal">个人</span>`;
    const tags = (s.tags&&s.tags.length)
      ? `<span class="sess-tags">`+s.tags.map(t=>`<span class="tag-chip">🏷${esc(t)}</span>`).join('')+`</span>` : '';
    // 死的才给 resume;运行中的它在跑,不给(点了会撞正在跑的会话)
    const act = live
      ? `<span class="badge live" title="该会话正在运行,勿 resume">在跑</span>`
      : `<button class="btn mini copy" onclick="copyResume('${esc(s.id)}',this)">复制 resume</button>`;
    // 属于角色(Master数分四态·不伪造未知):✓=权威(presence绑定/hello账本/归档manifest),~=标题推断非权威,—=无从判不猜
    const srcTip={bound:'presence 实时绑定·权威',ledger:'hello 账本 session_role_map·权威',archive:'session-archive manifest·权威',inferred:'标题启发推断·非权威(标题提到≠属于)',unknown:'无可靠归属源·不猜'};
    const roleChip = s.role
      ? `<span class="role-chip src-${esc(s.role_src)}" title="${srcTip[s.role_src]||''}">${s.role_src==='inferred'?'~':'✓'}${esc(s.role)}</span>`
      : `<span class="role-chip src-unknown" title="${srcTip.unknown}">—未知</span>`;
    return `<div class="sess-row ${live?'live':'dead'} ${s.is_agent?'agent':''}" data-key="ss-${esc(s.id)}">
      <div class="sess-main">
        <div class="sess-title" title="${esc(s.title)}">${st} ${own} ${esc(s.title||'(无标题)')} ${tags}</div>
        <div class="sess-meta">
          <span title="${esc(s.cwd)}">${esc(projShort(s.project||s.cwd))}</span>
          <span>最近 ${esc(s.last)}</span>
          ${s.size_kb!=null?szChip(s.size_kb):''}
          ${s.archived?'<span title="jsonl 已移出 projects/">📦已归档</span>':''}
          ${s.note?`<span class="dnote" title="${esc(s.note)}">📝${esc(s.note)}</span>`:''}
          <span class="mono">${esc((s.id||'').slice(0,8))}…</span>
        </div>
      </div>
      <div class="sess-side">${roleChip}${act}</div>
    </div>`;
  }).join(''));
}
function setSessFilter(f){
  _sessFilter=f;
  document.querySelectorAll('.sess-filt').forEach(b=>b.classList.toggle('active', b.getAttribute('data-f')===f));
  renderSessions();
}
function setSessOwner(o){
  _sessOwner=o;
  document.querySelectorAll('.sess-own').forEach(b=>b.classList.toggle('active', b.getAttribute('data-o')===o));
  renderSessions();
}
function setSessSort(s){
  _sessSort=s;
  document.querySelectorAll('.sess-sort').forEach(b=>b.classList.toggle('active', b.getAttribute('data-s')===s));
  renderSessions();
}
function copyResume(id,btn){
  const cmd='claude --resume '+id;
  const done=()=>{const o=btn.textContent;btn.textContent='已复制✓';setTimeout(()=>btn.textContent=o,1500);toast('已复制:'+cmd);};
  if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(cmd).then(done,()=>fallback()); }
  else fallback();
  function fallback(){const ta=document.createElement('textarea');ta.value=cmd;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');done();}catch(e){toast('复制失败,手动复制:'+cmd,true);}document.body.removeChild(ta);}
}
async function loadSessions(){
  const box=document.getElementById('sessBox'); if(!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">加载中…</div>';
  try{ const r=await fetch('/api/sessions'); _sessData=await r.json(); renderSessions(); }
  catch(e){ box.innerHTML='<div class="muted" style="padding:8px 16px">加载失败:'+esc(e)+'</div>'; }
}
document.getElementById('usageRefresh').onclick=()=>loadUsage(true);
document.getElementById('sessRefresh').onclick=()=>loadSessions();
{const _tlr=document.getElementById('tlRefresh'); if(_tlr) _tlr.onclick=()=>loadTasklines();}
document.querySelectorAll('.sess-filt').forEach(b=>b.onclick=()=>setSessFilter(b.getAttribute('data-f')));
document.querySelectorAll('.sess-own').forEach(b=>b.onclick=()=>setSessOwner(b.getAttribute('data-o')));
document.querySelectorAll('.sess-sort').forEach(b=>b.onclick=()=>setSessSort(b.getAttribute('data-s')));
{const _gr=document.getElementById('grpRefresh'); if(_gr) _gr.onclick=()=>loadGroups();}
"""
