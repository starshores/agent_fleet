#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js —— WebUI 应用 JS 段(UI2.0 U4 拆自 page.py·纯搬运字节不改;内含 morphdom 2.7.4 vendor 单行 blob,极少改动故不再单拆)。"""
JS = r"""/* ===== morphdom 2.7.4 (inlined · CSP-safe · 增量DOM diff,替代innerHTML整体重建) ===== */
(function(global,factory){typeof exports==="object"&&typeof module!=="undefined"?module.exports=factory():typeof define==="function"&&define.amd?define(factory):(global=global||self,global.morphdom=factory())})(this,function(){"use strict";var DOCUMENT_FRAGMENT_NODE=11;function morphAttrs(fromNode,toNode){var toNodeAttrs=toNode.attributes;var attr;var attrName;var attrNamespaceURI;var attrValue;var fromValue;if(toNode.nodeType===DOCUMENT_FRAGMENT_NODE||fromNode.nodeType===DOCUMENT_FRAGMENT_NODE){return}for(var i=toNodeAttrs.length-1;i>=0;i--){attr=toNodeAttrs[i];attrName=attr.name;attrNamespaceURI=attr.namespaceURI;attrValue=attr.value;if(attrNamespaceURI){attrName=attr.localName||attrName;fromValue=fromNode.getAttributeNS(attrNamespaceURI,attrName);if(fromValue!==attrValue){if(attr.prefix==="xmlns"){attrName=attr.name}fromNode.setAttributeNS(attrNamespaceURI,attrName,attrValue)}}else{fromValue=fromNode.getAttribute(attrName);if(fromValue!==attrValue){fromNode.setAttribute(attrName,attrValue)}}}var fromNodeAttrs=fromNode.attributes;for(var d=fromNodeAttrs.length-1;d>=0;d--){attr=fromNodeAttrs[d];attrName=attr.name;attrNamespaceURI=attr.namespaceURI;if(attrNamespaceURI){attrName=attr.localName||attrName;if(!toNode.hasAttributeNS(attrNamespaceURI,attrName)){fromNode.removeAttributeNS(attrNamespaceURI,attrName)}}else{if(!toNode.hasAttribute(attrName)){fromNode.removeAttribute(attrName)}}}}var range;var NS_XHTML="http://www.w3.org/1999/xhtml";var doc=typeof document==="undefined"?undefined:document;var HAS_TEMPLATE_SUPPORT=!!doc&&"content"in doc.createElement("template");var HAS_RANGE_SUPPORT=!!doc&&doc.createRange&&"createContextualFragment"in doc.createRange();function createFragmentFromTemplate(str){var template=doc.createElement("template");template.innerHTML=str;return template.content.childNodes[0]}function createFragmentFromRange(str){if(!range){range=doc.createRange();range.selectNode(doc.body)}var fragment=range.createContextualFragment(str);return fragment.childNodes[0]}function createFragmentFromWrap(str){var fragment=doc.createElement("body");fragment.innerHTML=str;return fragment.childNodes[0]}function toElement(str){str=str.trim();if(HAS_TEMPLATE_SUPPORT){return createFragmentFromTemplate(str)}else if(HAS_RANGE_SUPPORT){return createFragmentFromRange(str)}return createFragmentFromWrap(str)}function compareNodeNames(fromEl,toEl){var fromNodeName=fromEl.nodeName;var toNodeName=toEl.nodeName;var fromCodeStart,toCodeStart;if(fromNodeName===toNodeName){return true}fromCodeStart=fromNodeName.charCodeAt(0);toCodeStart=toNodeName.charCodeAt(0);if(fromCodeStart<=90&&toCodeStart>=97){return fromNodeName===toNodeName.toUpperCase()}else if(toCodeStart<=90&&fromCodeStart>=97){return toNodeName===fromNodeName.toUpperCase()}else{return false}}function createElementNS(name,namespaceURI){return!namespaceURI||namespaceURI===NS_XHTML?doc.createElement(name):doc.createElementNS(namespaceURI,name)}function moveChildren(fromEl,toEl){var curChild=fromEl.firstChild;while(curChild){var nextChild=curChild.nextSibling;toEl.appendChild(curChild);curChild=nextChild}return toEl}function syncBooleanAttrProp(fromEl,toEl,name){if(fromEl[name]!==toEl[name]){fromEl[name]=toEl[name];if(fromEl[name]){fromEl.setAttribute(name,"")}else{fromEl.removeAttribute(name)}}}var specialElHandlers={OPTION:function(fromEl,toEl){var parentNode=fromEl.parentNode;if(parentNode){var parentName=parentNode.nodeName.toUpperCase();if(parentName==="OPTGROUP"){parentNode=parentNode.parentNode;parentName=parentNode&&parentNode.nodeName.toUpperCase()}if(parentName==="SELECT"&&!parentNode.hasAttribute("multiple")){if(fromEl.hasAttribute("selected")&&!toEl.selected){fromEl.setAttribute("selected","selected");fromEl.removeAttribute("selected")}parentNode.selectedIndex=-1}}syncBooleanAttrProp(fromEl,toEl,"selected")},INPUT:function(fromEl,toEl){syncBooleanAttrProp(fromEl,toEl,"checked");syncBooleanAttrProp(fromEl,toEl,"disabled");if(fromEl.value!==toEl.value){fromEl.value=toEl.value}if(!toEl.hasAttribute("value")){fromEl.removeAttribute("value")}},TEXTAREA:function(fromEl,toEl){var newValue=toEl.value;if(fromEl.value!==newValue){fromEl.value=newValue}var firstChild=fromEl.firstChild;if(firstChild){var oldValue=firstChild.nodeValue;if(oldValue==newValue||!newValue&&oldValue==fromEl.placeholder){return}firstChild.nodeValue=newValue}},SELECT:function(fromEl,toEl){if(!toEl.hasAttribute("multiple")){var selectedIndex=-1;var i=0;var curChild=fromEl.firstChild;var optgroup;var nodeName;while(curChild){nodeName=curChild.nodeName&&curChild.nodeName.toUpperCase();if(nodeName==="OPTGROUP"){optgroup=curChild;curChild=optgroup.firstChild}else{if(nodeName==="OPTION"){if(curChild.hasAttribute("selected")){selectedIndex=i;break}i++}curChild=curChild.nextSibling;if(!curChild&&optgroup){curChild=optgroup.nextSibling;optgroup=null}}}fromEl.selectedIndex=selectedIndex}}};var ELEMENT_NODE=1;var DOCUMENT_FRAGMENT_NODE$1=11;var TEXT_NODE=3;var COMMENT_NODE=8;function noop(){}function defaultGetNodeKey(node){if(node){return node.getAttribute&&node.getAttribute("id")||node.id}}function morphdomFactory(morphAttrs){return function morphdom(fromNode,toNode,options){if(!options){options={}}if(typeof toNode==="string"){if(fromNode.nodeName==="#document"||fromNode.nodeName==="HTML"||fromNode.nodeName==="BODY"){var toNodeHtml=toNode;toNode=doc.createElement("html");toNode.innerHTML=toNodeHtml}else{toNode=toElement(toNode)}}else if(toNode.nodeType===DOCUMENT_FRAGMENT_NODE$1){toNode=toNode.firstElementChild}var getNodeKey=options.getNodeKey||defaultGetNodeKey;var onBeforeNodeAdded=options.onBeforeNodeAdded||noop;var onNodeAdded=options.onNodeAdded||noop;var onBeforeElUpdated=options.onBeforeElUpdated||noop;var onElUpdated=options.onElUpdated||noop;var onBeforeNodeDiscarded=options.onBeforeNodeDiscarded||noop;var onNodeDiscarded=options.onNodeDiscarded||noop;var onBeforeElChildrenUpdated=options.onBeforeElChildrenUpdated||noop;var skipFromChildren=options.skipFromChildren||noop;var addChild=options.addChild||function(parent,child){return parent.appendChild(child)};var childrenOnly=options.childrenOnly===true;var fromNodesLookup=Object.create(null);var keyedRemovalList=[];function addKeyedRemoval(key){keyedRemovalList.push(key)}function walkDiscardedChildNodes(node,skipKeyedNodes){if(node.nodeType===ELEMENT_NODE){var curChild=node.firstChild;while(curChild){var key=undefined;if(skipKeyedNodes&&(key=getNodeKey(curChild))){addKeyedRemoval(key)}else{onNodeDiscarded(curChild);if(curChild.firstChild){walkDiscardedChildNodes(curChild,skipKeyedNodes)}}curChild=curChild.nextSibling}}}function removeNode(node,parentNode,skipKeyedNodes){if(onBeforeNodeDiscarded(node)===false){return}if(parentNode){parentNode.removeChild(node)}onNodeDiscarded(node);walkDiscardedChildNodes(node,skipKeyedNodes)}function indexTree(node){if(node.nodeType===ELEMENT_NODE||node.nodeType===DOCUMENT_FRAGMENT_NODE$1){var curChild=node.firstChild;while(curChild){var key=getNodeKey(curChild);if(key){fromNodesLookup[key]=curChild}indexTree(curChild);curChild=curChild.nextSibling}}}indexTree(fromNode);function handleNodeAdded(el){onNodeAdded(el);var curChild=el.firstChild;while(curChild){var nextSibling=curChild.nextSibling;var key=getNodeKey(curChild);if(key){var unmatchedFromEl=fromNodesLookup[key];if(unmatchedFromEl&&compareNodeNames(curChild,unmatchedFromEl)){curChild.parentNode.replaceChild(unmatchedFromEl,curChild);morphEl(unmatchedFromEl,curChild)}else{handleNodeAdded(curChild)}}else{handleNodeAdded(curChild)}curChild=nextSibling}}function cleanupFromEl(fromEl,curFromNodeChild,curFromNodeKey){while(curFromNodeChild){var fromNextSibling=curFromNodeChild.nextSibling;if(curFromNodeKey=getNodeKey(curFromNodeChild)){addKeyedRemoval(curFromNodeKey)}else{removeNode(curFromNodeChild,fromEl,true)}curFromNodeChild=fromNextSibling}}function morphEl(fromEl,toEl,childrenOnly){var toElKey=getNodeKey(toEl);if(toElKey){delete fromNodesLookup[toElKey]}if(!childrenOnly){var beforeUpdateResult=onBeforeElUpdated(fromEl,toEl);if(beforeUpdateResult===false){return}else if(beforeUpdateResult instanceof HTMLElement){fromEl=beforeUpdateResult;indexTree(fromEl)}morphAttrs(fromEl,toEl);onElUpdated(fromEl);if(onBeforeElChildrenUpdated(fromEl,toEl)===false){return}}if(fromEl.nodeName!=="TEXTAREA"){morphChildren(fromEl,toEl)}else{specialElHandlers.TEXTAREA(fromEl,toEl)}}function morphChildren(fromEl,toEl){var skipFrom=skipFromChildren(fromEl,toEl);var curToNodeChild=toEl.firstChild;var curFromNodeChild=fromEl.firstChild;var curToNodeKey;var curFromNodeKey;var fromNextSibling;var toNextSibling;var matchingFromEl;outer:while(curToNodeChild){toNextSibling=curToNodeChild.nextSibling;curToNodeKey=getNodeKey(curToNodeChild);while(!skipFrom&&curFromNodeChild){fromNextSibling=curFromNodeChild.nextSibling;if(curToNodeChild.isSameNode&&curToNodeChild.isSameNode(curFromNodeChild)){curToNodeChild=toNextSibling;curFromNodeChild=fromNextSibling;continue outer}curFromNodeKey=getNodeKey(curFromNodeChild);var curFromNodeType=curFromNodeChild.nodeType;var isCompatible=undefined;if(curFromNodeType===curToNodeChild.nodeType){if(curFromNodeType===ELEMENT_NODE){if(curToNodeKey){if(curToNodeKey!==curFromNodeKey){if(matchingFromEl=fromNodesLookup[curToNodeKey]){if(fromNextSibling===matchingFromEl){isCompatible=false}else{fromEl.insertBefore(matchingFromEl,curFromNodeChild);if(curFromNodeKey){addKeyedRemoval(curFromNodeKey)}else{removeNode(curFromNodeChild,fromEl,true)}curFromNodeChild=matchingFromEl;curFromNodeKey=getNodeKey(curFromNodeChild)}}else{isCompatible=false}}}else if(curFromNodeKey){isCompatible=false}isCompatible=isCompatible!==false&&compareNodeNames(curFromNodeChild,curToNodeChild);if(isCompatible){morphEl(curFromNodeChild,curToNodeChild)}}else if(curFromNodeType===TEXT_NODE||curFromNodeType==COMMENT_NODE){isCompatible=true;if(curFromNodeChild.nodeValue!==curToNodeChild.nodeValue){curFromNodeChild.nodeValue=curToNodeChild.nodeValue}}}if(isCompatible){curToNodeChild=toNextSibling;curFromNodeChild=fromNextSibling;continue outer}if(curFromNodeKey){addKeyedRemoval(curFromNodeKey)}else{removeNode(curFromNodeChild,fromEl,true)}curFromNodeChild=fromNextSibling}if(curToNodeKey&&(matchingFromEl=fromNodesLookup[curToNodeKey])&&compareNodeNames(matchingFromEl,curToNodeChild)){if(!skipFrom){addChild(fromEl,matchingFromEl)}morphEl(matchingFromEl,curToNodeChild)}else{var onBeforeNodeAddedResult=onBeforeNodeAdded(curToNodeChild);if(onBeforeNodeAddedResult!==false){if(onBeforeNodeAddedResult){curToNodeChild=onBeforeNodeAddedResult}if(curToNodeChild.actualize){curToNodeChild=curToNodeChild.actualize(fromEl.ownerDocument||doc)}addChild(fromEl,curToNodeChild);handleNodeAdded(curToNodeChild)}}curToNodeChild=toNextSibling;curFromNodeChild=fromNextSibling}cleanupFromEl(fromEl,curFromNodeChild,curFromNodeKey);var specialElHandler=specialElHandlers[fromEl.nodeName];if(specialElHandler){specialElHandler(fromEl,toEl)}}var morphedNode=fromNode;var morphedNodeType=morphedNode.nodeType;var toNodeType=toNode.nodeType;if(!childrenOnly){if(morphedNodeType===ELEMENT_NODE){if(toNodeType===ELEMENT_NODE){if(!compareNodeNames(fromNode,toNode)){onNodeDiscarded(fromNode);morphedNode=moveChildren(fromNode,createElementNS(toNode.nodeName,toNode.namespaceURI))}}else{morphedNode=toNode}}else if(morphedNodeType===TEXT_NODE||morphedNodeType===COMMENT_NODE){if(toNodeType===morphedNodeType){if(morphedNode.nodeValue!==toNode.nodeValue){morphedNode.nodeValue=toNode.nodeValue}return morphedNode}else{morphedNode=toNode}}}if(morphedNode===toNode){onNodeDiscarded(fromNode)}else{if(toNode.isSameNode&&toNode.isSameNode(morphedNode)){return}morphEl(morphedNode,toNode,childrenOnly);if(keyedRemovalList){for(var i=0,len=keyedRemovalList.length;i<len;i++){var elToRemove=fromNodesLookup[keyedRemovalList[i]];if(elToRemove){removeNode(elToRemove,elToRemove.parentNode,false)}}}}if(!childrenOnly&&morphedNode!==fromNode&&fromNode.parentNode){if(morphedNode.actualize){morphedNode=morphedNode.actualize(fromNode.ownerDocument||doc)}fromNode.parentNode.replaceChild(morphedNode,fromNode)}return morphedNode}}var morphdom=morphdomFactory(morphAttrs);return morphdom});
/* ===== end morphdom ===== */
let state=null, pollTimer=null, lastTasksOpen=new Set();

// 增量更新统一入口:把容器 el 的【孩子】patch 成 innerHtml(childrenOnly→容器自身的 class/style 不动)。
//  · getNodeKey 用 id 或 data-key(列表项给稳定业务 key)→ 原地更新、不重建 → 不闪、滚动不跳。
//  · onBeforeElUpdated 跳过【正在输入/选择】的表单控件 → 光标/未提交输入/下拉态不被刷新冲掉。
function _keyOf(n){ if(n.nodeType!==1) return null; return n.id || n.getAttribute('data-key') || null; }
function _onBeforeUpd(f){ if(f===document.activeElement && (f.tagName==='INPUT'||f.tagName==='TEXTAREA'||f.tagName==='SELECT')) return false; return true; }
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
// 稳健化(2026-07-10 用户「不够灵敏 + 中断后卡住」):
//  ① armed(有 wait 进程=阻塞等消息)→ 权威判空闲待命,覆盖任何陈旧 thinking(此时不可能在思考);
//  ② thinking/tool 但 ts 超 STALE 秒无新事件 → 判「停顿?」——治中断/漏 Stop 卡在思考不回落
//     (PreToolUse/PostToolUse 每步刷新 ts,正常多工具干活 age 一直很小;冻住=中断或超长单工具)。
//  数据只来自 hook 事件 + wait 进程,非推断。
// 分状态陈旧阈:思考态推理很少 >30s,冻这么久多是【中断】(中断不触发任何 hook·官方证实);
// 工具态本可跑很久(长 bash 等),放宽到 300s 才疑,免误报正常长工具。
const TURN_STALE={thinking:30, tool:300};
function turnBadge(tn, sw){
  if(sw==='armed') return `<span class="badge idle" title="有 wait 进程·阻塞等消息=空闲待命(不可能在思考)">⚪ 空闲·待命</span>`;
  if(!tn||!tn.state) return '<span class="badge none" title="无 turn 状态(未装 hook / 非 bus 成员 / 尚无事件)">–</span>';
  const age=tn.age, ag=(age==null?'':' '+fmtAge(age));
  if(tn.state==='idle') return `<span class="badge idle" title="turn 已结束(Stop/idle_prompt)·空闲${ag}">⚪ 空闲${ag}</span>`;
  const stale=TURN_STALE[tn.state]||60;
  if(age!=null && age>stale)
    return `<span class="badge none" title="状态为「${esc(tn.label||tn.state)}」但已 ${fmtAge(age)} 无新 hook 事件 → 判停顿(用户中断不触发任何 hook·官方证实 / 或异常结束)">◌ 停顿?${ag}</span>`;
  if(tn.state==='tool') return `<span class="badge warn" title="正在执行工具 ${esc(tn.tool||'')}${ag}">🔧 ${esc(tn.label)}${ag}</span>`;
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
  f.roles.forEach(r=>{
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
        inner+=`<div class="task-card"><div class="t-h">
          <span class="badge ${subBadge[t.sub]||'idle'}">${esc(t.sub)}</span>
          ${appr?'<span class="badge appr">⚠ requires_approval</span>':''}
          ${(t.requeue_count>0)?'<span class="badge warn">requeue×'+esc(t.requeue_count)+'</span>':''}
          <span class="mono">${esc(t.id)}</span> · from <b>${esc(t.from)}</b>
          <span class="t-title">${esc(t.title)}</span>
          ${t.claimed?'· claimed '+esc(t.claimed):''}</div>
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

// ===== BI · 资源消耗 =====
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
  const cacheDenom=(t.cache_read||0)+(t.input||0)+(t.cache_write_5m||0)+(t.cache_write_1h||0);
  const cacheHit=cacheDenom?(Math.floor((t.cache_read||0)/cacheDenom*1000)/10).toFixed(1)+'%':'–';
  // 用户定 BI 口径:token 消耗提主位;总 token + io(有效工作量)并列对照 + cache_read 单列标注;$ 标(参考)
  const tk=_tokTotals(t);
  morphInto(document.getElementById('usageKpis'), [
    ['总 token(全历史)', fmtN(tk.total),'acc','input+output+cache_read+cache_write,固定物理量(不随 plan/定价浮动)'],
    ['io 有效工作量', fmtN(tk.io),'','input+output —— 真正的新 work / 有效工作量(不含 cache 重复读)'],
    ['cache_read 重复读', fmtN(tk.cache),'','重复读上下文 · 占总 token ~'+tk.cachePct+'%(总 token 被它灌水,故单列)'],
    ['cache 命中', cacheHit,'good','cache_read /(cache_read + cache_creation + input),输入侧命中占比'],
    ['API calls(去重)', fmtN(t.calls),''],
    ['API 等价成本(参考)', usd(t.cost_usd),'warn','$ 随 plan/定价浮动、plan 下还会下沉,仅作 API 等价参考;token 才是固定量'],
    ['范围成本('+rl+'·参考)', usd(rangeCost),'','daily 暂无 token 分项(token_stats 迁移后补),范围仍按 $ 参考'],
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
    const px=X(i),py=Y(v); ctx.beginPath(); ctx.arc(px,py,2.5,0,7); ctx.fillStyle=lineColor; ctx.fill();
    pts.push({x:px,y:py,date:p.date,v:v,src:p.src,cost:p.cost_usd,calls:p.calls}); });
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
              : best.src==='vacation' ? ' <span style="color:'+UC.idle+'">·无使用</span>' : '';
    let line = best.src==='backfill'
      ? fmtN(best.v)+' tok <span class="muted">(stats-cache·口径或异)</span>'
      : best.src==='vacation'
      ? '0 tok <span class="muted">(无使用)</span>'
      : fmtN(best.v)+' '+lbl+(best.cost!=null?' · '+usd(best.cost)+' 参考':'')+(best.calls!=null?' · '+fmtN(best.calls)+' calls':'');
    tip.innerHTML='<b>'+esc(best.date)+'</b>'+tag+'<br>'+line;
    tip.style.display='block';
    let lx=best.x+10; if(lx>rect.width-130)lx=best.x-122;
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

// ===== 能力池 caps =====
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
      '<div class="ag-desc">'+desc+'</div>'+caps+metaLine+'</div>';
  };
  const agAll=(d.agents||[]).map(agCard).join('')+(d.agents_retired||[]).map(agCard).join('');
  morphInto(document.getElementById('agDetail'), agAll||'<div class="muted" style="padding:8px">（无 agent）</div>');
  // ① 能力池清单卡片
  const pool=(d.caps||[]).map(c=>{
    const rk=c.risk?('<span class="rb '+esc(c.risk_cls)+'">'+esc(c.risk)+'</span>'):'';
    const cls='cap-card'+(c.risk_cls?(' '+c.risk_cls):'')+(c.count===0?' zero':'');
    const hold=c.count?('<span class="muted" style="font-size:11px"> · '+c.holders.map(esc).join('、')+'</span>'):'<span class="muted" style="font-size:11px"> · 无持有者</span>';
    return '<div class="'+cls+'"><div class="cn">'+esc(c.name)+rk+'</div>'+
      '<div class="cd">'+esc(c.desc)+'</div>'+
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
  meta.textContent='活跃 '+d.n+' · 精确绑定 '+(d.n_bound||0)+'/'+d.n+' · 最近 '+d.active_min+'min · '+esc(d.as_of||'');
  morphInto(kp,'<div class="k"><div class="v ctxpct '+(d.max_pct>=d.red?'red':d.max_pct>=d.yellow?'yellow':'green')+'">'+d.max_pct+'%</div><div class="l">峰值占用</div></div>'
    +'<div class="k"><div class="v ctxpct '+(d.n_red>0?'red':'green')+'">'+d.n_red+'</div><div class="l">≥65% 将被自动压</div></div>'
    +'<div class="k"><div class="v ctxpct '+(d.n_yellow>0?'yellow':'green')+'">'+d.n_yellow+'</div><div class="l">≥50% 注意</div></div>'
    +'<div class="k"><div class="v">'+d.n+'</div><div class="l">活跃会话</div></div>');
  if(!d.rows||!d.rows.length){ morphInto(body,'<tr><td colspan="6" class="muted">（最近 '+d.active_min+' 分钟无活跃会话）</td></tr>'); foot.textContent=''; return; }
  morphInto(body, d.rows.map(r=>{
    const barW=Math.max(2,Math.min(100,r.pct))*0.6;
    let who;
    if(r.role) who='<b>'+esc(r.role)+'</b> <span style="color:var(--live)" title="session_id 精确绑定(who 暴露的 presence.session_id ⨝ 会话 jsonl id)">✓</span>';
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
  foot.textContent='占用% =(input+cache_read+cache_creation)/模型窗口;自动压阈=窗口×65%(实测 auto-compact 真阈),余量=阈−ctx(<0=已过线将被压)。role✓=session_id 精确绑定(who 暴露 presence.session_id ⨝ 会话 jsonl id,MasterAgent 全解;不再 regex 猜);未绑定=daemon(无此 env)/尚未 re-hello/非注册会话,不猜。占用逐会话精确。窗口:opus-4*/sonnet-4=1M、haiku/未知=200K(假定标注)。所有会话如实显 proj(自看控制台默认不脱敏,能看清哪个个人会话在吃上下文/逼近压线);liveness/注册看 🛰 运维面板。';
}
async function loadCtx(){
  try{ const r=await fetch('/api/ctxpanel'); _ctx=await r.json(); }catch(e){ /* 保留上次 */ }
  renderCtx(); renderCtxBadge();
}
function renderCtxBadge(){
  const b=document.getElementById('ctxBadge'); if(!b) return; const d=_ctx;
  if(!d || d.loading){ b.className='hbadge loading'; b.textContent='🧠 …'; return; }
  if(d.n_red>0){ b.className='hbadge danger'; b.textContent='🧠 ⚠ '+d.n_red+' 将压 '+d.max_pct+'%'; b.title=d.n_red+' 个会话 ≥65%(峰值 '+d.max_pct+'%)— 将被 auto-compact,建议先主动落档重置'; }
  else if(d.n_yellow>0){ b.className='hbadge warn'; b.textContent='🧠 '+d.max_pct+'%'; b.title='峰值占用 '+d.max_pct+'%(有 ≥50% 会话)'; }
  else{ b.className='hbadge ok'; b.textContent='🧠 '+d.max_pct+'%'; b.title='峰值占用 '+d.max_pct+'% — 全部安全'; }
}
document.getElementById('ctxRefresh').onclick=()=>loadCtx();
document.getElementById('ctxBadge').onclick=()=>showTab('ctx');

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
      ${act}
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

// ===== 🧩 工作组看板(使用方出口径·MasterUI出可视化)=====
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
    glob+='<div style="margin:8px 16px;padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:var(--panel2)">'
      +'<div style="font-weight:600;color:var(--accent);font-size:13px">🌐 全局层 · compact 后所有角色共用</div>'
      +'<div class="muted" style="font-size:11px;margin:2px 0 6px">'+esc(m.common_rules_note||'compact hook 全局通用操作铁律;{role} 为模板占位(实注时按角色插值)')+'</div>'
      +'<ol style="margin:0;padding-left:18px;font-size:12px;line-height:1.6">'+cr.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ol></div>';
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
    return '<div class="tl-card" data-key="role-'+esc(r.role)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:var(--accent);font-size:14px">👤 '+esc(r.role)+'</b>'
      +'<span class="cap-chip" style="background:rgba(91,157,255,.14);color:var(--accent)">'+esc(r.layer||'')+'</span></div>'
      +(r.one_liner?'<div style="font-size:12.5px;margin-top:4px">'+esc(r.one_liner)+'</div>':'')
      +'<div class="muted" style="font-size:11px;margin-top:5px">cwd <span class="mono">'+esc(r.cwd||'?')+'</span> · mode '+esc(r.mode||'?')+' · caps '+(caps||'—')+'</div>'
      +'<div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:8px">'
      +col('启动流程',r.startup_flow,'🚀')
      +col('明式输入(显式派)',r.explicit_inputs,'📥')
      +col('隐式输入(自举读锚)',r.implicit_inputs,'🧭')+'</div>'
      +(r.boundary?'<div style="font-size:11.5px;margin-top:8px;background:rgba(245,166,35,.08);border-left:2px solid var(--warn);padding:4px 8px;border-radius:4px"><b class="muted">边界:</b>'+esc(r.boundary)+'</div>':'')
      +cblock
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
    return '<div class="tl-card" data-key="bizrole-'+esc(r.role)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:var(--accent);font-size:14px">🏢 '+esc(r.role)+'</b>'
      +'<span class="cap-chip" style="background:rgba(91,157,255,.14);color:var(--accent)">'+esc(r.layer||'')+'</span></div>'
      +(r.one_liner?'<div style="font-size:12.5px;margin-top:4px">'+esc(r.one_liner)+'</div>':'')
      +'<div class="muted" style="font-size:11px;margin-top:5px">cwd <span class="mono">'+esc(r.cwd||'?')+'</span> · mode '+esc(r.mode||'?')+' · caps '+(caps||'—')+'</div>'
      +'<div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:8px">'
      +col('启动流程',r.startup_flow,'🚀')
      +col('明式输入(显式派)',r.explicit_inputs,'📥')
      +col('隐式输入(自举读锚)',r.implicit_inputs,'🧭')+'</div>'
      +(r.boundary?'<div style="font-size:11.5px;margin-top:8px;background:rgba(245,166,35,.08);border-left:2px solid var(--warn);padding:4px 8px;border-radius:4px"><b class="muted">边界:</b>'+esc(r.boundary)+'</div>':'')
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
    return '<div class="tl-card" data-key="lr-'+esc(''+t.id)+'" style="border:1px solid var(--line);border-radius:8px;margin:8px 16px;padding:10px 14px">'
      +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:var(--accent)">🗂️ '+esc(t.title||t.id)+'</b>'
      +(t.status?'<span class="cap-chip" style="font-size:10px">'+esc(t.status)+'</span>':'')
      +savedBadge
      +'<span class="spacer" style="flex:1"></span><span class="muted" style="font-size:11px">更新 '+esc(t.updated||'')+'</span></div>'
      +rolesHtml
      +trackerHtml
      +'<div style="margin-top:8px"><b class="muted" style="font-size:11.5px">①锚点脊柱(👤user方向里程碑/🤖machine收敛检查点·状态upcoming灰/active蓝/reached绿):</b><div class="lr-spine">'+(sp||'<span class="muted">(无脊柱)</span>')+'</div></div>'
      +(arch?'<div style="margin-top:6px"><b class="muted" style="font-size:11.5px">已塌缩归档段:</b><div class="lr-net">'+arch+'</div></div>':'')
      +'<div style="margin-top:8px"><b class="muted" style="font-size:11.5px">②当前活跃段'+(seg.from_anchor?(' ['+esc(seg.from_anchor)+'→'+esc(seg.to_anchor||'')+']'):'')+'(pending灰/ready浅蓝/running蓝/blocked红/await_decision黄/done绿):</b><div class="lr-net">'+nodes+'</div></div>'
      +'<div style="margin-top:8px;display:flex;gap:8px;align-items:center"><b class="muted" style="font-size:11.5px">④进度:</b>'+pbar+'<span class="mono" style="font-size:12px">'+esc(''+pct)+'%</span><span class="muted" style="font-size:10px">段 '+(pg.done_segments||0)+'/'+(pg.total_segments||0)+' · 段内节点 '+(pg.seg_done_nodes||0)+'/'+(pg.seg_total_nodes||0)+'</span>'+gw+'</div>'
      +cxHtml
      +'</div>';
  }).join(''));
}

// ===== tab 分页 =====
const TAB_NAMES={fleet:'🛰 舰队/运维',bi:'📊 运营/资源','usage':'📈 历史用量',caps:'🧬 能力池',ctx:'🧠 上下文',prompts:'📝 提示词审核',tasklines:'🎯 任务线看板',groups:'🧩 工作组',mechanisms:'🔧 机制',roles:'👤 角色',bizroles:'🏢 业务角色',longrange:'🗂️ 长程任务',tasks:'📋 任务/协作',live:'🔴 实时',paths:'📁 路径',fleetlog:'📋 舰队日志'};
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
  else if(t==='prompts'){ if(!_promptsLoaded) loadPrompts(); }
  else if(t==='tasklines') loadTasklines();
  else if(t==='groups') loadGroups();
  else if(t==='mechanisms') loadMechanisms();
  else if(t==='roles') loadRoles();
  else if(t==='bizroles') loadBizRoles();
  else if(t==='longrange') loadLongrange();
  else if(t==='fleet') load();
  else if(t==='paths'){ loadPaths(); loadStorage(); }
  else if(t==='fleetlog') loadFleetLog();
}
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
}
function syncTabTop(){ const hd=document.querySelector('header'); if(hd) document.getElementById('tabbar').style.top=hd.offsetHeight+'px'; }
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
  const el=d.edge_lock;
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
  h+=hRow('Edge 调试锁', el?('被占:'+esc(el.who||'?')):'空闲', !!el);
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
"""
