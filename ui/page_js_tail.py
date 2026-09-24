#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js_tail —— page_js 拆分子模块 TAIL(自我迭代波·案①·纯搬运字节一致·2026-09-16)。"""
TAIL = r"""function syncTabTop(){ const hd=document.querySelector('header'); if(hd) document.getElementById('tabbar').style.top=hd.offsetHeight+'px'; }
document.querySelectorAll('#tabbar .tab').forEach(b=>b.onclick=()=>showTab(b.dataset.t));
document.getElementById('gConfirm').onclick=()=>{ showTab('tasks');
  const tp=[...document.querySelectorAll('main > [data-tab="tasks"]')].find(x=>x.querySelector('#todoBox'));
  if(tp) setTimeout(()=>tp.scrollIntoView({behavior:'smooth',block:'start'}),60); };

// 🩺 bus 健康徽标 —— 吃 /api/health(后台子进程刷新的缓存,请求零扫描),25s 轮询一次
let _health=null;
function hRow(k,v,bad){ return '<div class="hblk"><span class="hk">'+k+'</span><b class="'+(bad?'hbad':'')+'">'+v+'</b></div>'; }
function renderHealth(){
  const b=document.getElementById('healthBadge'); const pn=document.getElementById('healthPanel'); if(!b) return;
  const d=_health;
  if(!d || d.loading){ b.className='hbadge loading'; b.textContent='🩺 …'; b.title='健康数据加载中（后台扫描）'; return; }
  const warns=d.warns||[];
  if(d.healthy){ b.className='hbadge ok'; b.textContent='🩺 健康'; b.title='bus 全绿'; }
  else{ b.className='hbadge warn'; b.textContent='🩺 ⚠ '+warns.length+' 项'; b.title=warns.join(' / '); }
  // 展开明细(六块)
  const sw=d.selfwake||{}, q=d.queues||{}, ls=d.loss||{}, fl=d.fleet||{}, w=d.webui||{};
  const lks=Array.isArray(d.locks)?d.locks:[];
  let h='<h4>🩺 bus 健康 · '+esc(d.as_of||'')+'</h4>';
  h+= warns.length? warns.map(x=>'<div class="hwarn">⚠ '+esc(x)+'</div>').join('')
                  : '<div class="hblk hgood"><b>✓ 全绿,无警告</b></div>';
  // 自醒四态:✓armed / ◐recent(待确认·瞬时非告警) / ⚠stale(掉线) / —off。bad 只由 stale 触发,recent 不飘红。
  h+=hRow('自醒 ✓armed/◐待确认/⚠掉线/—off',
          (sw.armed||0)+' / '+(sw.recent||0)+' / '+(sw.stale||0)+' / '+(sw.off||0), (sw.stale||0)>0)
    + ((sw.recent||0)>0?'<div class="hk" style="padding-left:8px">◐待确认 '+(sw.recent||0)+':有心跳无wait·瞬时态(刚fire待重挂/交互中)·非告警</div>':'')
    + ((sw.stale_roles&&sw.stale_roles.length)?'<div class="hk hbad" style="padding-left:8px">⚠掉线:'+esc(sw.stale_roles.join('、'))+'</div>':'');
  h+=hRow('队列 in/proc/held/failed', (q.inbox||0)+' / '+(q.processing||0)+' / '+(q.held||0)+' / '+(q.failed||0), (q.held||0)+(q.failed||0)>0);
  h+=hRow('消息丢失 孤儿/卡死/久滞', (ls.orphans||0)+' / '+((ls.stuck||[]).length)+' / '+((ls.stale_inbox||[]).length), ((ls.orphans||0)+(ls.stuck||[]).length+(ls.stale_inbox||[]).length)>0);
  (ls.stuck||[]).forEach(s=>{ h+='<div class="hk" style="padding-left:8px">卡死 '+esc(s.role)+' · '+esc(s.title||s.id||'')+' ('+esc(''+s.age_min)+'min)</div>'; });
  (ls.stale_inbox||[]).forEach(s=>{ h+='<div class="hk" style="padding-left:8px">久滞 '+esc(s.role)+' ('+esc(''+s.age_min)+'min)</div>'; });
  h+=hRow('daemon 期望/在跑', (fl.alive||0)+' / '+(fl.expected||0), (fl.dead&&fl.dead.length)>0 || (fl.orphans&&fl.orphans.length)>0);
  if(fl.dead&&fl.dead.length) h+='<div class="hk hbad" style="padding-left:8px">已死:'+esc(fl.dead.join('、'))+'</div>';
  h+=hRow('WebUI owner pid / 陈旧实例', (w.owner_pid||'?')+' / '+(w.stale_dupes||0), (w.stale_dupes||0)>0);
  h+=hRow('资源锁', lks.length? lks.map(l=>esc(l.resource||'?')+' 被占:'+esc(l.who||'?')).join(' / '):'空闲', lks.length>0);
  pn.innerHTML=h;
}
async function loadHealth(){
  try{ const r=await fetch('/api/health'); _health=await r.json(); }catch(e){ /* 保留上次 */ }
  renderHealth();
}
document.getElementById('healthBadge').onclick=(e)=>{
  e.stopPropagation();
  const pn=document.getElementById('healthPanel');
  pn.style.display = pn.style.display==='none' ? 'block' : 'none';
};
document.addEventListener('click',(e)=>{ // 点外部收起
  const wrap=document.getElementById('healthWrap');
  if(wrap && !wrap.contains(e.target)) document.getElementById('healthPanel').style.display='none';
});
window.addEventListener('resize',syncTabTop);
syncTabTop(); showTab(curTab);

// 首屏顺序很关键:/api/bi 与 /api/state 都吃 psutil 进程扫,ThreadingHTTPServer 在 GIL 下
// 无法真正并行跑这两段 CPU 密集逻辑 —— 若并发,/api/state 的冷启全进程扫(~2s,机器忙时更久)
// 会把本应 ~220ms 的 /api/bi 一起拖到数秒。故【先让 /api/bi 跑完再发 /api/state】:
// BI 表格先出(首屏快),随后 /api/state 慢扫只影响舰队表(本就慢,晚 ~220ms 无感)。
// 配合服务端启动预热 _proc_cache,首个 /api/bi 即 ~220ms。loadBI 已从 load() 解耦,
// 下面 10s 定时单独刷 BI,/api/state 的 SSE/轮询刷新不再连带重拉 BI。
// ===== 📁 路径注册表 =====
const KIND_LABEL={'dir':'目录','file':'文件','db':'DB','junction':'junction','symlink':'symlink','hardlink_mirror':'硬链镜像'};
const KIND_COLOR={'junction':'var(--accent)','symlink':'var(--live)','hardlink_mirror':'#c97bfc','db':'var(--warn)','dir':'var(--muted)','file':'var(--muted)'};
async function loadPaths(){
  const box=document.getElementById('pathsBox'); const meta=document.getElementById('pathsMeta');
  if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">加载中…</div>';
  try{
    const r=await fetch('/api/paths'); const d=await r.json();
    if(meta) meta.textContent='刷新 '+esc(d.as_of||'')+' · '+(d.bad||0)+' 🔴 · '+(d.warn||0)+' 🟡';
    renderPaths(d);
  }catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(''+e)+'</div>'; }
}
function renderPaths(d){
  const box=document.getElementById('pathsBox'); if(!box) return;
  if(!d||!d.ok){ box.innerHTML='<div class="muted" style="padding:8px 16px">错误:'+esc((d&&d.error)||'?')+'</div>'; return; }
  const cats=d.categories||[];
  box.innerHTML=cats.map(cat=>{
    const rows=(cat.entries||[]).map(e=>{
      const hc=e.health==='bad'?'var(--err)':e.health==='warn'?'var(--warn)':'var(--live)';
      const hbadge='<span style="color:'+hc+';font-size:12px" title="'+esc(e.health_msg||'正常')+'">'+
        (e.health==='bad'?'🔴':(e.health==='warn'?'🟡':'🟢'))+'</span>';
      const kindC=KIND_COLOR[e.kind]||'var(--muted)';
      const kindBadge='<span class="cap-chip" style="color:'+kindC+';font-size:11px">'+esc(KIND_LABEL[e.kind]||e.kind)+'</span>';
      const pathHtml='<span class="mono" style="font-size:12px;cursor:pointer;text-decoration:underline dotted;color:var(--text)"'
        +' title="点击复制路径" onclick="navigator.clipboard.writeText(\''+esc(e.path||'')+'\').then(()=>showToast(\'已复制\'))">'+esc(e.path||'')+'</span>';
      const openBtn=e.exists?'<button class="btn ghost mini" style="padding:2px 7px;font-size:11px" onclick="openPath(\''+esc((e.path||'').replace(/\\/g,'\\\\'))+'\')">📂</button>':'';
      const targetRow=e.target?('<div class="muted" style="font-size:11px;padding:2px 0 0 2px">→ target: <span class="mono">'+esc(e.target)+'</span></div>'):'';
      const msgRow=e.health_msg&&e.health!=='ok'?('<div style="font-size:11px;color:'+hc+';padding:2px 0">'+esc(e.health_msg)+'</div>'):'';
      return '<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 14px;border-bottom:1px solid var(--line)">'
        +'<div style="min-width:18px;padding-top:1px">'+hbadge+'</div>'
        +'<div style="flex:1;min-width:0">'
        +'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
        +'<b style="font-size:13px">'+esc(e.name||e.id||'?')+'</b>'+kindBadge
        +'<span class="muted" style="font-size:11px">owner: '+esc(e.owner||'?')+'</span>'
        +'</div>'
        +'<div style="margin-top:3px;display:flex;gap:6px;align-items:center">'+pathHtml+openBtn+'</div>'
        +(e.desc?'<div class="muted" style="font-size:11.5px;margin-top:3px">'+esc(e.desc)+'</div>':'')
        +targetRow+msgRow
        +'</div></div>';
    }).join('');
    return '<div style="margin:10px 0 0 0">'
      +'<div class="sec-h" style="font-size:13.5px;padding:6px 16px;background:var(--bg2)">'+esc(cat.name)+'</div>'
      +rows+'</div>';
  }).join('');
}
async function openPath(p){
  try{
    const r=await fetch('/api/open-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})});
    const d=await r.json();
    showToast(d.ok?('已打开 '+p):(d.error||'打开失败'));
  }catch(e){ showToast('请求失败:'+e); }
}
document.getElementById('pathsRefresh').onclick=()=>loadPaths();

// ===== 💾 存储全景(路径页嵌入) =====
function _fmtBytes(b){
  if(b==null||b<0) return '?';
  if(b<1024) return b+'B';
  if(b<1024*1024) return (b/1024).toFixed(1)+'KB';
  if(b<1024*1024*1024) return (b/(1024*1024)).toFixed(1)+'MB';
  return (b/(1024*1024*1024)).toFixed(2)+'GB';
}
async function loadStorage(force){
  const panel=document.getElementById('storagePanel'), meta=document.getElementById('storageMeta');
  if(!panel) return;
  if(!panel.innerHTML.trim()) morphInto(panel,'<div class="muted" style="font-size:12px">加载中…</div>');
  try{
    const r=await fetch('/api/storage'+(force?'?fresh=1':'')); const d=await r.json();
    if(meta) meta.textContent='缓存2min · '+(d.violations&&d.violations.length?'⚠'+d.violations.length+'项违规':'无违规');
    renderStorage(d);
  }catch(e){ morphInto(panel,'<div class="muted" style="font-size:12px">读取失败:'+esc(''+e)+'</div>'); }
}
function renderStorage(d){
  const panel=document.getElementById('storagePanel'); if(!panel) return;
  if(!d||!d.ok){ morphInto(panel,'<div class="muted" style="font-size:12px">错误:'+esc((d&&d.error)||'?')+'</div>'); return; }
  const tri=(d.tri||[]).map(t=>{
    const isMA=t.role==='代码+运行态方舟';
    return `<div style="display:inline-flex;align-items:baseline;gap:6px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:5px 12px;margin:0 6px 4px 0">`
      +`<span style="font-size:12px;font-weight:600;color:${isMA?'var(--accent)':'var(--muted)'}">${esc(t.name)}</span>`
      +`<span style="font-size:14px;font-weight:700">${_fmtBytes(t.bytes)}</span>`
      +`<span class="muted" style="font-size:11px">${esc(t.role)}</span>`+`</div>`;
  }).join('');
  const topRows=(d.root_top||[]).map(t=>{
    const jBadge=t.junction?'<span class="cap-chip" style="color:var(--accent);font-size:10.5px">↗外置</span>':'';
    const sizeCell=t.junction?'<span class="muted" style="font-size:11px">—(外置)</span>'
      :`<span style="font-size:12.5px;font-weight:${t.bytes>50*1024*1024?700:400};color:${t.bytes>200*1024*1024?'var(--warn)':'inherit'}">${_fmtBytes(t.bytes)}</span>`;
    return `<tr><td style="padding:4px 10px 4px 0"><span class="mono" style="font-size:12px">${esc(t.name)}</span> ${jBadge}</td>`
      +`<td style="padding:4px 10px 4px 0">${sizeCell}</td>`
      +`<td style="padding:4px 10px 4px 0"><span class="cap-chip" style="font-size:10.5px">${esc(t.cls||'')}</span></td>`
      +`<td style="padding:4px 0;font-size:11px;color:var(--muted)">${esc(t.note||'')}</td></tr>`;
  }).join('');
  const viols=d.violations||[];
  const violHtml=viols.length
    ?`<div style="margin-top:10px;padding:8px 12px;background:rgba(255,80,80,.08);border-left:3px solid var(--err);border-radius:0 6px 6px 0">`
      +`<div style="font-size:12px;font-weight:700;color:var(--err);margin-bottom:6px">⚠ 违规候选 ${viols.length} 项（>5MB 非豁免区）</div>`
      +viols.map(v=>`<div style="display:flex;gap:10px;align-items:center;font-size:11.5px;padding:2px 0">`
        +`<span class="mono" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(v.path)}</span>`
        +`<span style="font-weight:600;color:var(--err)">${_fmtBytes(v.bytes)}</span></div>`).join('')
      +`<div class="muted" style="font-size:11px;margin-top:6px">处置指引：移至 _data + 建 junction 回链</div></div>`:'';
  morphInto(panel,
    `<div style="margin-bottom:8px">${tri}</div>`
    +`<div class="muted" style="font-size:11px;font-weight:600;margin-bottom:4px">项目根内分布</div>`
    +`<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%"><tbody>${topRows}</tbody></table></div>`
    +violHtml);
}
document.getElementById('storageRefresh').onclick=()=>loadStorage(true);

// ===== 📋 舰队日志 =====
let _fleetlogLoaded=false;
async function loadFleetLog(){
  const box=document.getElementById('flBox');
  if(box&&!box.innerHTML.trim()) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{
    const r=await fetch('/api/fleet-log');
    renderFleetLog(await r.json());
    _fleetlogLoaded=true;
  }catch(e){
    if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>';
  }
}
function renderFleetLog(d){
  const box=document.getElementById('flBox'); const meta=document.getElementById('flMeta'); if(!box) return;
  if(!d||!d.ok){ box.innerHTML='<div class="muted" style="padding:8px 16px">日志读取失败</div>'; if(meta)meta.textContent=''; return; }
  const kindIcon={incident:'🔥',maintenance:'🔧',milestone:'🏁',retire:'⊘',revive:'↩',train:'🎓',note:'📌'};
  const kindColor={incident:'var(--fail)',maintenance:'rgba(255,165,0,.9)',milestone:'var(--live)',retire:'var(--dim)',revive:'var(--accent)',train:'var(--accent)',note:'var(--muted)'};
  if(meta) meta.textContent=(d.n||0)+' 条 · 倒序';
  const entries=d.entries||[];
  if(!entries.length){ morphInto(box,'<div class="muted" style="padding:8px 16px">（无日志）</div>'); return; }
  morphInto(box, entries.map((e,i)=>{
    const icon=kindIcon[e.kind]||'·';
    const col=kindColor[e.kind]||'var(--fg)';
    const ts=(e.ts||'').replace('T',' ');
    return `<div class="ev" data-key="fl-${i}" style="padding:8px 16px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:flex-start">
      <span style="font-size:16px;line-height:1.3">${icon}</span>
      <div style="flex:1;min-width:0">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <span style="color:${col};font-weight:600">${esc(e.title||'')}</span>
          <span class="cap-chip" style="font-size:10px;color:${col}">${esc(e.kind||'')}</span>
          <span class="muted" style="font-size:11px">${esc(ts)}</span>
          ${e.actor?`<span class="muted" style="font-size:11px">by ${esc(e.actor)}</span>`:''}
        </div>
        ${e.detail?`<div class="muted" style="font-size:12px;margin-top:3px">${esc(e.detail)}</div>`:''}
      </div>
    </div>`;
  }).join(''));
}
document.getElementById('flRefresh').onclick=()=>loadFleetLog();

loadBI().finally(load); connectSSE(); setPoll();
// 刷新门控(P1):BI 定时刷只在【BI tab 可见】时跑,不再无视可见性每 10s 扫 psutil + 拉 /api/bi
setInterval(()=>{ if(curTab==='bi') loadBI(); }, 10000);
// 能力池兜底轮询:caps tab 可见时每 10s 重拉 /api/caps —— agents.json/capabilities.json 改了(如培训师加 cap)
// 不必再手动刷新即可反映。门控同 BI:不在 caps tab 不跑。/api/caps 只读 JSON、零 psutil 扫,轮询开销极小。
setInterval(()=>{ if(curTab==='caps') loadCaps(); }, 10000);
// 首屏一次性预热(各自独立懒加载,不拖首屏);之后按 tab 进入再刷(见 showTab)
setTimeout(()=>loadUsage(true), 800); setTimeout(loadSessions, 1200);
// ===== 🛰 舰队整体状态顶栏 =====
async function loadFleetState(){
  try{
    const r=await fetch('/api/fleet-state');
    renderFleetState(await r.json());
  }catch(e){}
}
function renderFleetState(d){
  const bar=document.getElementById('fleetStateBar');
  if(!d||d.rule==='error'){ bar.style.display='none'; return; }
  bar.style.display='flex';
  document.getElementById('fsIcon').textContent=d.icon||'';
  document.getElementById('fsState').textContent=d.state||'';
  document.getElementById('fsGuard').textContent=d.guard||'–';
  const c=d.counts||{};
  document.getElementById('fsArmed').textContent=c.armed!=null?c.armed:'–';
  document.getElementById('fsExpected').textContent=c.expected!=null?c.expected:'–';
  const stale=d.stale_roles||[];
  const sw=document.getElementById('fsStaleWrap');
  if(stale.length){ sw.style.display=''; document.getElementById('fsStaleN').textContent=stale.length; document.getElementById('fsStaleDetail').textContent=stale.join('、'); }
  else { sw.style.display='none'; }
  const boot=d.boot_pending||[];
  const bw=document.getElementById('fsBootWrap');
  if(boot.length){ bw.style.display=''; document.getElementById('fsBootN').textContent=boot.length; document.getElementById('fsBootDetail').textContent=boot.join('、'); }
  else { bw.style.display='none'; }
  // 维修模式:顶栏底色变暖
  bar.style.background=d.rule==='maintenance'?'rgba(255,165,0,.08)':'var(--card)';
}
function toggleFleetStale(btn){
  const d=document.getElementById('fsStaleDetail');
  const open=d.style.display!=='none';
  d.style.display=open?'none':'';
  const n=document.getElementById('fsStaleN').textContent;
  btn.textContent=open?('⚠ 掉线 '+n+' ▾'):'⚠ 折叠 ▴';
}
function toggleFleetBoot(btn){
  const d=document.getElementById('fsBootDetail');
  const open=d.style.display!=='none';
  d.style.display=open?'none':'';
  const n=document.getElementById('fsBootN').textContent;
  btn.textContent=open?('⛔ 未装载 '+n+' ▾'):'⛔ 折叠 ▴';
}
loadFleetState(); setInterval(loadFleetState, 30000); // 30s 轮询(fleet_state_rules.json·零 psutil)

loadHealth(); setInterval(loadHealth, 25000);   // 🩺 健康徽标:25s 轮询(后端缓存,零扫描)
loadCtx(); setInterval(loadCtx, 15000);          // 🧠 上下文占用徽标+看板:15s 轮询(后端缓存,请求零现读)— 全局告警,不分 tab

// U5b 全站交互原语(块认知):钳行点击展开 / 折叠卡头点击开合(正文默认收敛·标题即块锚点)
document.addEventListener('click',e=>{
  if(e.target.closest('a,button,select,input,textarea,summary,.arch-sid')) return;
  const c=e.target.closest('.clampable'); if(c){ c.classList.toggle('open'); return; }
  const fh=e.target.closest('.fold-head'); if(fh){ const f=fh.closest('.fold'); if(f) f.classList.toggle('open'); }
});
"""
