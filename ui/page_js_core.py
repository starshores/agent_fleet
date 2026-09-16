#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js_core —— page_js 拆分子模块 CORE(自我迭代波·案①·纯搬运字节一致·2026-09-16)。"""
CORE = r"""let state=null, pollTimer=null, lastTasksOpen=new Set();

// 增量更新统一入口:把容器 el 的【孩子】patch 成 innerHtml(childrenOnly→容器自身的 class/style 不动)。
//  · getNodeKey 用 id 或 data-key(列表项给稳定业务 key)→ 原地更新、不重建 → 不闪、滚动不跳。
//  · onBeforeElUpdated 跳过【正在输入/选择】的表单控件 → 光标/未提交输入/下拉态不被刷新冲掉。
function _keyOf(n){ if(n.nodeType!==1) return null; return n.id || n.getAttribute('data-key') || null; }
function _onBeforeUpd(f,t){ if(f===document.activeElement && (f.tagName==='INPUT'||f.tagName==='TEXTAREA'||f.tagName==='SELECT')) return false;
  // fold/clampable 展开态活在 DOM class(渲染串不带 open)→ morph 前把 open 抄到目标,数据刷新不收起(Master数分 U5b finding 修死)
  if(t&&f.classList&&f.classList.contains('open')&&t.classList&&(t.classList.contains('fold')||t.classList.contains('clampable'))) t.classList.add('open');
  return true; }
function morphInto(el, innerHtml){
  if(!el) return;
  const tag=el.tagName.toLowerCase();
  try{ morphdom(el, '<'+tag+'>'+innerHtml+'</'+tag+'>',
    {childrenOnly:true, getNodeKey:_keyOf, onBeforeElUpdated:_onBeforeUpd}); }
  catch(e){ el.innerHTML=innerHtml; }   // 兜底:morph 异常退回整建,绝不白屏
}
// 舰队表展开任务明细(委托 onclick;展开态记入 lastTasksOpen,morph 重渲染后由 render 还原)
function toggleTasks(tr){
  const det=tr.nextElementSibling;
  if(!det || !det.classList.contains('tasks')) return;
  const role=tr.dataset.role; const vis=det.style.display!=='none';
  det.style.display=vis?'none':''; if(vis) lastTasksOpen.delete(role); else lastTasksOpen.add(role);
}
async function manualDone(taskId,role,btn){
  const result=window.prompt('done 结果(留空=手动done)','手动done');
  if(result===null) return;
  btn.disabled=true; btn.textContent='…';
  try{
    const r=await fetch('/api/done',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task_id:taskId,role:role,result:result||'手动done'})});
    const d=await r.json();
    if(d.ok){toast('✅ done: '+taskId); btn.textContent='已done';}
    else{toast('done失败:'+(d.error||'?'),true); btn.disabled=false; btn.textContent='done ✓';}
  }catch(e){toast('请求失败:'+e,true); btn.disabled=false; btn.textContent='done ✓';}
}

function esc(s){return (s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtAge(a){ if(a==null) return '?'; if(a<60) return a+'s'; if(a<3600) return Math.floor(a/60)+'m'+(a%60)+'s'; return Math.floor(a/3600)+'h'+Math.floor((a%3600)/60)+'m';}
function qc(n,cls){return `<span class="qcount ${n?cls:'z'}">${n}</span>`;}
function swBadge(s,detail){
  const t=esc(detail||'');
  if(s==='armed') return ` <span class="badge live" title="自醒在岗 ${t}">🔔自醒</span>`;
  if(s==='recent') return ` <span class="badge idle" title="有心跳无wait进程·瞬时态(刚fire待重挂/交互中)·非告警 ${t}">◐待确认</span>`;
  if(s==='stale') return ` <span class="badge dead" title="自醒掉线!需重挂 ${t}">⚠自醒掉线</span>`;
  return ` <span class="badge none" title="未自醒 ${t}">—</span>`;
}
// turn 状态(turn_state.py hook 实时盖):thinking=turn内思考 / tool=正跑工具 / idle=空闲。
// 稳健化(2026-07-10 用户「不够灵敏 + 中断后卡住」;2026-08-04 用户抓错「armed 盖掉真在干活」重排):
//  ① 【新鲜活动态优先】thinking/tool 且 age 未超陈旧阈 → 判在干活——前台 cli+后台 wait 是主流形态,
//     armed(后台 wait 挂着)与会话正在干活【并存】,armed 不可再无条件盖成空闲(Master数分 Orchestrating
//     16min 被显示成「空闲·待命」实证);旧假设「有 wait=阻塞等=不可能在思考」只对 /loop serve worker 成立。
//  ② armed 只接管【无新鲜活动】的情形(idle/无状态/陈旧)→ 空闲·待命——仍治 2026-07-10 的
//     「中断后 thinking 卡住不回落」(中断不触发任何 hook,状态冻住变陈旧 → armed 收口成空闲)。
//  ③ 非 armed 且 thinking/tool 超 STALE 秒无新事件 → 判「停顿?」。数据只来自 hook 事件 + wait 进程,非推断。
// 分状态陈旧阈:思考态推理很少 >30s;工具态本可跑很久(长 bash/Agent 编排等),放宽到 300s 才疑。
const TURN_STALE={thinking:30, tool:300};
function turnBadge(tn, sw){
  const st=tn&&tn.state, age=tn?tn.age:null, ag=(age==null?'':' '+fmtAge(age));
  const stale=st?(TURN_STALE[st]||60):0;
  const freshActive=st&&st!=='idle'&&age!=null&&age<=stale;
  if(freshActive){
    if(st==='tool') return `<span class="badge warn" title="正在执行工具 ${esc(tn.tool||'')}${ag}(后台 wait 可并存)">🔧 ${esc(tn.label)}${ag}</span>`;
    return `<span class="badge live" title="在 turn 内(思考/执行中)${ag}(后台 wait 可并存)">🟢 ${esc(tn.label||'思考/执行中')}${ag}</span>`;
  }
  if(sw==='armed'){
    const note=(st&&st!=='idle')?`(最近状态「${esc(tn.label||st)}」已冻 ${fmtAge(age)}:长工具编排中或已中断,无新 hook 事件)`:'';
    return `<span class="badge idle" title="后台 wait 在·无新鲜 turn 事件=空闲待命${note}">⚪ 空闲·待命</span>`;
  }
  if(!st) return '<span class="badge none" title="无 turn 状态(未装 hook / 非 bus 成员 / 尚无事件)">–</span>';
  if(st==='idle') return `<span class="badge idle" title="turn 已结束(Stop/idle_prompt)·空闲${ag}">⚪ 空闲${ag}</span>`;
  if(age!=null && age>stale)
    return `<span class="badge none" title="状态为「${esc(tn.label||st)}」但已 ${fmtAge(age)} 无新 hook 事件 → 判停顿(用户中断不触发任何 hook·官方证实 / 或异常结束)">◌ 停顿?${ag}</span>`;
  if(st==='tool') return `<span class="badge warn" title="正在执行工具 ${esc(tn.tool||'')}${ag}">🔧 ${esc(tn.label)}${ag}</span>`;
  return `<span class="badge live" title="在 turn 内(思考/执行中)${ag}">🟢 ${esc(tn.label||'思考/执行中')}${ag}</span>`;
}

function toast(msg,err){const t=document.getElementById('toast');t.textContent=msg;t.className='toast show'+(err?' err':'');setTimeout(()=>t.className='toast',3200);}

function setConn(on){const d=document.getElementById('connDot'),t=document.getElementById('connTxt');
  d.className='dot '+(on?'on':'off'); t.textContent=on?'事件实时(SSE)':'已断开 · 轮询中';}

function render(){
  if(!state) return;
  const f=state.fleet;
  document.getElementById('aliveN').textContent=f.alive_count;
  document.getElementById('fleetN').textContent=f.fleet_count;
  document.getElementById('updT').textContent='更新 '+state.now;

  // fleet table —— 增量 morph(展开态由 lastTasksOpen 声明式驱动 + 委托 onclick,刷新中展开/滚动不丢)
  const subBadge={inbox:'idle',processing:'warn',held:'held',failed:'failed'};
  let fleetHtml='';
  const _mkey=m=>(!m||m==='-')?'￿':m;
  const sortedRoles=[...f.roles].sort((a,b)=>{
    const aModel=_mkey(a.model),bModel=_mkey(b.model);
    if(aModel!==bModel) return aModel.localeCompare(bModel);
    return (a.role||'').localeCompare(b.role||'');
  });
  sortedRoles.forEach(r=>{
    const hasTasks=(r.inbox+r.processing+(r.held||0)+(r.failed||0))>0;
    const open=lastTasksOpen.has(r.role);
    fleetHtml+=`<tr data-key="fr-${esc(r.role)}" class="role-row${hasTasks?' clickable':''}"${hasTasks?` data-role="${esc(r.role)}" onclick="toggleTasks(this)"`:''}>
      <td><b>${esc(r.role)}</b></td>
      <td class="mono" style="font-size:11px;width:120px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.model||'')}">${esc((r.model||'').replace('claude-','')||'–')}</td>
      <td style="width:200px;max-width:200px;overflow:hidden"><div style="display:flex;gap:4px;align-items:center;max-width:200px;overflow:hidden"><span class="badge vbadge ${r.vclass}" title="${esc(r.verdict)}">${esc(r.verdict)}</span>${swBadge(r.self_wake,r.self_wake_detail)}</div></td>
      <td>${turnBadge(r.turn, r.self_wake)}</td>
      <td class="mono">${esc(r.pid_display||'–')}</td>
      <td class="mono">${fmtAge(r.age)}</td>
      <td>${qc(r.inbox,'ib')}</td><td>${qc(r.processing,'pr')}</td><td>${qc(r.done,'dn')}</td>
      <td>${qc(r.held||0,'held')}</td><td>${qc(r.failed||0,'failed')}</td>
      <td class="cwd" title="${esc(r.cwd||'')}">${esc(r.cwd||'–')}</td></tr>`;
    if(hasTasks){
      let inner='<div class="task-wrap">';
      (r.tasks||[]).forEach(t=>{
        const appr=(t.sub==='inbox'&&t.requires_approval);
        const canDone=(t.sub==='processing'||t.sub==='inbox'||t.sub==='failed');
        inner+=`<div class="task-card"><div class="t-h">
          <span class="badge ${subBadge[t.sub]||'idle'}">${esc(t.sub)}</span>
          ${appr?'<span class="badge appr">⚠ requires_approval</span>':''}
          ${(t.requeue_count>0)?'<span class="badge warn">requeue×'+esc(t.requeue_count)+'</span>':''}
          <span class="mono">${esc(t.id)}</span> · from <b>${esc(t.from)}</b>
          <span class="t-title">${esc(t.title)}</span>
          ${t.claimed?'· claimed '+esc(t.claimed):''}
          <span class="spacer" style="flex:1"></span>
          ${canDone?`<button class="btn ghost mini" style="font-size:11px;padding:2px 8px;color:var(--ok)" onclick="manualDone('${esc(t.id)}','${esc(r.role)}',this)">done ✓</button>`:''}
          </div>
          ${appr?'<div class="hint">高风险任务:自动监听者(serve/daemon/wait)不会领取 → 有人值守的 tab 用 <code>wait --include-approval</code> 显式领取执行</div>':''}
          <pre>${esc(t.task)}</pre></div>`;
      });
      inner+='</div>';
      fleetHtml+=`<tr data-key="fd-${esc(r.role)}" class="tasks" data-role="${esc(r.role)}" style="display:${open?'':'none'}"><td colspan="12">${inner}</td></tr>`;
    }
  });
  morphInto(document.getElementById('fleetBody'), fleetHtml);

  // 维修横幅
  const mb=document.getElementById('maintBanner');
  const mt=state.maintenance||{};
  mb.style.display=mt.active?'':'none';
  document.getElementById('maintDetail').textContent=mt.active?(mt.by||'')+(mt.since?' · since '+mt.since:'')+(mt.note?' — '+mt.note:''):'';

  // process footer
  let foot=[];
  if(f.tagged.length) foot.push('已标注(非fleet):'+f.tagged.map(t=>`<span class="tag ${t.private?'lock':''}">${esc(t.label)} pid ${t.pid}${t.private?' 🔒':(t.cwd?' · '+esc(t.cwd):'')}</span>`).join(''));
  foot.push(`<span class="muted">在跑 claude ${f.alive_count} · 在岗(已认领)${f.fleet_count} · 已标注 ${f.tagged.length} · 未声明 ${f.unknown.length}</span>`);
  if(f.unknown.length) foot.push('未声明(非fleet·未标记):'+f.unknown.map(u=>`<span class="tag">pid ${u.pid}${u.cwd?' · '+esc(u.cwd):''}</span>`).join(''));
  morphInto(document.getElementById('procFoot'), foot.join('<br>'));

  // daemon 自愈面板(fleet.json × 存活)
  const db=document.getElementById('daemonBox'); const dm=state.daemons||{daemons:[]};
  if(dm.error){ morphInto(db,'<div class="dmn"><span class="badge dead">err</span> '+esc(dm.error)+'</div>'); }
  else if(!dm.daemons.length){ morphInto(db,'<div class="muted" style="padding:8px 16px">fleet.json 无 daemon 条目</div>'); }
  else{ morphInto(db, dm.daemons.map(d=>`<div class="dmn" data-key="dm-${esc(d.role)}">
      <span class="badge ${d.alive?'live':'dead'}">${d.alive?'✓ 存活':'✗ 已死'}</span>
      <b>${esc(d.role)}</b>
      <span class="cwd" title="${esc(d.cwd||'')}">${esc(d.cwd||'')}</span>
      <span class="spacer" style="flex:1"></span>
      <span class="muted">wait ${esc(d.wait_timeout)}s</span></div>`).join('')); }

  // 最近事件(events.jsonl 倒序)
  const eb=document.getElementById('eventBox'); const evs=state.events||[];
  if(!evs.length){ morphInto(eb,'<div class="muted" style="padding:8px 16px">（无事件）</div>'); }
  else{ morphInto(eb, evs.map(e=>{
      const tail=e.event==='send'?('→ '+esc(e.to)+(e.title?' · '+esc(e.title):'')):
                 e.event==='claim'?('by '+esc(e.role)):
                 e.event==='done'?('→ '+esc(e.to)+' · '+esc(e.status||'')):(esc(e.raw||''));
      return `<div class="ev" data-key="ev-${esc(e.id||'')}-${esc(e.ts||'')}-${esc(e.event||'')}"><span class="ets">${esc(e.ts)}</span>
        <span class="et ${esc(e.event)}">${esc(e.event)}</span>
        <span class="mono">${esc(e.id||'')}</span>
        ${e.frm?'<span class="muted">from '+esc(e.frm)+'</span>':''}
        <span>${tail}</span></div>`; }).join('')); }

  // send-to dropdown (preserve selection) —— morph 保选中 + 聚焦时不被刷
  const sel=document.getElementById('sendTo'); const cur=sel.value;
  morphInto(sel, state.all_roles.map(r=>`<option data-key="o-${esc(r)}" ${r==cur?'selected':''}>${esc(r)}</option>`).join(''));

  // reply role dropdown
  const rr=document.getElementById('replyRole'); const rc=rr.value||state.reply_role;
  morphInto(rr, state.all_roles.map(r=>`<option data-key="o-${esc(r)}" ${r==rc?'selected':''}>${esc(r)}</option>`).join(''));

  // replies
  const rb=document.getElementById('replyBox');
  if(!state.replies.length){ morphInto(rb,'<div class="muted" style="padding:6px 16px">（无回填）</div>'); }
  else{ morphInto(rb, state.replies.map(r=>`<div class="reply" data-key="rp-${esc(r.file||r.id)}">
      <div class="r-h"><span class="badge ${r.status==='ok'?'live':'dead'}">${esc(r.status||'?')}</span>
        <span class="mono">${esc(r.id)}</span> · from <b>${esc(r.from)}</b>
        <span>${esc(r.title)}</span> <span class="muted">${esc(r.finished)}</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn ghost" style="margin:0;padding:2px 9px" onclick="clearOne('${esc(r.file)}')">清除</button></div>
      <pre>${esc(r.result)}</pre></div>`).join('')); }

  renderTodos();
  fillLiveRoles();
}

// 实时视图 live.log —— .log 不走 SSE,选中 role 后独立轮询(1.5s)
let livePoll=null;
function fillLiveRoles(){
  const sel=document.getElementById('liveRole'); if(!sel) return;
  const cur=sel.value;
  morphInto(sel, ['<option value="" data-key="o-">（选 role）</option>']
    .concat((state.all_roles||[]).map(r=>`<option data-key="o-${esc(r)}" ${r==cur?'selected':''}>${esc(r)}</option>`)).join(''));
}
async function pollLive(){
  const role=document.getElementById('liveRole').value;
  const box=document.getElementById('liveBox');
  const stat=document.getElementById('liveStat');
  if(!role){ box.textContent='（未选 role）'; stat.textContent='未选 role'; return; }
  try{
    const r=await fetch('/api/live?role='+encodeURIComponent(role));
    const j=await r.json();
    const atBottom=box.scrollTop+box.clientHeight>=box.scrollHeight-40;
    if(!j.ok){ box.textContent='读取失败:'+esc(j.error||'?'); stat.textContent='错误'; return; }
    if(!j.exists){ box.textContent='（'+esc(role)+' 暂无 live.log —— 该 role 可能不是 headless 执行体,或还没跑过任务）'; stat.textContent='无 live.log'; }
    else{ box.textContent=j.content||'（空）'; stat.textContent='更新 '+esc(j.mtime||'')+' · 末尾'+Math.min(j.size,12288)+'B/共'+j.size+'B'; if(atBottom) box.scrollTop=box.scrollHeight; }
  }catch(e){ stat.textContent='拉取异常'; }
}
function setLivePoll(){
  if(livePoll){clearInterval(livePoll);livePoll=null;}
  const role=document.getElementById('liveRole').value;
  const dot=document.getElementById('liveDot');
  if(role){ dot.className='live-dot on'; pollLive(); livePoll=setInterval(pollLive,1500); }
  else{ dot.className='live-dot'; document.getElementById('liveBox').textContent='（选一个 role 看它的 live.log）';
        document.getElementById('liveStat').textContent='未选 role'; }
}

const PRI_CLS={'高':'p-hi','中':'p-mid','低':'p-lo'};
const PRI_CARD={'高':'pri-hi','中':'pri-mid','低':'pri-lo'};
const ST_CLS={'待办':'s-todo','进行中':'s-doing','完成':'s-done','搁置':'s-hold'};
function isOverdue(t){
  if(!t.deadline||t.status==='完成') return false;
  const d=new Date(t.deadline.replace(' ','T'));
  if(isNaN(d)) return false;
  return d.getTime() < Date.now();
}
let todoView='active';   // active=待办池(未完成) | history=已完成历史
const roleSel={};        // todoId → 用户为该卡选的「派给」role;重渲染后回填,防自动刷新冲掉选择(误派根因)
function roleOptsFor(t){
  const sel=roleSel[t.id]||'';
  let h='<option value="" '+(sel?'':'selected')+' disabled>派给…</option>';
  (state.all_roles||[]).forEach(r=>{ h+='<option value="'+esc(r)+'" '+(sel===r?'selected':'')+'>'+esc(r)+'</option>'; });
  return h;
}
// 响应式重构(morphdom):卡内【临时 UI 态】全部存 JS、由 cardHtml 渲染出来 → morphdom 增量 patch DOM 与之对齐,
// 刷新时天然不丢(不再靠 dirtyCards 手工保护整卡)。临时态有两处:
//   · roleSel[id]  —— 用户为该卡选的「派给」role(下拉选择)。
//   · openEditors[id] —— 该卡的补充说明编辑器草稿文本(打开中);undefined=未打开。
const openEditors={};
const answerDrafts={};   // todoId → 🙋待确认卡回复框草稿(响应式,morphdom 刷新不丢)
// 待确认:agent 通过 ask-user 投池、在等用户拍板的项(source 以「确认·」开头 或 带 confirm_from,且 status=待办)
function isPendingConfirm(t){
  return t.status==='待办' && (t.confirm_from || (t.source||'').indexOf('确认·')===0);
}
function cardHtml(t){
  const od=isOverdue(t);
  const pc=isPendingConfirm(t);
  const stSel=['待办','进行中','搁置','完成'].map(s=>`<option ${s===t.status?'selected':''}>${s}</option>`).join('');
  const link=t.task_id?`<span class="linkpill" title="已派给 ${esc(t.dispatched_to||'')}">🔗 ${esc(t.dispatched_to||'')} · ${esc(t.task_id)}</span>`:'';
  const confirmBadge=pc?`<span class="badge confirm">🙋 待确认</span>`:'';
  const confirmWho=pc&&t.confirm_from?`<span class="confirm-who">等 ${esc(t.confirm_from)} 拍板</span>`:'';
  const who=t.confirm_from||t.creator||t.source||'用户';   // 谁发的(创建者/来源)
  // 补充说明区:由 openEditors 状态驱动(打开=渲染 textarea+草稿;关闭=渲染按钮)→ morphdom 刷新天然保留
  const editing = openEditors[t.id] !== undefined;
  const noteHtml = editing
    ? `<textarea class="note-edit" id="ne-${t.id}" placeholder="给这条待办加补充说明/上下文(随时改)" oninput="openEditors['${t.id}']=this.value">${esc(openEditors[t.id])}</textarea>`
      + `<div class="note-btns"><button class="btn mini" onclick="tNoteSave('${t.id}')">保存</button>`
      + `<button class="btn mini ghost" onclick="tNoteCancel('${t.id}')">取消</button></div>`
    : (t.note
        ? `<span class="unote">💬 补充:${esc(t.note)}</span><button class="btn mini ghost nbtn" onclick="tNoteEdit('${t.id}')">改</button>`
        : `<button class="btn mini ghost nbtn" onclick="tNoteEdit('${t.id}')">+ 补充说明</button>`);
  return `<div class="todo-card ${PRI_CARD[t.priority]||''} ${pc?'pending-confirm':''} ${t.status==='完成'?'is-done':''}" id="todocard-${t.id}" data-id="${t.id}">
      <div class="todo-h">
        ${confirmBadge}
        <span class="badge ${PRI_CLS[t.priority]||'p-mid'}">${esc(t.priority)}</span>
        <span class="badge ${ST_CLS[t.status]||'s-todo'}">${esc(t.status)}</span>
        <span class="todo-title">${esc(t.title)}</span>
        ${confirmWho}
        ${link}
      </div>
      ${t.detail?`<div class="todo-detail">${esc(t.detail)}</div>`:''}
      <div class="todo-meta">
        <span class="who-pill" title="谁发的(创建者/来源)">👤 ${esc(who)}</span>
        ${t.source&&t.source!==who?`<span>来源:${esc(t.source)}</span>`:''}
        ${t.deadline?`<span class="${od?'overdue':''}">截止:${esc(t.deadline)}${od?' ⚠逾期':''}</span>`:''}
        <span>建:${esc(t.created)}</span>
        ${t.dispatch_note?`<span class="dnote" title="${esc(t.dispatch_note_by||'')} @ ${esc(t.dispatch_note_at||'')}">📝 附加:${esc(t.dispatch_note)}</span>`:''}
        ${t.ignored?`<span class="badge failed">🚫 已忽略</span>`:''}
        ${t.dispatch_result?`<span class="dnote" title="派活结果 status=${esc(t.dispatch_result_status||'')}">✅ 结果:${esc(t.dispatch_result)}</span>`:''}
        ${t.updated&&todoView==='history'?`<span>完成/更新:${esc(t.updated)}</span>`:''}
      </div>
      <div class="note-area" id="na-${t.id}">${noteHtml}</div>
      ${(pc && t.confirm_from && t.confirm_from!=='bus健康' && t.confirm_from!=='unknown') ? `<div class="answer-area">
        <textarea class="ans-edit" id="ans-${t.id}" placeholder="回复 ${esc(t.confirm_from)} 的提问 → 发送后自动路由回它、本项标完成" oninput="answerDrafts['${t.id}']=this.value">${esc(answerDrafts[t.id]||'')}</textarea>
        <button class="btn mini" onclick="tAnswer('${t.id}')">↩ 回复 ${esc(t.confirm_from)}</button>
      </div>` : ''}
      <div class="todo-acts">
        <span class="muted">状态</span>
        <select onchange="tStatus('${t.id}',this.value)">${stSel}</select>
        <span class="muted" style="margin-left:6px">派给</span>
        <select id="trole-${t.id}" onchange="roleSel['${t.id}']=this.value">${roleOptsFor(t)}</select>
        <button class="btn mini" onclick="tDispatch('${t.id}')">派活</button>
        ${t.status!=='完成'?`<button class="btn mini ghost" onclick="tIgnore('${t.id}')" title="标记了结:不派活、不发任何 bus 任务">忽略</button>`:''}
        <button class="btn mini ghost" onclick="tEdit('${t.id}')">编辑</button>
        <button class="btn mini danger" onclick="tDelete('${t.id}')">删除</button>
      </div>
    </div>`;
}
function renderTodos(){
  const box=document.getElementById('todoBox'); if(!box) return;
  const all=(state.todos||[]);
  // 计数/角标/tab 高亮 —— 始终更新(不碰卡片 DOM,不受脏卡影响)
  const activeAll=all.filter(t=>t.status!=='完成');
  const doneAll=all.filter(t=>t.status==='完成');
  const pendN=activeAll.filter(isPendingConfirm).length;
  document.getElementById('tcountActive').textContent=activeAll.length;
  document.getElementById('tcountHistory').textContent=doneAll.length;
  const ca=document.getElementById('confirmAlert');
  if(pendN){ ca.style.display=''; ca.textContent='🙋 '+pendN+' 项待你确认'; } else { ca.style.display='none'; }
  const gc=document.getElementById('gConfirm');
  if(gc){ if(pendN){ gc.style.display=''; gc.innerHTML='🙋 <b>'+pendN+'</b> 项待确认'; } else { gc.style.display='none'; } }
  document.getElementById('tviewActive').classList.toggle('active',todoView==='active');
  document.getElementById('tviewHistory').classList.toggle('active',todoView==='history');

  let items=(todoView==='history'?doneAll:activeAll).slice();
  const fp=document.getElementById('filtPri').value;
  const fs=document.getElementById('filtStatus').value;
  if(fp) items=items.filter(t=>t.priority===fp);
  if(fs) items=items.filter(t=>t.status===fs);
  if(todoView==='active'){ items.sort((a,b)=>(isPendingConfirm(b)?1:0)-(isPendingConfirm(a)?1:0)); }
  else{ items.sort((a,b)=>String(b.updated||'').localeCompare(String(a.updated||''))); }

  const inner = items.length
    ? items.map(cardHtml).join('')
    : '<div class="muted" style="padding:8px 16px">'+(todoView==='history'?'（暂无已完成）':'（无未完成待办,或被筛选）')+'</div>';
  // 增量 DOM diff:morphdom 把 #todoBox 的孩子 patch 成新 html,只动变化的节点。
  //  · getNodeKey 用 id(卡片 id=todocard-<id>)→ 卡片按身份匹配、原地更新,不重建 → 不闪、滚动位不跳。
  //  · onBeforeElUpdated:用户正在输入/选择的表单控件(textarea/select/input)直接跳过 patch →
  //    光标、未提交的下拉态、草稿绝不被刷新冲掉。其余卡内态(草稿/派给选择)已由 openEditors/roleSel 渲进 html,morphdom 自然对齐。
  morphdom(box, '<div id="todoBox">'+inner+'</div>', {
    getNodeKey: el => (el.nodeType===1 && el.id) ? el.id : null,
    onBeforeElUpdated(fromEl, toEl){
      if(fromEl===document.activeElement &&
         (fromEl.tagName==='TEXTAREA'||fromEl.tagName==='SELECT'||fromEl.tagName==='INPUT')) return false;
      return true;
    }
  });
}

async function tPost(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
async function tStatus(id,val){ const j=await tPost('/api/todos/update',{id,status:val}); if(j.ok)toast('状态已改为 '+val); else toast('改状态失败:'+(j.error||'?'),true); load(); }
async function tDelete(id){ if(!confirm('删除这条待办?')) return; const j=await tPost('/api/todos/delete',{id}); toast(j.ok?'已删除':'删除失败',!j.ok); load(); }
async function tDispatch(id){
  const to=document.getElementById('trole-'+id).value;
  if(!to){ toast('先选「派给…」要发给哪个 role',true); return; }   // 占位未选 → 不派,防误触
  // 先明确确认派活(发 bus 任务),再问可选附加说明 —— 别让随手点确定=派活
  const t=(state.todos||[]).find(x=>x.id===id);
  const hasNote=!!(t&&t.note&&t.note.trim());
  const carry=hasNote?'\n这条待办的 💬补充说明 会【自动带给】'+to+',无需重写。':'';
  if(!confirm('确定把这条待办【派活】给 '+to+'?\n会给 '+to+' 发一个 bus 任务。'+carry+'\n(只想了结不处理→点取消,改用「忽略」)')){ return; }
  // 派活额外说明 = 只填派活当下的额外话;待办常驻💬补充说明由后端自动带上,不在此重复
  const extra=prompt((hasNote?'💬补充说明已自动带上。':'')+'【派活额外说明】(可空)——只填派活当下要补的额外话,不用重复补充说明。\n点【确定】即派给 '+to+';点【取消】则不派:','');
  if(extra===null){ return; }   // 取消 = 不派
  const j=await tPost('/api/todos/dispatch',{id,to,note:extra.trim()});
  if(j.ok){delete roleSel[id];toast('✅ 已派给 '+to+(hasNote?'(补充说明已带上)':'')+(extra.trim()?'(+额外说明)':'')+' · task '+j.task_id);}else{toast('派活失败:'+(j.error||'?'),true);}
  load();
}
async function tIgnore(id){
  if(!confirm('忽略这条?将标记为【已了结】(状态置完成+已忽略),不派活、不发任何任务,移入历史。')) return;
  const j=await tPost('/api/todos/update',{id,status:'完成',ignored:true});
  toast(j.ok?'已忽略(移入历史)':'操作失败:'+(j.error||'?'),!j.ok); load();
}
// 常驻可编辑【补充说明】(note):inline 编辑,草稿存 openEditors[id](响应式)→ 刷新中天然不丢。
function tNoteEdit(id){
  const t=(state.todos||[]).find(x=>x.id===id); if(!t) return;
  openEditors[id]=t.note||'';   // 进入编辑态(草稿入 state)→ cardHtml 渲染出 textarea
  renderTodos();
  const ta=document.getElementById('ne-'+id); if(ta){ ta.focus(); try{ta.setSelectionRange(ta.value.length,ta.value.length);}catch(e){} }
}
async function tNoteSave(id){
  const val=(openEditors[id]||'').trim();
  let j; try{ j=await tPost('/api/todos/update',{id,note:val}); }catch(e){ toast('保存失败:'+e,true); return; }
  delete openEditors[id];   // 关闭编辑态
  toast(j&&j.ok?'补充说明已保存':'保存失败:'+((j&&j.error)||'?'),!(j&&j.ok));
  load();
}
function tNoteCancel(id){ delete openEditors[id]; renderTodos(); }
// 🙋待确认卡回复 → 闭环 ask-user:把答复路由回提问 agent(confirm_from)+ 标本项完成
async function tAnswer(id){
  const reply=(answerDrafts[id]||'').trim();
  if(!reply){ toast('回复内容不能为空',true); return; }
  const t=(state.todos||[]).find(x=>x.id===id); const who=t&&t.confirm_from||'提问方';
  let j; try{ j=await tPost('/api/todos/answer',{id,reply}); }catch(e){ toast('回复失败:'+e,true); return; }
  if(j&&j.ok){ delete answerDrafts[id]; toast('↩ 已回复 '+who+',答复已路由、本项标完成'); }
  else{ toast('回复失败:'+((j&&j.error)||'?'),true); }
  load();
}
function tEdit(id){
  const t=(state.todos||[]).find(x=>x.id===id); if(!t) return;
  const title=prompt('标题',t.title); if(title===null) return;
  const detail=prompt('详情',t.detail||''); if(detail===null) return;
  const source=prompt('来源',t.source||''); if(source===null) return;
  const deadline=prompt('截止(如 2026-06-23 09:00,留空清除)',t.deadline||''); if(deadline===null) return;
  const priority=prompt('优先级(高/中/低)',t.priority)||t.priority;
  tPost('/api/todos/update',{id,title,detail,source,deadline,priority}).then(j=>{toast(j.ok?'已保存':'保存失败:'+(j.error||'?'),!j.ok);load();});
}

let _loadFails=0;
async function load(role, fresh){
  const rr=role||document.getElementById('replyRole').value||'Master';
  const ctrl=new AbortController(); const tid=setTimeout(()=>ctrl.abort(),15000);  // 15s 超时,防卡死
  try{
    const res=await fetch('/api/state?reply_role='+encodeURIComponent(rr)+(fresh?'&fresh=1':''),{signal:ctrl.signal});
    if(!res.ok) throw new Error('HTTP '+res.status);
    const d=await res.json();
    if(!d || typeof d!=='object' || !d.fleet) throw new Error('payload 不完整');  // 半截/空响应不覆盖
    // 舰队还在后台子进程算(冷启/刷新中)→ 用上次的舰队数据,别闪空表(其余区照常用新数据)
    if(d.fleet.loading && state && state.fleet && !state.fleet.loading && state.fleet.roles && state.fleet.roles.length){
      d.fleet=state.fleet;
    }
    state=d; _loadFails=0; render();
    loadUserAbsence();
    const ut=document.getElementById('updT'); ut.classList.remove('flash');void ut.offsetWidth; ut.classList.add('flash');
  }catch(e){
    // 韧性:后端偶发慢/超时/半截 —— 【绝不清空页面】,保留上次 state 与已渲染 DOM,标记重试;下次轮询/SSE 自动再拉
    _loadFails++;
    console.warn('load 失败(保留上次看板,自动重试):',e&&e.message);
    const ut=document.getElementById('updT'); if(ut) ut.textContent='⚠ 刷新慢/重试中('+_loadFails+')· 显示上次数据';
    if(_loadFails<=2){ setTimeout(()=>load(role,fresh), 1500); }   // 轻量自动重试两次
  }finally{ clearTimeout(tid); }
}

function setPoll(){
  if(pollTimer){clearInterval(pollTimer);pollTimer=null;}
  const v=+document.getElementById('pollSel').value;
  if(v>0) pollTimer=setInterval(()=>load(),v);
}

// SSE — watchdog 文件事件实时推送;断了走轮询兜底
function connectSSE(){
  try{
    const es=new EventSource('/api/events');
    es.onopen=()=>setConn(true);
    es.onmessage=()=>load(null,true);   // 真事件(任务/完成)→ 强制新算,不吃缓存延迟
    es.onerror=()=>{setConn(false); es.close(); setTimeout(connectSSE,3000);};
  }catch(e){ setConn(false); }
}

async function clearOne(file){
  const role=document.getElementById('replyRole').value;
  await fetch('/api/clear_replies',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({role,file})});
  toast('已清除 '+file); load();
}

document.getElementById('refreshBtn').onclick=()=>load(null,true);
document.getElementById('pollSel').onchange=setPoll;
document.getElementById('replyRole').onchange=()=>load(document.getElementById('replyRole').value);
document.getElementById('clearAllBtn').onclick=async()=>{
  const role=document.getElementById('replyRole').value;
  if(!confirm('清空 '+role+'/done 全部回填?')) return;
  const r=await fetch('/api/clear_replies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role})});
  const j=await r.json(); toast('已清空 '+j.cleared+' 条'); load();
};
document.getElementById('sendBtn').onclick=async()=>{
  const to=document.getElementById('sendTo').value;
  const title=document.getElementById('sendTitle').value.trim();
  const task=document.getElementById('sendTask').value.trim();
  const notify=document.getElementById('sendNotify').value;
  const requires_approval=document.getElementById('sendApproval').checked;
  if(!task){toast('任务内容不能为空',true);return;}
  const btn=document.getElementById('sendBtn');btn.disabled=true;
  try{
    const r=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({to,title,task,notify,requires_approval})});
    const j=await r.json();
    if(j.ok){toast('✅ 已派给 '+to+(requires_approval?'(高风险·留 inbox 待批)':'')+' · id '+j.id);
      document.getElementById('sendTitle').value='';document.getElementById('sendTask').value='';
      document.getElementById('sendApproval').checked=false;load();}
    else toast('发送失败:'+(j.error||'?'),true);
  }catch(e){toast('发送失败:'+e,true);}
  btn.disabled=false;
};

// U3 防误派:标题/任务输入 → 防抖拉 /api/route(bus.roles.route_scores 单一源)→ 建议对口 chip,点击即选
let _routeTimer=null;
async function refreshRouteChips(){
  const box=document.getElementById('routeChips'); if(!box) return;
  const q=((document.getElementById('sendTitle').value||'')+' '+(document.getElementById('sendTask').value||'')).trim();
  if(q.length<4){ box.style.display='none'; morphInto(box,''); return; }
  try{
    const r=await fetch('/api/route?q='+encodeURIComponent(q.slice(0,400)));
    const d=await r.json();
    if(!d.ok||!(d.recs||[]).length){ box.style.display='none'; morphInto(box,''); return; }
    const mark={armed:'✓在岗',recent:'◐待确认',stale:'⚠掉线',off:'—未醒',parked:'🅿️休眠'};
    morphInto(box,'<span class="muted" style="font-size:11px">建议对口:</span>'+d.recs.map(x=>
      `<button type="button" class="cap-chip route-chip" data-key="rc-${esc(x.role)}" data-role="${esc(x.role)}" style="cursor:pointer" title="${esc(x.desc||'')} · 命中${x.score} · ${mark[x.state]||x.state}">${esc(x.role)} <span class="muted" style="font-size:10px">${mark[x.state]||''}</span></button>`).join(' '));
    box.style.display='';
  }catch(e){}
}
['sendTitle','sendTask'].forEach(id=>{const el=document.getElementById(id);
  if(el) el.addEventListener('input',()=>{clearTimeout(_routeTimer);_routeTimer=setTimeout(refreshRouteChips,600);});});
document.getElementById('routeChips').addEventListener('click',e=>{
  const c=e.target.closest('.route-chip'); if(!c) return;
  const sel=document.getElementById('sendTo'); if(sel) sel.value=c.dataset.role;
  toast('已选派给:'+c.dataset.role);
});

// 孤儿巡检(reap --dry-run)
document.getElementById('reapBtn').onclick=async()=>{
  const box=document.getElementById('reapBox');const btn=document.getElementById('reapBtn');
  btn.disabled=true;box.textContent='巡检中…';
  try{
    const r=await fetch('/api/reap');const j=await r.json();
    if(!j.ok&&j.error){box.innerHTML='<span style="color:var(--dead)">巡检失败:'+esc(j.error)+'</span>';}
    else if(!j.lines.length){box.textContent='(无孤儿任务)';}
    else{box.innerHTML='<pre style="margin:0;white-space:pre-wrap;color:var(--warn);background:var(--bg);padding:8px 10px;border-radius:6px">'+j.lines.map(esc).join('\n')+'</pre>';}
  }catch(e){box.innerHTML='<span style="color:var(--dead)">巡检失败:'+esc(e)+'</span>';}
  btn.disabled=false;
};

// daemon 自愈(supervise 一次)
document.getElementById('superviseBtn').onclick=async()=>{
  if(!confirm('立即跑一次 supervise?会重启 fleet.json 里【已死】的 daemon。')) return;
  const btn=document.getElementById('superviseBtn');btn.disabled=true;
  try{
    const r=await fetch('/api/supervise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
    const j=await r.json();
    toast((j.ok?'supervise 完成':'supervise 出错')+(j.lines&&j.lines.length?':'+j.lines.join(' / '):'')+(j.error?' ['+j.error+']':''),!j.ok);
    load();
  }catch(e){toast('supervise 失败:'+e,true);}
  btn.disabled=false;
};

// 待办池:新增 + 筛选
document.getElementById('tAddBtn').onclick=async()=>{
  const title=document.getElementById('tAddTitle').value.trim();
  if(!title){toast('标题不能为空',true);return;}
  const body={title,
    detail:document.getElementById('tAddDetail').value.trim(),
    source:document.getElementById('tAddSource').value.trim(),
    deadline:document.getElementById('tAddDeadline').value.trim(),
    priority:document.getElementById('tAddPri').value,
    status:document.getElementById('tAddStatus').value};
  const j=await tPost('/api/todos/add',body);
  if(j.ok){toast('已新增待办');
    document.getElementById('tAddTitle').value='';document.getElementById('tAddDetail').value='';
    document.getElementById('tAddSource').value='';document.getElementById('tAddDeadline').value='';
    load();}
  else toast('新增失败:'+(j.error||'?'),true);
};
document.getElementById('filtPri').onchange=renderTodos;
document.getElementById('filtStatus').onchange=renderTodos;
document.getElementById('tviewActive').onclick=()=>{todoView='active';renderTodos();};
document.getElementById('tviewHistory').onclick=()=>{todoView='history';renderTodos();};

// 实时视图 live.log
document.getElementById('liveRole').onchange=setLivePoll;
document.getElementById('liveBottom').onclick=()=>{const b=document.getElementById('liveBox');b.scrollTop=b.scrollHeight;};
"""
