#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js_panels_b —— page_js 拆分子模块 PANELS_B(自我迭代波·案①·纯搬运字节一致·2026-09-16)。"""
PANELS_B = r"""// ===== 🧩 工作组看板(使用方出口径·MasterUI出可视化)=====
async function loadGroups(){
  const box=document.getElementById('grpBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/groups'); renderGroups(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderGroups(d){
  const box=document.getElementById('grpBox'); const meta=document.getElementById('grpMeta'); if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc((d&&d.error)||'?')+'</div>'); if(meta)meta.textContent=''; return; }
  const gs=d.groups||[];
  if(meta) meta.textContent=(d.count||0)+' 个组 · 读取于 '+esc(d.as_of||'');
  if(!gs.length){ morphInto(box,'<div class="muted" style="padding:8px 16px">（暂无工作组·_groups/ 空)</div>'); return; }
  morphInto(box, gs.map(g=>{
    const exp=(g.target!=null)?('/'+esc(''+g.target)):'';
    const actCls=(g.target!=null && g.n_joined<g.target)?'warn':'live';
    const act='<span class="cap-chip '+actCls+'" title="当前在组成员数'+(g.target!=null?'/软目标target_size':'·成员运行时动态join无固定分母')+'">'+esc(''+g.n_joined)+exp+' 成员在组</span>';
    const ibCls=g.inbox_depth>0?'warn':'';
    const ib='<span class="cap-chip '+ibCls+'" title="组inbox待领深度=积压">组inbox '+esc(''+g.inbox_depth)+'</span>';
    const caps=(g.caps&&g.caps.length)?('<span style="margin-left:2px">'+g.caps.map(c=>'<span class="cap-chip">'+esc(c)+'</span>').join('')+'</span>'):'';
    const mem=(g.members&&g.members.length)?g.members.map(m=>'<span class="cap-chip">'+esc(m)+'</span>').join(''):'<span class="muted" style="font-size:11px">(无成员·未join)</span>';
    // ③④⑤ 负载/并行(使用方口径 group_metrics)
    let metricsHtml;
    const m=g.metrics;
    if(!m){ metricsHtml='<div class="muted" style="font-size:11.5px;padding:4px 0">③④⑤ 负载/并行口径未接入(group_metrics import 失败或无数据)</div>'; }
    else{
      const ld=m.members_load||[];
      const maxsh=Math.max(1,...ld.map(x=>x.share_pct||0));
      const bars=ld.length? ld.map(x=>{
        const w=Math.max(3,Math.round((x.share_pct||0)/maxsh*100));
        return '<div class="bar-row"><div class="lbl" title="'+esc(x.role)+'">'+esc(x.role)+'</div>'
          +'<div class="bar-track"><div class="bar-fill" style="width:'+w+'%"></div></div>'
          +'<div class="num">'+esc(''+x.share_pct)+'% · '+esc(''+x.claims)+'活/'+Math.round(x.busy_s/60)+'m</div></div>';
      }).join('') : '<div class="muted" style="font-size:11px">窗口内无 send_group 组路由(成员活动走直发·见舰队面板)</div>';
      const ep=m.effective_parallelism, ipr=m.intra_parallel_rate;
      const noAct=!(m.tasks_in_window>0);
      const epHtml= noAct ? '<b class="mono muted" title="窗口内无组任务·无并行可算">N/A</b>' : '<b class="mono" style="color:var(--'+(ep>=2?'live':ep>1?'warn':'dim')+')">'+esc(''+ep)+'×</b>';
      const iprHtml= noAct ? '<b class="mono muted" title="窗口内无组任务·无并行可算">N/A</b>' : '<b class="mono" style="color:var(--'+(ipr>=40?'live':ipr>0?'warn':'dim')+')">'+esc(''+ipr)+'%</b>';
      const kpis='<div style="display:flex;gap:16px;flex-wrap:wrap;margin:6px 0">'
        +'<span title="Σ成员busy/≥1busy墙钟;=1串行,→成员数理想">'+epHtml+' <span class="muted" style="font-size:11px">有效并行系数</span></span>'
        +'<span title="≥2成员同时busy占≥1busy时长">'+iprHtml+' <span class="muted" style="font-size:11px">组内并行率</span></span>'
        +'<span class="muted" style="font-size:11px">窗口内组任务 '+esc(''+(m.tasks_in_window||0))+'(done '+esc(''+(m.done_in_window||0))+')· 活跃墙钟 '+Math.round((m.active_wall_s||0)/60)+'m</span></div>';
      metricsHtml=kpis+'<div class="bars">'+bars+'</div>';
    }
    return '<div class="tl-card" data-key="grp-'+esc(g.group)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:var(--accent)">🧩 '+esc(g.group)+'</b>'+act+ib
      +'<span class="spacer" style="flex:1"></span><span class="muted" style="font-size:11px" title="成员名单最后变更时间(members.json·非数据新鲜度)">成员更新于 '+esc(g.updated||'')+'</span></div>'
      +'<div style="margin-top:6px;font-size:12px"><b class="muted">成员:</b> '+mem+' <b class="muted" style="margin-left:6px">caps:</b> '+caps+'</div>'
      +'<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">③负载分布 / ④有效并行 / ⑤组内并行率(使用方口径·仅组任务·24h窗):</b>'+metricsHtml+'</div>'
      +'</div>';
  }).join(''));
}

// ===== 🔧 机制注册表(使用方出数据·MasterUI render;机制≠能力池·related_cap交叉链跳能力池)=====
{const _mr=document.getElementById('mechRefresh'); if(_mr) _mr.onclick=()=>loadMechanisms();}
const _MECH_STCLS={'在用':'idle','新上':'live','计划':'warn','退役':'none'};
async function loadMechanisms(){
  const box=document.getElementById('mechBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/mechanisms'); renderMechanisms(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderMechanisms(d){
  const box=document.getElementById('mechBox'); const meta=document.getElementById('mechMeta'); if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc((d&&d.error)||'?')+'</div>'); if(meta)meta.textContent=''; return; }
  const cats=d.categories||[], m=d.meta||{};
  if(meta) meta.textContent=(d.n_mech||0)+' 机制 / '+(d.n_cats||0)+' 类 · 读取于 '+esc(d.as_of||'');
  // 顶部:purpose + 机制≠caps 区分 + 状态图例
  const leg=m.status_legend||{};
  const legHtml=Object.keys(leg).map(k=>'<span class="cap-chip '+(_MECH_STCLS[k]||'')+'" title="'+esc(leg[k])+'">'+esc(k)+'</span>').join('');
  let head='<div style="padding:2px 16px 8px">'
    +(m.purpose?'<div class="muted" style="font-size:12px">'+esc(m.purpose)+'</div>':'')
    +(m.distinction_vs_caps?'<div style="font-size:11.5px;margin-top:4px;background:rgba(91,157,255,.06);border-left:2px solid var(--accent);padding:4px 8px;border-radius:4px">🔧机制 vs 🧬caps:'+esc(m.distinction_vs_caps)+'</div>':'')
    +'<div style="margin-top:6px">状态图例:'+legHtml+' <span class="muted" style="font-size:11px">· 点 related_cap 标签跳🧬能力池</span></div></div>';
  morphInto(box, head+cats.map(c=>{
    const relCap=c.related_cap||'';
    const capChip=(relCap && relCap.charAt(0)!=='(')
      ? '<span class="cap-chip" style="cursor:pointer" title="跳能力池看此cap" onclick="event.stopPropagation();showTab(\'caps\')">🧬'+esc(relCap)+'</span>'
      : '<span class="muted" style="font-size:11px">'+esc(relCap)+'</span>';
    const sd=c.status_dist||{};
    const sdChips=Object.keys(sd).map(k=>'<span class="cap-chip '+(_MECH_STCLS[k]||'')+'" style="font-size:10px">'+esc(k)+' '+sd[k]+'</span>').join('');
    const rows=(c.mechanisms||[]).map(x=>{
      const st='<span class="cap-chip '+(_MECH_STCLS[x.status]||'')+'" style="font-size:10px">'+esc(x.status||'?')+'</span>';
      return '<tr><td style="font-weight:600">'+esc(x.name)+' '+st+'</td>'
        +'<td style="font-size:12px">'+esc(x.does||'')+'</td>'
        +'<td class="mono muted" style="font-size:11px">'+esc(x.carrier||'')+'</td></tr>';
    }).join('');
    return '<div class="tl-card" data-key="mech-'+esc(''+c.id)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:0">'
      +'<div style="cursor:pointer;display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 14px" onclick="var b=this.parentNode.querySelector(\'.mech-body\');b.style.display=b.style.display===\'none\'?\'block\':\'none\';">'
      +'<b>'+esc(''+c.id)+'. '+esc(c.name)+'</b><span class="cap-chip" style="background:rgba(91,157,255,.14);color:var(--accent)">'+c.count+' 机制</span>'+capChip
      +'<span class="spacer" style="flex:1"></span>'+sdChips+'<span class="muted" style="font-size:11px">▸展开</span></div>'
      +'<div class="mech-body" style="display:none;padding:0 14px 10px;overflow:auto">'
      +'<table style="width:100%"><thead><tr><th>机制</th><th>作用</th><th>载体</th></tr></thead><tbody>'+rows+'</tbody></table></div>'
      +'</div>';
  }).join(''));
}

// ===== 👤 角色注册表(纯静态文档型·读 role_profiles.json·MasterAgent 单一真相源·MasterUI render)=====
{const _rr=document.getElementById('rolesRefresh'); if(_rr) _rr.onclick=()=>loadRoles();}
async function loadRoles(){
  const box=document.getElementById('rolesBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/role-profiles'); renderRoles(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderRoles(d){
  const box=document.getElementById('rolesBox'); const meta=document.getElementById('rolesMeta'); if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc((d&&d.error)||'?')+'</div>'); if(meta)meta.textContent=''; return; }
  const roles=d.roles||[], m=d.meta||{};
  const cj=d.compact||{}, cby=(cj.by_role)||{};   // compact hook 注入(单一源·reinject_post_compact 导出)
  if(meta) meta.textContent=(d.n_roles||0)+' 角色 · v'+esc(m.version||'?')+' · 更新 '+esc(m.updated||'')+(m.owner?(' · owner '+esc(m.owner)):'');
  const head=(m.note?'<div class="muted" style="font-size:12px;padding:2px 16px 8px">'+esc(m.note)+(m.scope?(' · 范围:'+esc(m.scope)):'')+'</div>':'');
  // 全局层(compact 后所有角色共用·展示一次)——meta.common_rules verbatim,{role} 占位保留为模板样;hook 不可读→降级提示不塌页
  let glob='';
  if(cj.ok===false){
    glob+='<div style="margin:8px 16px;padding:8px 12px;border:1px solid var(--warn);border-radius:8px;background:rgba(245,166,35,.10);font-size:12px"><b class="muted">⚠ compact hook 模块不可读:</b>'+esc(cj.error||'?')+'<span class="muted">(仅 compact 注入区块降级·注册表展示不受影响)</span></div>';
  }
  const cr=m.common_rules||[];
  if(cr.length){
    glob+='<div class="fold" style="margin:8px 16px;padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:var(--panel2)">'
      +'<div class="fold-head" style="font-weight:600;color:var(--accent);font-size:13px"><span class="fold-arrow">▶</span> 🌐 全局层 · compact 后所有角色共用 <span class="muted" style="font-weight:400;font-size:11px">('+cr.length+' 条铁律·点开看)</span></div>'
      +'<div class="fold-body">'
      +'<div class="muted" style="font-size:11px;margin:2px 0 6px">'+esc(m.common_rules_note||'compact hook 全局通用操作铁律;{role} 为模板占位(实注时按角色插值)')+'</div>'
      +'<ol style="margin:0;padding-left:18px;font-size:12px;line-height:1.6">'+cr.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ol></div></div>';
  }
  const col=(title,items,ic)=>'<div style="flex:1;min-width:150px"><div class="muted" style="font-size:11px;font-weight:600;margin-bottom:3px">'+ic+' '+title+'</div>'
    +((items&&items.length)?('<ol style="margin:0;padding-left:16px;font-size:11.5px;line-height:1.55">'+items.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ol>')
      :'<div class="muted" style="font-size:11px">—</div>')+'</div>';
  const normalRoles=roles.filter(r=>!r._abstract);
  const abstractRoles=roles.filter(r=>r._abstract);
  const abstractCards=abstractRoles.map(r=>'<div class="tl-card" data-key="role-abs-'+esc(r.role)+'" style="border:1px solid var(--accent2);border-radius:8px;margin:8px 16px;padding:10px 14px;cursor:pointer;background:rgba(91,157,255,.06)" onclick="showTab(\'bizroles\')">'
    +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:var(--accent);font-size:14px">🏢 '+esc(r.role)+'</b>'
    +'<span class="cap-chip" style="background:rgba(91,157,255,.14);color:var(--accent)">'+esc(r.layer||'')+'</span>'
    +'<span class="badge live" style="font-size:10px">→ 业务角色页</span></div>'
    +(r.one_liner?'<div style="font-size:12.5px;margin-top:4px">'+esc(r.one_liner)+'</div>':'')
    +'<div class="muted" style="font-size:11px;margin-top:5px">点击跳转 <b>🏢 业务角色</b> 页查看具体业务角色档案 (role_profiles_biz.json)</div>'
    +'</div>').join('');
  morphInto(box, head+glob+normalRoles.map(r=>{
    const caps=(r.caps||[]).map(c=>'<span class="cap-chip">'+esc(c)+'</span>').join('');
    // compact 注入区块:角色层(render_role)+ 完整注入折叠(build_context·WYSIWYG=hook 实注同一串)
    const ci=cby[r.role]||{}; let cblock='';
    if(cj.ok!==false){
      if(ci.error){
        cblock='<div class="muted" style="font-size:11px;margin-top:8px">⚠ 本角色 compact 注入产出失败:'+esc(ci.error)+'</div>';
      }else{
        const rl=ci.role_layer;
        const rlHtml=rl
          ? '<div style="font-size:11.5px"><b class="muted">one_liner:</b> '+esc(rl.one_liner||'')+'</div><div style="font-size:11.5px;margin-top:2px"><b class="muted">boundary:</b> '+esc(rl.boundary||'')+'</div>'
          : '<div class="muted" style="font-size:11.5px">render_role 返 None · 未注册回落态(compact 时回落「读你的角色恢复锚记忆」)</div>';
        cblock='<div style="margin-top:8px;border-top:1px dashed var(--line);padding-top:6px">'
          +'<div class="muted" style="font-size:11px;font-weight:600">🧩 角色层(render_role · compact 注入)</div>'+rlHtml
          +(ci.full?('<details style="margin-top:6px"><summary style="cursor:pointer;font-size:11.5px;color:var(--accent)">▸ 查看完整 compact 注入(build_context · 所见即所注)</summary>'
             +'<pre style="white-space:pre-wrap;word-break:break-word;font:11.5px/1.5 Consolas,monospace;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:6px 0 0;max-height:50vh;overflow:auto">'+esc(ci.full)+'</pre></details>'):'')
          +'</div>';
      }
    }
    return '<div class="tl-card fold" data-key="role-'+esc(r.role)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div class="fold-head" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="fold-arrow">▶</span><b style="color:var(--accent);font-size:14px">👤 '+esc(r.role)+'</b>'
      +'<span class="cap-chip" style="background:rgba(91,157,255,.14);color:var(--accent)">'+esc(r.layer||'')+'</span></div>'
      +(r.one_liner?'<div style="font-size:12.5px;margin-top:4px">'+esc(r.one_liner)+'</div>':'')
      +'<div class="fold-body">'
      +'<div class="muted" style="font-size:11px;margin-top:5px">cwd <span class="mono">'+esc(r.cwd||'?')+'</span> · mode '+esc(r.mode||'?')+' · caps '+(caps||'—')+'</div>'
      +'<div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:8px">'
      +col('启动流程',r.startup_flow,'🚀')
      +col('明式输入(显式派)',r.explicit_inputs,'📥')
      +col('隐式输入(自举读锚)',r.implicit_inputs,'🧭')+'</div>'
      +(r.boundary?'<div style="font-size:11.5px;margin-top:8px;background:rgba(245,166,35,.08);border-left:2px solid var(--warn);padding:4px 8px;border-radius:4px"><b class="muted">边界:</b>'+esc(r.boundary)+'</div>':'')
      +cblock
      +'</div>'
      +'</div>';
  }).join('')+abstractCards);
}

// ===== ⏱ 角色全生命周期总览(懒加载·点展开) =====
let _lcLoaded=false;
function toggleLifecycle(){
  const box=document.getElementById('lcBox');
  const arr=document.getElementById('lcArrow');
  const open=box.style.display!=='none';
  box.style.display=open?'none':'';
  arr.textContent=open?'▸':'▾';
  if(!open&&!_lcLoaded) loadLifecycle();
}
async function loadLifecycle(){
  const box=document.getElementById('lcBox');
  if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/lifecycle'); renderLifecycle(await r.json()); _lcLoaded=true; }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderLifecycle(d){
  const box=document.getElementById('lcBox'); const meta=document.getElementById('lcMeta');
  if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败</div>'); return; }
  if(meta) meta.textContent='共 '+d.n_active+' 在册 · '+d.n_retired+' 退役';
  const STAGE={active:'<span class="badge live">✓ 在册</span>',boot_pending:'<span class="badge warn">⚠ 装载中</span>',retired:'<span class="badge dead">🗃 退役</span>',revived:'<span class="badge idle">🔄 复活</span>'};
  const rows=(d.rows||[]).map(r=>{
    const stage=STAGE[r.stage]||('<span class="badge idle">'+esc(r.stage)+'</span>');
    const reg=r.registered?r.registered.slice(0,10):'—';
    const ret=r.retired_at?r.retired_at.slice(0,10):'—';
    const succ=r.succeeded_by?'→ '+esc(r.succeeded_by):'—';
    const logBtn=(r.stage==='retired'||r.stage==='revived')
      ?'<button class="btn ghost mini" style="font-size:10px;padding:1px 6px;margin:0" onclick="showTab(\'fleetlog\')">查看日志</button>':'';
    return '<tr style="border-bottom:1px solid var(--line)">'
      +'<td style="padding:4px 16px;font-weight:600;white-space:nowrap">'+esc(r.role)+'</td>'
      +'<td style="padding:4px 8px">'+stage+'</td>'
      +'<td class="mono" style="padding:4px 8px;font-size:11px;color:var(--dim)">'+reg+'</td>'
      +'<td class="mono" style="padding:4px 8px;font-size:11px;color:var(--dim)">'+ret+'</td>'
      +'<td style="padding:4px 8px;font-size:11.5px;color:var(--dim)">'+succ+'</td>'
      +'<td style="padding:4px 8px">'+logBtn+'</td>'
      +'</tr>';
  }).join('');
  morphInto(box,'<table style="width:100%;border-collapse:collapse">'
    +'<thead><tr style="font-size:11px;color:var(--dim);border-bottom:1px solid var(--line)">'
    +'<th style="padding:3px 16px;text-align:left;font-weight:600">角色</th>'
    +'<th style="padding:3px 8px;text-align:left;font-weight:600">阶段</th>'
    +'<th style="padding:3px 8px;text-align:left;font-weight:600">注册</th>'
    +'<th style="padding:3px 8px;text-align:left;font-weight:600">退役</th>'
    +'<th style="padding:3px 8px;text-align:left;font-weight:600">接替</th>'
    +'<th style="padding:3px 8px"></th>'
    +'</tr></thead><tbody>'+rows+'</tbody></table>');
}

// ===== 🏢 业务角色(读 role_profiles_biz.json·业务数据不进 git)=====
{const _br=document.getElementById('bizRolesRefresh'); if(_br) _br.onclick=()=>loadBizRoles();}
async function loadBizRoles(){
  const box=document.getElementById('bizRolesBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/role-profiles-biz'); renderBizRoles(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
function renderBizRoles(d){
  const box=document.getElementById('bizRolesBox'); const meta=document.getElementById('bizRolesMeta'); if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc((d&&d.error)||'?')+'</div>'); if(meta)meta.textContent=''; return; }
  if(d.missing){ morphInto(box,'<div class="muted" style="padding:12px 16px">无业务建档 (role_profiles_biz.json 不存在)</div>'); if(meta)meta.textContent=''; return; }
  const roles=d.roles||[], m=d.meta||{};
  if(meta) meta.textContent=(d.n_roles||0)+' 个业务角色 · v'+esc(m.version||'?')+' · 更新 '+esc(m.updated||'');
  const col=(title,items,ic)=>'<div style="flex:1;min-width:150px"><div class="muted" style="font-size:11px;font-weight:600;margin-bottom:3px">'+ic+' '+title+'</div>'
    +((items&&items.length)?('<ol style="margin:0;padding-left:16px;font-size:11.5px;line-height:1.55">'+items.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ol>')
      :'<div class="muted" style="font-size:11px">—</div>')+'</div>';
  morphInto(box, roles.map(r=>{
    const caps=(r.caps||[]).map(c=>'<span class="cap-chip">'+esc(c)+'</span>').join('');
    return '<div class="tl-card fold" data-key="bizrole-'+esc(r.role)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div class="fold-head" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="fold-arrow">▶</span><b style="color:var(--accent);font-size:14px">🏢 '+esc(r.role)+'</b>'
      +'<span class="cap-chip" style="background:rgba(91,157,255,.14);color:var(--accent)">'+esc(r.layer||'')+'</span></div>'
      +(r.one_liner?'<div style="font-size:12.5px;margin-top:4px">'+esc(r.one_liner)+'</div>':'')
      +'<div class="fold-body">'
      +'<div class="muted" style="font-size:11px;margin-top:5px">cwd <span class="mono">'+esc(r.cwd||'?')+'</span> · mode '+esc(r.mode||'?')+' · caps '+(caps||'—')+'</div>'
      +'<div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:8px">'
      +col('启动流程',r.startup_flow,'🚀')
      +col('明式输入(显式派)',r.explicit_inputs,'📥')
      +col('隐式输入(自举读锚)',r.implicit_inputs,'🧭')+'</div>'
      +(r.boundary?'<div style="font-size:11.5px;margin-top:8px;background:rgba(245,166,35,.08);border-left:2px solid var(--warn);padding:4px 8px;border-radius:4px"><b class="muted">边界:</b>'+esc(r.boundary)+'</div>':'')
      +'</div>'
      +'</div>';
  }).join('')||'<div class="muted" style="padding:8px 16px">（无业务角色）</div>');
}

// ===== 🗂️ 长程任务·锚定生长网(P0:脊柱+当前段+进度·使用方简报/MasterAgent数据/MasterUI render)=====
{const _lr=document.getElementById('lrRefresh'); if(_lr) _lr.onclick=()=>loadLongrange();}
// 节点状态→CSS(MasterAgent longrange_v0 口径:pending灰/ready浅蓝/running蓝/blocked红/await_decision黄/done绿/failed深红)
const _LR_NODE={pending:'idle',ready:'accent',running:'accent',blocked:'dead',await_decision:'warn',done:'live',failed:'dead'};
// 锚点状态→CSS(upcoming灰/active蓝/reached绿)
const _LR_ANCH={upcoming:'idle',active:'accent',reached:'live'};
async function loadLongrange(){
  const box=document.getElementById('lrBox'); if(box&&!box.innerHTML) box.innerHTML='<div class="muted" style="padding:8px 16px">读取中…</div>';
  try{ const r=await fetch('/api/longrange'); renderLongrange(await r.json()); }
  catch(e){ if(box) box.innerHTML='<div class="muted" style="padding:8px 16px">读取失败:'+esc(e)+'</div>'; }
}
// P1 ③ 活跃段 fork/merge DAG:按拓扑深度分层成列,标注分叉(出度>1)/合并(入度>1)节点。
//   edges=[{from,to}](节点 id);无边→退回扁平一列(即 P0 行为)。纯 JS 计算,不依赖 MasterAgent 预排版。
function _lrLayout(nodes,edges){
  const byId={}; nodes.forEach(n=>byId[n.id]=n);
  const indeg={}, outdeg={}, succ={}, pred={};
  nodes.forEach(n=>{indeg[n.id]=0;outdeg[n.id]=0;succ[n.id]=[];pred[n.id]=[];});
  (edges||[]).forEach(e=>{ if(byId[e.from]&&byId[e.to]){ outdeg[e.from]++; indeg[e.to]++; succ[e.from].push(e.to); pred[e.to].push(e.from);} });
  // 最长路径深度(拓扑)——环保护:迭代上限
  const depth={}; nodes.forEach(n=>depth[n.id]=0);
  for(let it=0;it<nodes.length+2;it++){ let ch=false;
    (edges||[]).forEach(e=>{ if(byId[e.from]&&byId[e.to]&&depth[e.to]<depth[e.from]+1){depth[e.to]=depth[e.from]+1;ch=true;} });
    if(!ch) break; }
  const cols={}; nodes.forEach(n=>{const dcol=depth[n.id]||0;(cols[dcol]=cols[dcol]||[]).push(n);});
  return {cols,indeg,outdeg,maxDepth:Math.max(0,...nodes.map(n=>depth[n.id]||0))};
}
// P1 ④ 复杂度成长曲线:series=[{t,size}] → 内联 SVG sparkline(净规模随时间)。无 series→null。
function _lrSpark(series){
  if(!series||series.length<2) return '';
  const W=180,H=30,pad=2; const xs=series.map((_,i)=>i), ys=series.map(p=>+p.size||0);
  const ymin=Math.min(...ys), ymax=Math.max(...ys), yr=(ymax-ymin)||1, xr=(xs.length-1)||1;
  const pts=series.map((p,i)=>(pad+i/xr*(W-2*pad)).toFixed(1)+','+(H-pad-((+p.size||0)-ymin)/yr*(H-2*pad)).toFixed(1)).join(' ');
  const last=ys[ys.length-1], first=ys[0], up=last>=first;
  return '<svg width="'+W+'" height="'+H+'" style="vertical-align:middle"><polyline points="'+pts+'" fill="none" stroke="'+(up?'#5b9dff':'#f5a623')+'" stroke-width="1.5"/></svg>'
    +'<span class="mono" style="font-size:11px;margin-left:6px">'+first+'→'+last+' ('+(up?'+':'')+(last-first)+')</span>';
}
// ★P1 决策合批·交互写回(MasterAgent 口径:approve→ready执行 / reject→failed / 未答默认reject)。
window._lrDec=window._lrDec||{};
function lrPick(tid,node,act){
  _lrDec[tid+'::'+node]=act;
  document.querySelectorAll('button[data-dec="'+CSS.escape(tid+'::'+node)+'"]').forEach(b=>{
    const on=b.dataset.act===act;
    b.style.background=on?(act==='approve'?'rgba(62,207,142,.28)':'rgba(255,93,93,.28)'):'transparent';
    b.style.fontWeight=on?'700':'400';
  });
  const c=document.getElementById('lrsub-'+CSS.escape(tid)); if(c){ const ns=JSON.parse(c.dataset.nodes||'[]'); c.textContent='✅ 一次批完('+ns.filter(n=>_lrDec[tid+'::'+n]).length+'/'+ns.length+' 已选·未选按reject)'; }
}
async function lrSubmit(tid){
  const c=document.getElementById('lrsub-'+CSS.escape(tid)); const nodes=JSON.parse((c&&c.dataset.nodes)||'[]');
  const ap={}; nodes.forEach(n=>{ ap[n]=_lrDec[tid+'::'+n]||'reject'; });
  const nAns=nodes.filter(n=>_lrDec[tid+'::'+n]).length;
  if(!window.confirm('提交本批 '+nodes.length+' 条裁决?\n已选 '+nAns+' 条,未选的按 reject 处理(保守·宁卡不误做不可逆)。\napprove→节点交执行(可能含不可逆DDL),reject→标 failed。')) return;
  if(c){ c.disabled=true; c.textContent='提交中…'; }
  try{
    const r=await fetch('/api/lr-decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:tid,approvals:ap})});
    const j=await r.json();
    if(!j.ok){ alert('写回失败:'+esc(j.error||'?')); if(c){c.disabled=false;} return; }
    nodes.forEach(n=>delete _lrDec[tid+'::'+n]); loadLongrange();
  }catch(e){ alert('写回异常:'+esc(''+e)); if(c){c.disabled=false;} }
}
function renderLongrange(d){
  const box=document.getElementById('lrBox'); const meta=document.getElementById('lrMeta'); if(!box) return;
  if(!d||!d.ok){ morphInto(box,'<div class="muted" style="padding:8px 16px">读取失败:'+esc((d&&d.error)||'?')+'</div>'); if(meta)meta.textContent=''; return; }
  if(d.pending){ if(meta)meta.textContent='P0·待数据'; morphInto(box,'<div class="muted" style="padding:10px 16px">🗂️ P0 骨架已就位。'+esc(d.note||'')+'</div>'); return; }
  const ts=d.tasks||[];
  if(meta) meta.textContent=(d.count||0)+' 条 · schema '+esc(d.schema||'')+' · 更新 '+esc(d.updated||'');
  if(!ts.length){ morphInto(box,'<div class="muted" style="padding:8px 16px">（暂无长程任务)</div>'); return; }
  // ★P1 决策合批单(置顶·跨任务聚合):当前待人拍板的点聚成一张单,一处看。空=当前无待拍(诚实)。
  const pd=d.pending_decisions||[]; const nb=(d.decision_batches||[]).length;
  const _AUTt={auto:'<span style="color:var(--live)">●auto</span>',notify:'<span style="color:var(--accent)">●notify</span>',ask:'<span style="color:var(--warn)">🔒ask</span>'};
  const _PCt=pc=>{if(!pc||!pc.status)return '';const m={pass:'✓预检pass',pending:'…预检pending',fail:'⚠预检fail'};return ' <span class="muted" style="font-size:10px" title="'+((pc.findings&&pc.findings.length)?esc(pc.findings.join(' / ')):'')+'">'+(m[pc.status]||esc(pc.status))+((pc.status==='fail'&&pc.findings&&pc.findings.length)?(':'+esc(pc.findings[0])):'')+'</span>';};
  const decSrc=nb?('MasterAgent 权威 pending_decision_batch × '+nb+' 单'):(d.decisions_derived?'MasterUI 派生(锚闸active+await_decision)':'MasterAgent/使用方 emit');
  const nbatch=d.decision_batches||[];
  // 决策行(带交互按钮 or 只读):inter=true 时接 lrPick/lrSubmit 写回;false=派生只读
  const decRow=(p,inter,tid,node)=>'<div class="lr-node warn" style="white-space:normal;display:block;padding:6px 10px">'
    +'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>'+(p.kind==='anchor-gate'?'⚑里程碑闸':'●节点')+'</b> '+esc(p.label||'')
    +(p.autonomy?(' '+(_AUTt[p.autonomy]||esc(p.autonomy))):'')+_PCt(p.precheck)
    +(p.task_title?(' <span class="muted" style="font-size:10px">@ '+esc(p.task_title)+'</span>'):'')+(p.owner?(' <span class="muted" style="font-size:10px">owner '+esc(p.owner)+'</span>'):'')
    +'<span class="spacer" style="flex:1"></span>'
    +(inter?(
        '<button data-dec="'+esc(tid+'::'+node)+'" data-act="approve" onclick="lrPick(\''+esc(tid)+'\',\''+esc(node)+'\',\'approve\')" title="批准→节点交执行" style="font-size:10px;padding:1px 8px;border:1px solid var(--live);color:var(--live);background:transparent;border-radius:4px;cursor:pointer">✓批</button>'
       +'<button data-dec="'+esc(tid+'::'+node)+'" data-act="reject" onclick="lrPick(\''+esc(tid)+'\',\''+esc(node)+'\',\'reject\')" title="驳回→标 failed" style="font-size:10px;padding:1px 8px;border:1px solid var(--dead);color:var(--dead);background:transparent;border-radius:4px;cursor:pointer">✗驳</button>'
      ):'<span class="muted" style="font-size:9px">(派生·只读·无batch写回)</span>')
    +'</div>'+(p.question?('<div class="muted" style="font-size:11px;margin-top:2px">验收:'+esc(p.question)+'</div>'):'')+'</div>';
  let decBody;
  if(nbatch.length){
    // MasterAgent 权威:每任务一张 pending_decision_batch → 交互写回(一次批完)
    decBody=nbatch.map(b=>{
      const ds=b.decisions||[]; const nodeIds=ds.map(dc=>dc.node||dc.label||'');
      return '<div style="margin-top:8px;border-top:1px dashed var(--line);padding-top:6px">'
        +'<div style="font-size:11px" class="muted">📋 <b>'+esc(b.task_title||b.task_id||'')+'</b> · batch '+esc(b.batch_id||'')+(b.segment!=null?(' · 段'+esc(''+b.segment)):'')+'</div>'
        +'<div style="margin-top:6px;display:flex;flex-direction:column;gap:6px">'+ds.map(dc=>decRow(dc,true,b.task_id,dc.node||dc.label||'')).join('')+'</div>'
        +'<button id="lrsub-'+esc(b.task_id)+'" data-nodes=\''+esc(JSON.stringify(nodeIds))+'\' onclick="lrSubmit(\''+esc(b.task_id)+'\')" style="margin-top:8px;font-size:11px;padding:4px 14px;border:1px solid var(--warn);color:var(--warn);background:rgba(245,166,35,.1);border-radius:5px;cursor:pointer;font-weight:700">✅ 一次批完(0/'+ds.length+' 已选·未选按reject)</button>'
        +'</div>';
    }).join('');
  } else if(pd.length){
    decBody='<div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">'+pd.map(p=>decRow(p,false)).join('')+'</div>';
  } else {
    decBody='<div class="muted" style="font-size:11.5px;margin-top:6px">当前无待拍板点 —— 无 pending_decision_batch、无 active 里程碑闸/await_decision 节点。(空=诚实,不是漏读)</div>';
  }
  const decCard='<div class="tl-card" data-key="lr-decisions" style="border:1px solid '+(pd.length?'var(--warn)':'var(--line)')+';border-radius:8px;margin:8px 16px;padding:10px 14px;'+(pd.length?'background:rgba(245,166,35,.06)':'')+'">'
    +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:'+(pd.length?'var(--warn)':'var(--dim)')+'">⚖ 决策合批单</b>'
    +'<span class="cap-chip '+(pd.length?'warn':'idle')+'" style="font-size:10px">'+pd.length+' 待拍</span>'
    +'<span class="muted" style="font-size:10px">'+decSrc+' · 一处看一次批不零售'+(nbatch.length?' · approve→执行/reject→failed/未答默认reject(保守)':' · 派生视图·拍板走总线')+'</span></div>'
    +decBody
    +'</div>';
  morphInto(box, decCard + ts.map(t=>{
    // ① 锚点脊柱:起→中…→终;author(👤user实心/🤖machine虚框)× status色(upcoming灰/active蓝/reached绿)
    const sp=(t.spine||[]).map((a,i)=>{
      const isU=a.author==='user';
      const stc=_LR_ANCH[a.status]||'idle';
      const gate=a.decision_gate?' <span title="决策闸·人拍板">⚖</span>':'';
      const tp={start:'起',mid:'中',end:'终'}[a.type]||'·';
      const stTag=a.status?('<span class="lr-node '+stc+'" style="font-size:9px;padding:0 5px">'+esc(a.status)+'</span>'):'';
      return (i>0?'<span class="lr-arrow">→</span>':'')
        +'<span class="lr-anchor '+(isU?'u':'m')+'" title="'+esc(a.author||'')+' 锚点'+(a.acceptance?(' · 验收:'+esc(a.acceptance)):'')+'">'
        +'<span><b>'+tp+'</b> '+esc(a.anchor||'')+gate+' '+stTag+'</span><span class="lr-au">'+(isU?'👤user':'🤖machine')+'</span></span>';
    }).join('');
    // ② 当前活跃段:节点状态色 + blocked-on + verdict + author + fork/merge 标注
    const seg=t.active_segment||{};
    const segNodes=seg.nodes||[]; const lay=_lrLayout(segNodes,seg.edges);
    const nodeHtml=n=>{
      const cls=_LR_NODE[n.status]||'idle';
      const bo=(n.blocked_on&&n.blocked_on.length)?' <span class="muted" style="font-size:10px">⛔'+n.blocked_on.map(esc).join(','):'';
      const bc=(n.blocked_on&&n.blocked_on.length)?'</span>':'';
      const vd=n.verdict?' <span title="验收结论" style="font-size:10px">✓'+esc(n.verdict)+'</span>':'';
      const au=n.author==='machine'?' 🤖':'';
      const fk=(lay.outdeg[n.id]>1)?' <span title="分叉 fork" style="font-size:10px">⑃</span>':'';
      const mg=(lay.indeg[n.id]>1)?' <span title="合并 merge" style="font-size:10px">⑂</span>':'';
      // P1 自主度徽标:auto●绿(自动跑)/notify●蓝(跑完知会)/ask🔒黄(要人拍)
      const AUT={auto:'<span style="color:var(--live)" title="auto·自动执行">●</span>',notify:'<span style="color:var(--accent)" title="notify·执行后知会">●</span>',ask:'<span style="color:var(--warn)" title="ask·须人拍板">🔒</span>'};
      const aut=n.autonomy?(' '+(AUT[n.autonomy]||esc(n.autonomy))):'';
      // P1 预检:pass✓/pending…/fail⚠(fail 时节点已 blocked·hover 看 findings 为啥拦)
      const pc=n.precheck||{}; const PCE={pass:'✓',pending:'…',fail:'⚠'};
      const pcHtml=pc.status?(' <span title="预检:'+esc(pc.status)+((pc.findings&&pc.findings.length)?(' — '+pc.findings.map(esc).join(' / ')):'')+'" style="font-size:10px">'+(PCE[pc.status]||'')+'</span>'):'';
      return '<span class="lr-node '+cls+'" title="'+esc(n.owner||'无owner')+(n.acceptance?(' · 验收:'+esc(n.acceptance)):'')+'"><b>'+esc(n.status||'?')+'</b> '+esc(n.label||n.id||'')+au+aut+pcHtml+fk+mg+bo+bc+vd+'</span>';
    };
    let nodes;
    if((seg.edges||[]).length){
      // 有边→分层 DAG:每列=同拓扑深度,列间以 ⇒ 分隔,天然显分叉/合并
      const colKeys=Object.keys(lay.cols).map(Number).sort((a,b)=>a-b);
      nodes='<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
        +colKeys.map(c=>'<div style="display:flex;flex-direction:column;gap:5px">'+lay.cols[c].map(nodeHtml).join('')+'</div>').join('<span class="lr-arrow">⇒</span>')
        +'</div>';
    } else {
      nodes=segNodes.map(nodeHtml).join('')||'<span class="muted" style="font-size:11px">（当前段无活节点)</span>';
    }
    // 塌缩归档段(简报核心:达锚→塌缩归档不占活内存)
    const arch=(t.archived_segments||[]).map(s=>'<span class="lr-node idle" title="'+esc(s.summary||'')+'" style="opacity:.7">📦段'+esc(''+s.seg_index)+' 塌缩('+((s.collapsed&&s.collapsed.nodes)||s.node_count||0)+'节点)→'+esc(s.to_anchor||'')+'</span>').join('');
    // ④ 进度%(分母生长/可倒退=诚实,不报警)
    const pg=t.progress||{}; const pct=(pg.pct!=null)?pg.pct:0;
    const pbar='<div class="bar-track" style="flex:1"><div class="bar-fill" style="width:'+Math.max(2,Math.min(100,pct))+'%"></div></div>';
    const gw=pg.growable?' <span class="muted" style="font-size:10px" title="分母会生长/可倒退=诚实信号非bug">📈可生长</span>':'';
    // ★P1 价值徽标:统一读 task.efficiency(使用方 confirmation_savings_from_events 原始输出·单一源·MasterAgent 每 task 实时刷)。
    //   saved=confirmation_turns_saved;breakdown{batch:roundtrips_saved_by_batching, precheck:precheck_fail_avoided}。saved>0 才显(0=无价值·不显噪音)。
    const _ef=t.efficiency||{}; let savedBadge=''; const _sv=+_ef.confirmation_turns_saved||0;
    if(_sv>0){ const _b=+_ef.roundtrips_saved_by_batching||0, _p=+_ef.precheck_fail_avoided||0;
      savedBadge='<span class="cap-chip live" style="font-size:10px" title="'+esc(_ef.note||'合批+precheck 少打断用户的确认次数(使用方 events 口径)')+'">💡已省 '+_sv+' 次确认 (batch'+_b+'+precheck'+_p+')</span>'; }
    // ★P1 ⑤ 三角色(拆解者/执行者/跟踪者):读 t.roles;缺→—(诚实,不编)
    const rl=t.roles||{};
    const roleChip=(k,lab)=>'<span class="cap-chip '+(rl[k]?'live':'none')+'" style="font-size:10px" title="'+lab+'">'+lab+':'+esc(rl[k]||'—')+'</span>';
    const rolesHtml='<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">⑤三角色:</b>'
      +roleChip('decomposer','拆解者')+roleChip('executor','执行者')+roleChip('tracker','跟踪者')+'</div>';
    // ★P1 🔭 跟踪者活动面板(MasterAgent task.tracker_status·null=未指派·不编造):谁在盯/盯啥/待督办/最近督办动作。
    const trk=t.tracker_status; let trackerHtml;
    if(!trk){ trackerHtml='<div style="margin-top:6px" class="muted"><b style="font-size:11.5px">🔭 跟踪者:</b> 未指派跟踪者</div>'; }
    else {
      const _ago=(iso)=>{ if(!iso) return '?'; const s=Math.floor((Date.now()-Date.parse(iso))/1000); return isNaN(s)?esc(iso):(s<0?'刚刚':fmtAge(s)+'前'); };
      const watch=(trk.watching||[]).map(w=>'<li>'+esc(w)+'</li>').join('');
      const pend=(trk.pending||[]).map(p=>'<li>'+esc(p)+'</li>').join('');
      const acts=(trk.recent_actions||[]).slice(-4).reverse().map(a=>'<div style="font-size:10.5px" class="muted">· <span class="mono">'+esc((a.ts||'').replace('T',' ').slice(5,16))+'</span> '+esc(a.action||'')+'</div>').join('');
      trackerHtml='<div style="margin-top:6px;border-left:2px solid var(--accent);padding-left:8px">'
        +'<div style="font-size:11.5px"><b>🔭 跟踪者 '+esc(trk.tracker||'?')+'</b> <span class="muted">· 上次巡 '+_ago(trk.last_scan)+'</span></div>'
        +(trk.summary?('<div class="muted" style="font-size:11px;margin-top:2px">'+esc(trk.summary)+'</div>'):'')
        +(watch?('<div style="font-size:11px;margin-top:3px"><b class="muted">关注:</b><ul style="margin:2px 0 0 16px;padding:0">'+watch+'</ul></div>'):'')
        +(pend?('<div style="font-size:11px;margin-top:3px"><b style="color:var(--warn)">待督办:</b><ul style="margin:2px 0 0 16px;padding:0">'+pend+'</ul></div>'):'')
        +(acts?('<div style="margin-top:3px"><b class="muted" style="font-size:10.5px">最近督办:</b>'+acts+'</div>'):'')
        +'</div>';
    }
    // ⑥ 复杂度:raw 数值 + P1 成长曲线(t.complexity_series=[{t,size}]·MasterAgent emit;缺→仅 raw)
    const cx=t.complexity||{};
    const spark=_lrSpark(t.complexity_series);
    const cxHtml=Object.keys(cx).length?('<div style="margin-top:6px;font-size:11px" class="muted">⑥复杂度:节点'+esc(''+(cx.nodes_total||0))+'/边'+esc(''+(cx.edges_total||0))+' · 活节点'+esc(''+(cx.nodes_live||0))+' · 宽'+esc(''+(cx.width_max||0))+'/深'+esc(''+(cx.depth||0))+' · 合并'+esc(''+(cx.merges||0))+'/跨任务边'+esc(''+(cx.cross_task_edges||0))+(cx.note?(' <i>['+esc(cx.note)+']</i>'):'')
      +(spark?('<div style="margin-top:4px">📈成长曲线(净规模/时间):'+spark+'</div>'):'<div style="margin-top:2px;font-size:10px">📈成长曲线:待 MasterAgent emit t.complexity_series(读 longrange_events.jsonl 成长史)</div>')+'</div>'):'';
    return '<div class="tl-card fold" data-key="lr-'+esc(''+t.id)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div class="fold-head" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="fold-arrow">▶</span><b style="color:var(--accent)">🗂️ '+esc(t.title||t.id)+'</b>'
      +(t.status?'<span class="cap-chip" style="font-size:10px">'+esc(t.status)+'</span>':'')
      +savedBadge
      +'<span class="cap-chip" style="font-size:10px" title="进度(分母可生长·详情展开看④)">'+esc(''+pct)+'%</span>'
      +'<span class="spacer" style="flex:1"></span><span class="muted" style="font-size:11px">更新 '+esc(t.updated||'')+'</span></div>'
      +'<div class="fold-body">'
      +rolesHtml
      +trackerHtml
      +'<div style="margin-top:8px"><b class="muted" style="font-size:11.5px">①锚点脊柱(👤user方向里程碑/🤖machine收敛检查点·状态upcoming灰/active蓝/reached绿):</b><div class="lr-spine">'+(sp||'<span class="muted">(无脊柱)</span>')+'</div></div>'
      +(arch?'<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">已塌缩归档段:</b><div class="lr-net">'+arch+'</div></div>':'')
      +'<div style="margin-top:8px"><b class="muted" style="font-size:11.5px">②当前活跃段'+(seg.from_anchor?(' ['+esc(seg.from_anchor)+'→'+esc(seg.to_anchor||'')+']'):'')+'(pending灰/ready浅蓝/running蓝/blocked红/await_decision黄/done绿):</b><div class="lr-net">'+nodes+'</div></div>'
      +'<div style="margin-top:8px;display:flex;gap:8px;align-items:center"><b class="muted" style="font-size:11.5px">④进度:</b>'+pbar+'<span class="mono" style="font-size:12px">'+esc(''+pct)+'%</span><span class="muted" style="font-size:10px">段 '+(pg.done_segments||0)+'/'+(pg.total_segments||0)+' · 段内节点 '+(pg.seg_done_nodes||0)+'/'+(pg.seg_total_nodes||0)+'</span>'+gw+'</div>'
      +cxHtml
      +'</div>'
      +'</div>';
  }).join(''));
}
"""
