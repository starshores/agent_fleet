#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js_bi —— page_js 拆分子模块 BI(自我迭代波·案①·纯搬运字节一致·2026-09-16)。"""
BI = r"""// ===== BI · 资源消耗 =====
function fmtN(n){ n=n||0; if(n>=1e6)return (n/1e6).toFixed(2)+'M'; if(n>=1e3)return (n/1e3).toFixed(1)+'k'; return ''+n; }
// 会话 jsonl 大小(输入=KB)→ 人类可读:>=1024KB 显 xxM,否则 xxK。
function fmtSize(kb){ kb=+kb||0; if(kb>=1024)return (kb/1024).toFixed(1)+'M'; if(kb>=1)return Math.round(kb)+'K'; return '0'; }
// 大小徽标(彩色强调):>=5M red / >=1M warn / else 常规
function szChip(kb){ kb=+kb||0; if(!kb)return ''; const c=kb>=5120?'red':(kb>=1024?'warn':''); return '<span class="sz-chip '+c+'" title="会话 jsonl 大小;大=越吃上下文/越接近 compact/越该归档清">'+fmtSize(kb)+'</span>'; }
function fmtDur(s){ s=s||0; if(s>=60){const m=Math.floor(s/60);return m+'m'+Math.round(s%60)+'s';} return s+'s'; }
function renderBI(bi){
  if(!bi) return;
  document.getElementById('biUpd').textContent=bi.now+(bi.psutil?'':' · 无psutil(系统资源不可用)');
  const tu=bi.today_usage||{};
  const st=bi.sys_total;
  const fmtMem=mb=>mb>=1024?(mb/1024).toFixed(2)+' GB':Math.round(mb)+' MB';
  const kpi=[
    ['bus 总内存', st?fmtMem(st.rss_mb):'—', st?(st.roles+' 角色 · '+st.nproc+' 进程 · '+st.cpu+'% CPU'):'无psutil', false],
    ['今日 token(总)', fmtN(tu.tokens_total||0), (tu.date?(tu.is_today?tu.date:('库存至 '+tu.date)):'usage.db'), false],
    ['今日 io(in+out)', fmtN(tu.tokens_io||0), 'in '+fmtN(tu.input||0)+' / out '+fmtN(tu.output||0), false],
    ['今日成本($参考)', usd(tu.cost_usd||0), '', false],
    ['今日 calls', fmtN(tu.calls||0), '', false],
    ['在跑任务', bi.running.length, '', false]
  ];
  morphInto(document.getElementById('biKpis'), kpi.map(k=>
    `<div class="bi-kpi${k[3]?' warn':''}" data-key="k-${esc(k[0])}"><div class="k">${esc(k[0])}</div><div class="v">${k[1]}${k[2]?'<small>'+esc(k[2])+'</small>':''}</div></div>`).join(''));
  morphInto(document.getElementById('biRunning'), bi.running.length? bi.running.map(r=>
    `<span class="bi-run" data-key="run-${esc(r.role)}"><span class="live-dot on"></span><b>${esc(r.role)}</b> ${esc(r.title||r.task)}</span>`).join('')
    : '<span class="muted" style="font-size:12px">当前无在跑任务</span>');
  // 模型用量统计:各模型→会话数+角色列表
  const mc=bi.model_counts||[];
  if(mc.length){
    const chips=mc.map(m=>{
      const col=modelColor(m.model);
      const roles=(m.roles||[]).join('、');
      return `<div data-key="mc-${esc(m.model)}" style="display:inline-flex;align-items:baseline;gap:6px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:5px 12px;margin:2px 4px 2px 0">`
        +`<span class="mono" style="font-size:12.5px;color:${col};font-weight:600">${esc(modelShort(m.model))}</span>`
        +`<span style="font-size:13px;font-weight:700">${m.sessions}</span><span class="muted" style="font-size:11px">个会话</span>`
        +(roles?`<span class="muted" style="font-size:10.5px;margin-left:2px">(${esc(roles)})</span>`:``)+`</div>`;
    }).join('');
    morphInto(document.getElementById('biModels'),
      `<div class="muted" style="font-size:11px;font-weight:600;margin-bottom:5px">模型用量统计 <span style="font-weight:400">· 各会话当前使用模型</span></div>`+chips);
  } else {
    morphInto(document.getElementById('biModels'),'');
  }
  // 在岗资源占用:仅列有活进程(sys)的 role,按内存降序(metrics 任务消耗口径已随 daemon 移除退役,成本看「历史用量」)
  const live=(bi.per_role||[]).filter(r=>r.sys).sort((a,b)=>(b.sys.rss_mb||0)-(a.sys.rss_mb||0));
  morphInto(document.getElementById('biSysBody'), !live.length
    ? '<tr><td colspan="3" class="muted" style="padding:10px 16px">无在岗进程资源数据（psutil 不可用或无活进程）</td></tr>'
    : live.map(r=>`<tr data-key="bs-${esc(r.role)}">
      <td><b>${esc(r.role)}</b></td>
      <td class="num">${r.sys.cpu}</td>
      <td class="num">${r.sys.rss_mb}</td></tr>`).join(''));
}
async function loadBI(){ try{ const r=await fetch('/api/bi'); renderBI(await r.json()); }catch(e){} }

// ===== 历史用量 BI (P0) =====
function _fit(cv,cssH){
  const dpr=window.devicePixelRatio||1;
  const cssW=cv.clientWidth||(cv.parentElement&&cv.parentElement.clientWidth)||600;
  cv.width=Math.round(cssW*dpr); cv.height=Math.round(cssH*dpr); cv.style.height=cssH+'px';
  const ctx=cv.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); return {ctx,w:cssW,h:cssH};
}
const UC={bg:'#0f1115',line:'#2a2f3a',dim:'#9aa3b2',acc:'#5b9dff',live:'#3ecf8e',warn:'#f5a623',dead:'#ff5d5d',idle:'#7d8aa0',none:'#5a6172'};
function usd(n){ return '$'+(n||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); }
// token 口径(用户定:token 才是固定物理量;$ 会随 plan/定价浮动)。总=io+cache_read+cache_write;io=新增有效工作量。
function _tokTotals(t){ t=t||{};
  const io=(t.input||0)+(t.output||0);
  const cache=(t.cache_read||0);
  const cw=(t.cache_write_5m||0)+(t.cache_write_1h||0);
  const total=io+cache+cw;
  return {io,cache,cw,total,cachePct: total?Math.round(cache/total*100):0};
}
function modelColor(m){ if(m.indexOf('opus-4-8')>=0)return UC.acc; if(m.indexOf('opus-4-7')>=0)return UC.idle; if(m.indexOf('haiku')>=0)return UC.live; return UC.none; }
function modelShort(m){ return m.replace('claude-','').replace('-20251001',''); }
function projShort(p){ return p ? p.replace(/^([A-Za-z])--/, '$1:/').replace(/-/g, '/') : ''; }

let _usageData=null, _uRange=30;
const openProjRaws=new Set();   // 展开钻取的项目名(状态驱动·morph 刷新不丢;U1 响应式教义:命令式注入子树先状态化)
function sliceDaily(daily,r){
  // 按"日历范围"切片(以数据最后一天为锚,非系统今天 — 与数据对齐、免时区抖动)
  if(!r||!daily||!daily.length) return (daily||[]).slice();
  const last=daily[daily.length-1].date;
  const d=new Date(last+'T00:00:00'); d.setDate(d.getDate()-(r-1));
  const y=d.getFullYear(), m=('0'+(d.getMonth()+1)).slice(-2), dd=('0'+d.getDate()).slice(-2);
  const cut=y+'-'+m+'-'+dd;
  return daily.filter(x=>x.date>=cut);
}
function renderUsage(d){
  const kp=document.getElementById('usageKpis');
  if(!d || d.error){ kp.innerHTML='<div class="muted" style="padding:6px 12px">用量数据读取失败:'+esc(d&&d.error||'?')+'</div>'; return; }
  _usageData=d;
  const mt=d.meta||{};
  const cfg=mt.config_active||{alias_count:mt.alias_count,merge_groups:mt.merge_groups,ignore_projects:mt.ignore_projects};
  document.getElementById('usageMeta').textContent='刷新于 '+esc(d.as_of||'')
    +' · 跟踪 '+(mt.tracked_jsonl_files!=null?mt.tracked_jsonl_files:'?')+' jsonl'
    +(cfg?(' · alias '+(cfg.alias_count||0)+'/merge '+(cfg.merge_groups||0)+'/ignore '+(cfg.ignore_projects||0)):'');
  // 范围相关 KPI + 趋势:交给 applyRange;范围按钮绑定一次
  const rg=document.getElementById('uRange');
  if(rg && !rg._bound){ rg._bound=1; rg.querySelectorAll('button').forEach(b=>b.onclick=()=>applyRange(+b.dataset.r)); }
  _bindTrendHover('uTrend','uTrendTip'); _bindTrendHover('uTrendTotal','uTrendTotalTip');
  applyRange(_uRange);
  renderUProj();
  // 模型迁移
  drawModels(d.by_month_model||[]);
}
// 项目 Top N —— token 为主(用量统计侧已补 by_project token 分项):按 tokens_total 排;并列 io + $(参考),与 KPI 同口径。
// U1 响应式:钻取展开=openProjRaws 状态驱动、raw 内容随主渲染一并出 html → morphInto 增量 patch,刷新中展开态/内容天然保留。
function renderUProj(){
  const d=_usageData; if(!d) return;
  let projs=(d.by_project||[]).slice();
  const tokOf=p=>p.tokens_total!=null?p.tokens_total:(p.input!=null?((p.input||0)+(p.output||0)+(p.cache_read||0)+(p.cache_write_5m||0)+(p.cache_write_1h||0)):null);
  const ioOf=p=>p.tokens_io!=null?p.tokens_io:(p.input!=null?((p.input||0)+(p.output||0)):null);
  const hasTok=projs.some(p=>tokOf(p)!=null);
  const valOf=p=>hasTok?(tokOf(p)||0):(p.cost_usd||0);
  projs.sort((a,b)=>valOf(b)-valOf(a)); projs=projs.slice(0,8);
  const max=projs.length?valOf(projs[0]):1;
  // 对数刻度:小项目彼此差几十倍,线性会全钳到 min 看不出;log 把它们拉开(条长仅视觉相对,绝对值看右侧数字)
  const lmin=projs.length?Math.log(Math.max(1,valOf(projs[projs.length-1]))):0;
  const lmax=Math.log(Math.max(1,max)), lspan=(lmax-lmin)||1;
  const unit=document.getElementById('uProjUnit');
  if(unit) unit.textContent=hasTok?'(全历史 · 按 tokens_total 排 · 条=对数刻度 · io/$ 对照)':'(全历史 · $ API等价·参考 — token 分项待 token_stats 迁移补齐)';
  const rawHtml=(proj)=>{
    let raws=rawFor(proj), note='';
    if(!raws.length){ raws=(d.by_project_raw||[]).slice(0,12); note='<div class="muted" style="font-size:11px;padding:1px 0 3px">未能自动映射,列出全部原始目录:</div>'; }
    const rmax=raws.length?Math.max.apply(null,raws.map(x=>x.cost_usd))||1:1;
    return note+raws.map(rr=>{const pct=Math.max(2,rr.cost_usd/rmax*100).toFixed(1);
      return `<div class="bar-row" style="grid-template-columns:150px 1fr 90px"><span class="lbl" title="${esc(rr.project)}">${esc(projShort(rr.project))}</span><span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:var(--idle)"></span></span><span class="num">${usd(rr.cost_usd)} · ${fmtN(rr.calls)}</span></div>`;
    }).join('')||'<div class="muted" style="font-size:11px;padding:2px 0">无</div>';
  };
  morphInto(document.getElementById('uProj'), projs.map(p=>{
    const v=valOf(p); const pct=(8+(Math.log(Math.max(1,v))-lmin)/lspan*92).toFixed(1);
    const num=hasTok
      ? `${fmtN(tokOf(p))} tok <span style="color:var(--dim)">· io ${fmtN(ioOf(p))} · ${usd(p.cost_usd)}参考</span>`
      : usd(p.cost_usd);
    const open=openProjRaws.has(p.project);
    return `<div class="bar-row uproj-row" data-key="up-${esc(p.project)}" data-proj="${esc(p.project)}" title="点击展开原始目录构成">
      <span class="lbl">${esc(p.project)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${pct}%"></span></span>
      <span class="num">${num}</span></div>
      <div class="uproj-raw" data-key="ur-${esc(p.project)}" style="display:${open?'block':'none'}">${open?rawHtml(p.project):''}</div>`;
  }).join('')||'<div class="muted" style="padding:6px 0">无数据</div>');
  bindProjDrill();
}
function applyRange(r){
  _uRange=r;
  const rg=document.getElementById('uRange');
  if(rg) rg.querySelectorAll('button').forEach(b=>b.classList.toggle('on',+b.dataset.r===r));
  const d=_usageData; if(!d) return;
  const daily=sliceDaily(d.daily||[],r);
  const t=d.totals||{};
  const rangeCost=daily.reduce((s,x)=>s+(x.cost_usd||0),0);
  const rl=r?(r+'d'):'全历史';
  // 范围内 输入/输出/缓存 分项(仅逐条 ETL 段 src=messages 有拆分;backfill 日聚合段无、不计入)
  const msgs=daily.filter(x=>x.src==='messages'&&x.input!=null);
  const rIn=msgs.reduce((s,x)=>s+(x.input||0),0), rOut=msgs.reduce((s,x)=>s+(x.output||0),0);
  const rCr=msgs.reduce((s,x)=>s+(x.cache_read||0),0);
  const rCw=msgs.reduce((s,x)=>s+(x.cache_write_5m||0)+(x.cache_write_1h||0),0);
  const rDenom=rCr+rIn+rCw;
  const cacheHit=rDenom?(Math.floor(rCr/rDenom*1000)/10).toFixed(1)+'%':'–';
  // 用户定 BI 口径:token 消耗提主位;总 token + io(有效工作量)并列对照 + cache_read 单列标注;$ 标(参考)
  const tk=_tokTotals(t);
  morphInto(document.getElementById('usageKpis'), [
    ['总 token(全历史)', fmtN(tk.total),'acc','input+output+cache_read+cache_write,固定物理量(不随 plan/定价浮动)'],
    ['io 有效工作量', fmtN(tk.io),'','input+output —— 真正的新 work / 有效工作量(不含 cache 重复读)'],
    ['输入 input('+rl+')', fmtN(rIn),'','每次调用的新输入 token(不含 cache 读写)· 仅逐条 ETL 段(05-11 起)有拆分,backfill 段不计'],
    ['输出 output('+rl+')', fmtN(rOut),'','模型生成的输出 token · 仅逐条 ETL 段(05-11 起)有拆分,backfill 段不计'],
    ['cache_read 重复读', fmtN(tk.cache),'','重复读上下文 · 占总 token ~'+tk.cachePct+'%(总 token 被它灌水,故单列)'],
    ['cache 命中('+rl+')', cacheHit,'good','cache_read /(cache_read + cache_write + input),范围内输入侧命中占比(单日值悬停趋势图看)'],
    ['API calls(去重)', fmtN(t.calls),''],
    ['API 等价成本(参考)', usd(t.cost_usd),'warn','$ 随 plan/定价浮动、plan 下还会下沉,仅作 API 等价参考;token 才是固定量'],
    ['范围成本('+rl+'·参考)', usd(rangeCost),'','范围内 $ 合计(参考)'],
  ].map(k=>`<div class="kpi" data-key="uk-${esc(k[0])}"${k[3]?` title="${esc(k[3])}"`:''}><div class="k">${esc(k[0])}</div><div class="v ${k[2]}">${k[1]}</div></div>`).join(''));
  drawTrend(daily,{cv:'uTrend',mode:'io',peak:'trendPeak',tip:'uTrendTip',legend:'trendLegend'});
  drawTrend(daily,{cv:'uTrendTotal',mode:'total',peak:'trendPeakTotal',tip:'uTrendTotalTip'});
}
// 每日 token 趋势:前后一致的【单一连续序列】(别3段)。真实性靠脚注披露口径,不靠视觉分段。
//  · backfill 段(03-27~04-28)= stats-cache 日聚合,其 total 量级≈io(未含 cache);io 图两段真可比、连续。
//  · vacation 段(04-29~05-10)= 无使用 = 真实 0(已知的零,非插值/估算)。
//  · 口径:mode='io'(input+output) | 'total'(io+cache读写)。总图早期 backfill 无 cache 故偏低=真相(不捏造)。
function _trendVal(x, mode){
  if(!x) return null;
  if(x.src==='backfill') return (x.tokens_total!=null?x.tokens_total:null);  // 日聚合≈io量级(无 cache 拆分)
  if(mode==='total') return (x.tokens_total!=null?x.tokens_total:null);
  return (x.tokens_io!=null?x.tokens_io:null);
}
// opt={cv,mode,peak,tip,legend?} —— 参数化:io/总两图各调一次,各自独立纵轴
function drawTrend(daily, opt){
  const cv=document.getElementById(opt.cv); if(!cv) return;
  const f=_fit(cv,190); const ctx=f.ctx,w=f.w,h=f.h;
  const pts=[]; const tip=opt.tip?document.getElementById(opt.tip):null; if(tip)tip.style.display='none';
  const peakEl=opt.peak?document.getElementById(opt.peak):null;
  const lg=opt.legend?document.getElementById(opt.legend):null;
  const padL=54,padR=14,padT=14,padB=22, gw=w-padL-padR, gh=h-padT-padB;
  const all=daily||[];
  const vals=all.map(x=>_trendVal(x,opt.mode));
  const has=vals.filter(v=>v!=null);
  if(!has.length){ ctx.fillStyle=UC.dim;ctx.fillText('无数据',padL,padT+20); if(peakEl)peakEl.textContent=''; if(lg)lg.innerHTML=''; cv._pts=[]; return; }
  const maxV=Math.max.apply(null,has)*1.12||1;
  const n=all.length;
  const X=i=>padL+gw*(n<2?0.5:i/(n-1)), Y=v=>padT+gh*(1-v/maxV);
  ctx.strokeStyle=UC.line;ctx.fillStyle=UC.dim;ctx.lineWidth=1;ctx.font='10px Consolas';
  for(let g=0;g<=4;g++){const gv=maxV*g/4,yy=Y(gv);ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();ctx.fillText(fmtN(Math.round(gv)),2,yy+3);}
  const lineColor=opt.mode==='total'?UC.live:UC.acc;   // 总口径用绿、io用蓝,两图一眼区分(同图内前后一色·不再分段)
  (function seg(){ let prev=null;
    all.forEach((p,i)=>{ const v=vals[i];
      if(v==null){ prev=null; return; }
      const px=X(i),py=Y(v);
      if(prev!=null){ ctx.beginPath(); ctx.moveTo(prev.x,prev.y); ctx.lineTo(px,py);
        ctx.strokeStyle=lineColor; ctx.lineWidth=2; ctx.stroke(); }
      prev={x:px,y:py};
    }); })();
  let pk=-1, pkv=-1;
  all.forEach((p,i)=>{ const v=vals[i]; if(v==null) return; if(v>pkv){pkv=v;pk=i;}
    const zero=(p.src==='zero'||p.src==='vacation');   // 无使用日=弱化点(浅色·小)区别真测行(Master数分契约:src=zero 视觉可辨)
    const px=X(i),py=Y(v); ctx.beginPath(); ctx.arc(px,py,zero?1.6:2.5,0,7); ctx.fillStyle=zero?UC.dim:lineColor; ctx.fill();
    pts.push({x:px,y:py,date:p.date,v:v,src:p.src,cost:p.cost_usd,calls:p.calls,
      inp:p.input,out:p.output,cr:p.cache_read,cw:(p.cache_write_5m||0)+(p.cache_write_1h||0)}); });
  if(pk>=0){ const px=X(pk),py=Y(pkv); ctx.beginPath(); ctx.arc(px,py,4,0,7); ctx.fillStyle=UC.dead; ctx.fill(); }
  ctx.fillStyle=UC.dim;ctx.fillText(all[0].date.slice(5),padL-2,h-8);ctx.textAlign='right';ctx.fillText(all[n-1].date.slice(5),w-padR,h-8);ctx.textAlign='left';
  if(peakEl) peakEl.textContent='峰值 '+fmtN(pkv)+' tok · '+all[pk].date.slice(5)+' · '+n+'天';
  cv._pts=pts; cv._mode=opt.mode;
  if(lg){ const bm=_usageData&&_usageData.backfill_meta;
    lg.innerHTML='<span><i style="background:'+lineColor+'"></i>'+(opt.mode==='total'?'总(io+cache读写)':'io(input+output)')+'</span>'
      +(bm?'<span class="muted" style="margin-left:auto">'+esc(bm.bf_from||'')+'~'+esc(bm.bf_to||'')+' stats-cache 日聚合(略粗'+(opt.mode==='total'?'·总口径未含cache故偏低':'')+');'+esc(bm.gap_from||'')+'~'+esc(bm.gap_to||'')+' 无使用(=0);其后逐条ETL</span>':''); }
}
function _bindTrendHover(cvId, tipId){
  const cv=document.getElementById(cvId); if(!cv||cv._hov)return; cv._hov=1;
  const tip=document.getElementById(tipId);
  cv.addEventListener('mousemove',e=>{
    const pts=cv._pts||[]; if(!pts.length||!tip){if(tip)tip.style.display='none';return;}
    const rect=cv.getBoundingClientRect(); const mx=e.clientX-rect.left;
    let best=pts[0],bd=1e9;
    pts.forEach(p=>{const dd=Math.abs(p.x-mx); if(dd<bd){bd=dd;best=p;}});
    const lbl=cv._mode==='total'?'tok 总':'tok io';
    const tag = best.src==='backfill' ? ' <span style="color:'+UC.idle+'">·backfill日聚合</span>'
              : best.src==='vacation' ? ' <span style="color:'+UC.idle+'">·无使用</span>'
              : best.src==='zero' ? ' <span style="color:'+UC.idle+'">·无使用</span>' : '';
    let line = best.src==='backfill'
      ? fmtN(best.v)+' tok <span class="muted">(stats-cache·口径或异)</span>'
      : best.src==='vacation'
      ? '0 tok <span class="muted">(无使用)</span>'
      : best.src==='zero'
      ? '0 tok <span class="muted">(当天无使用·已知零)</span>'
      : fmtN(best.v)+' '+lbl+(best.cost!=null?' · '+usd(best.cost)+' 参考':'')+(best.calls!=null?' · '+fmtN(best.calls)+' calls':'');
    // 单日 输入/输出/缓存比(仅逐条 ETL 段有拆分;缓存比=cache_read/(cache_read+cache_write+input))
    if(best.inp!=null && best.src!=='backfill' && best.src!=='vacation'){
      const dn=(best.cr||0)+(best.cw||0)+(best.inp||0);
      const cp=dn?(Math.floor((best.cr||0)/dn*1000)/10).toFixed(1)+'%':'–';
      line+='<br>入 '+fmtN(best.inp)+' · 出 '+fmtN(best.out)+' · 缓存比 '+cp;
    }
    tip.innerHTML='<b>'+esc(best.date)+'</b>'+tag+'<br>'+line;
    tip.style.display='block';
    let lx=best.x+10; if(lx>rect.width-170)lx=best.x-162;
    tip.style.left=Math.max(2,lx)+'px'; tip.style.top=Math.max(2,best.y-36)+'px';
  });
  cv.addEventListener('mouseleave',()=>{if(tip)tip.style.display='none';});
}
function rawFor(logical){
  // 启发式把 by_project_raw 归到 logical(alias 多为同名;merge 组无法纯前端映射 → 由调用方兜底)
  const raws=(_usageData&&_usageData.by_project_raw)||[];
  const norm=s=>projShort(String(s)).toLowerCase().replace(/[^a-z0-9]/g,'');
  const l=String(logical).toLowerCase().replace(/[^a-z0-9]/g,'');
  return raws.filter(r=>{const n=norm(r.project); return n===l||(n&&l&&(n.indexOf(l)>=0||l.indexOf(n)>=0));});
}
function bindProjDrill(){
  // 委托事件·只绑一次:点击行 → 切 openProjRaws 状态 → 重渲染项目表(morph 增量,展开态状态驱动)
  const up=document.getElementById('uProj'); if(!up||up._drill)return; up._drill=1;
  up.addEventListener('click',e=>{
    const row=e.target.closest('.uproj-row'); if(!row||!up.contains(row))return;
    const proj=row.dataset.proj; if(proj==null)return;
    if(openProjRaws.has(proj)) openProjRaws.delete(proj); else openProjRaws.add(proj);
    renderUProj();
  });
}
function drawModels(bmm){
  const cv=document.getElementById('uModels'); const f=_fit(cv,200); const ctx=f.ctx,w=f.w,h=f.h;
  const rows=bmm.filter(r=>r.model.indexOf('synthetic')<0);
  const months=[...new Set(rows.map(r=>r.month))].sort();
  const models=[...new Set(rows.map(r=>r.model))];
  if(!months.length){ ctx.fillStyle=UC.dim;ctx.fillText('无数据',20,30); return; }
  const padL=18,padR=14,padT=14,padB=26, gw=w-padL-padR, gh=h-padT-padB;
  const byMM={}; rows.forEach(r=>{byMM[r.month+'|'+r.model]=r.calls;});
  const tot=months.map(m=>models.reduce((s,md)=>s+(byMM[m+'|'+md]||0),0));
  const maxV=Math.max.apply(null,tot)*1.12||1; const bw=gw/months.length*0.5;
  ctx.font='10px Consolas';ctx.textAlign='center';
  months.forEach((m,mi)=>{
    const cx=padL+gw*(mi+0.5)/months.length-bw/2; let yCur=padT+gh; let t=tot[mi];
    models.forEach(md=>{const v=byMM[m+'|'+md]||0; if(!v)return; const bh=gh*v/maxV; yCur-=bh; ctx.fillStyle=modelColor(md); ctx.fillRect(cx,yCur,bw,bh);});
    ctx.fillStyle=UC.dim;ctx.fillText(m.slice(2),cx+bw/2,h-10);
    const v48=models.filter(md=>md.indexOf('opus-4-8')>=0).reduce((s,md)=>s+(byMM[m+'|'+md]||0),0);
    if(t){const pt=Math.round(v48/t*100)+'%';ctx.font='bold 10px Consolas';
      ctx.lineWidth=3;ctx.strokeStyle='rgba(15,17,21,.92)';ctx.strokeText(pt,cx+bw/2,yCur-5);
      ctx.fillStyle='#fff';ctx.fillText(pt,cx+bw/2,yCur-5);ctx.font='10px Consolas';}
  });
  ctx.textAlign='left';
  morphInto(document.getElementById('uModelLegend'), models.map(md=>`<span data-key="ml-${esc(md)}"><i style="background:${modelColor(md)}"></i>${esc(modelShort(md))}</span>`).join(''));
}
async function loadUsage(force){
  const kp=document.getElementById('usageKpis'); if(!kp.innerHTML) kp.innerHTML='<div class="muted" style="padding:6px 12px">加载中…</div>';
  try{ const r=await fetch('/api/usage'+(force?'?fresh=1':'')); renderUsage(await r.json()); }
  catch(e){ kp.innerHTML='<div class="muted" style="padding:6px 12px">加载失败:'+esc(e)+'</div>'; }
}

// ===== 派工/协作效率(metrics.jsonl · 仅自动化派工口径,非总账)=====
function renderDispatch(d){
  if(!d) return;
  const t=d.total||{};
  document.getElementById('dmMeta').textContent='刷新于 '+esc(d.now||'')+(d.span?(' · '+esc(d.span.from)+'→'+esc(d.span.to)):'');
  const ratio=(t.daemon||0)+':'+(t.dispatch||0);
  morphInto(document.getElementById('dmKpis'), [
    ['派工总成本', usd(t.cost), 'acc'],
    ['任务数', fmtN(t.tasks)+(t.errors?' ('+t.errors+'e)':''), ''],
    ['总 turns', fmtN(t.turns), ''],
    ['平均 $/任务', usd(t.avg_cost), ''],
    ['平均时长', fmtDur(t.avg_dur_s), ''],
    ['daemon:dispatch', ratio, 'warn'],
  ].map(k=>`<div class="kpi" data-key="dk-${esc(k[0])}"><div class="k">${esc(k[0])}</div><div class="v ${k[2]}">${k[1]}</div></div>`).join(''));
  // 各角色成本(降序条形 + $/turn 效率)
  const roles=d.per_role||[]; const rmax=roles.length?(roles[0].cost||1):1;
  morphInto(document.getElementById('dmRoles'), roles.map(r=>{
    const pct=Math.max(2,(r.cost/(rmax||1))*100).toFixed(1);
    return `<div class="bar-row" data-key="dr-${esc(r.role)}" style="grid-template-columns:120px 1fr auto"><span class="lbl" title="${esc(r.role)}">${esc(r.role)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${pct}%"></span></span>
      <span class="num">${usd(r.cost)} <span style="color:var(--dim)">· ${r.tasks}任务 · $${(r.per_turn||0).toFixed(3)}/t</span></span></div>`;
  }).join('')||'<div class="muted" style="padding:6px 16px">无数据</div>');
  // daemon vs dispatch 拆分
  const bk=d.by_kind||{}; const kinds=[['daemon',bk.daemon],['dispatch',bk.dispatch]];
  const kmax=Math.max(1,...kinds.map(k=>k[1]?(k[1].cost||0):0));
  morphInto(document.getElementById('dmKind'), kinds.map(([name,k])=>{
    k=k||{tasks:0,cost:0,per_task:0,avg_dur_s:0};
    const pct=Math.max(2,((k.cost||0)/kmax)*100).toFixed(1);
    const col=name==='dispatch'?'var(--warn)':'var(--accent)';
    return `<div class="bar-row" data-key="kind-${name}" style="grid-template-columns:90px 1fr auto"><span class="lbl"><span class="kind-tag ${name}">${name}</span></span>
      <span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:${col}"></span></span>
      <span class="num">${usd(k.cost)} <span style="color:var(--dim)">· ${k.tasks}任务 · ${usd(k.per_task)}/任务 · ${fmtDur(k.avg_dur_s)}</span></span></div>`;
  }).join(''));
  // Top 耗时任务(title 标注)
  const top=d.top_dur||[];
  morphInto(document.getElementById('dmTop'), top.length? top.map((x,i)=>{
    const secs=Math.round((x.dur_ms||0)/1000);
    const err=x.status==='error'?' <span style="color:var(--dead)">err</span>':'';
    return `<div class="bar-row" data-key="dt-${esc(x.id||(x.role+'-'+i))}" style="grid-template-columns:1fr auto"><span class="lbl" title="${esc(x.title||'(无标题)')}"><span class="kind-tag ${esc(x.kind)}">${esc(x.kind)}</span>${esc(x.role)} · ${esc(x.title||'(无标题)')}${err}</span>
      <span class="num">${fmtDur(secs)} · ${x.turns}t · ${usd(x.cost)}</span></div>`;
  }).join(''):'<div class="muted" style="padding:6px 16px">无数据</div>');
}
async function loadDispatch(force){
  try{ const r=await fetch('/api/dispatch_metrics'+(force?'?fresh=1':'')); renderDispatch(await r.json()); }
  catch(e){}
}
"""
