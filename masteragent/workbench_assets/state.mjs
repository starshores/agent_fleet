/** Pure presentation state. No storage, process, or network side effects. */
export const PAGE_NAMES = Object.freeze({overview: '工作台', tasks: '任务', team: '团队', resources: '资料', connections: '运行设置'});

export const STATE_LABELS = Object.freeze({
  queued: '待执行', running: '执行中', waiting: '等待协作', awaiting_decision: '等待确认',
  needs_reconcile: '需要核对', completed: '已完成', failed: '未成功', cancelled: '已取消',
  pending: '已保存', sending: '发送中', accepted: '已接收', held: '无法确认',
  approved: '已批准', rejected: '已拒绝', expired: '已过期', active: '已启用',
  inactive: '未启用', uninstalled: '已卸载', stopped: '已停止', stopping: '正在停止',
  starting: '正在启动', unavailable: '暂不可用', not_verified: '尚未验证',
});

export function stateLabel(value) { return STATE_LABELS[value] ?? (value || '状态未知'); }
export function stateTone(value) {
  if (['completed', 'approved', 'accepted', 'active'].includes(value)) return 'success';
  if (['running', 'sending'].includes(value)) return 'accent';
  if (['awaiting_decision', 'needs_reconcile', 'held', 'expired', 'stopping'].includes(value)) return 'warning';
  if (['failed', 'rejected'].includes(value)) return 'danger';
  return 'neutral';
}
export function displayValue(value, fallback = '未采集') {
  return value === null || value === undefined || value === '' ? fallback : String(value);
}
export function displayCount(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '—';
}
export function formatTime(value, {short = false} = {}) {
  if (value === null || value === undefined || value === '') return '未记录';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', short
    ? {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false}
    : {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(date);
}
export function shortId(value) { return typeof value === 'string' ? value.slice(0, 8) : '—'; }
export function scopeKey(selection) {
  return selection ? JSON.stringify([selection.stateRoot ?? null, selection.installationId, selection.generation]) : 'unselected';
}
export function emptySnapshot() {
  return {status: 'idle', data: null, observedAt: null, source: null, error: null};
}
export function reduceSnapshot(previous, event) {
  if (event.type === 'loading') return {...previous, status: previous.data === null ? 'loading' : 'refreshing', error: null};
  if (event.type === 'success') return {status: 'ready', data: event.envelope.data,
    observedAt: event.envelope.observed_at, source: event.envelope.source, error: null};
  if (event.type === 'error') return {...previous, status: previous.data === null ? 'error' : 'stale', error: event.error};
  return previous;
}
export function mergeTasks(existing, incoming) {
  const result = [], positions = new Map();
  for (const row of [...existing, ...incoming]) {
    if (!row || typeof row.task_id !== 'string') continue;
    if (positions.has(row.task_id)) result[positions.get(row.task_id)] = row;
    else { positions.set(row.task_id, result.length); result.push(row); }
  }
  return result;
}
export function chooseInstallation(installations, activeId, current, stateRoot = null) {
  const rows = Array.isArray(installations) ? installations : [];
  const row = rows.find(item => item.installation_id === current?.installationId)
    ?? rows.find(item => item.installation_id === activeId) ?? rows[0];
  if (!row) return null;
  const root = stateRoot ?? current?.stateRoot ?? null;
  const preserve = current?.installationId === row.installation_id && current?.stateRoot === root
    && Number.isInteger(current.generation) && current.generation >= 0 && current.generation <= row.generation;
  return {stateRoot: root, installationId: row.installation_id, generation: preserve ? current.generation : row.generation};
}
export function createModel() {
  return {deployment: emptySnapshot(), workbench: emptySnapshot(), selection: null, page: 'overview', section: 'main', composerOpen: false,
    snapshots: Object.fromEntries(['overview', 'tasks', 'team', 'resources', 'connections', 'detail', 'history'].map(key => [key, emptySnapshot()])),
    selectedTaskId: null, taskLookup: '', detailTab: 'timeline', historySection: 'generations', historyGeneration: null,
    historyCursors: [null], historyPage: 0, filters: {state: '', role_id: ''}, taskPageCount: 1};
}
export function selectScope(model, selection) {
  if (scopeKey(model.selection) === scopeKey(selection)) return model;
  return {...createModel(), deployment: model.deployment, workbench: model.workbench, page: model.page, section: model.section, selection};
}
export function memberPresentation(member) {
  if (!member) return {label: '尚无成员记录', tone: 'neutral', detail: '角色配置已登记，尚无该代次的接单成员。'};
  if (!member.lease_valid) return {label: stateLabel(member.state), tone: 'neutral', detail: '当前没有有效成员租约。'};
  if (member.owned_attempt_active) return {label: '有执行归属', tone: 'accent', detail: '租约有效；执行归属不代表原生模型可交互。'};
  if (member.accepting_tasks) return {label: '可接单', tone: 'success', detail: '成员租约有效；模型连通尚未验证。'};
  return {label: stateLabel(member.state), tone: 'warning', detail: '成员租约有效，当前不接纳新任务。'};
}

/** Absence is meaningful only in a complete, fresh, same-scope snapshot. */
export function memberSnapshotPresentation(snapshot, selection, roleId) {
  const source = snapshot?.source;
  const sameScope = source?.state_root === selection?.stateRoot && source?.installation_id === selection?.installationId
    && source?.generation === selection?.generation;
  const unknown = {label: '接单状态未知', tone: 'neutral', member: null,
    detail: '成员快照尚未确认。任务仍可保存排队；是否有执行服务接单需要刷新核对。'};
  if (!roleId || !sameScope || !snapshot.data || !Array.isArray(snapshot.data.members)) return unknown;
  const members = snapshot.data.members.filter(row => row.role_id === roleId && row.generation === selection.generation);
  const member = members.find(row => row.lease_valid === true) ?? members[0];
  const complete = snapshot.data.truncated?.members === false;
  if (snapshot.status !== 'ready') return {...unknown, observedAt: snapshot.observedAt,
    detail: '成员快照不是最新已确认状态。保留原快照时间供核对，不能据此认定当前可接单或无人接单。'};
  if (!member && !complete) return {...unknown, observedAt: snapshot.observedAt,
    detail: '成员列表不完整，当前页未找到此角色的记录，不能认定没有成员。任务可保存排队。'};
  if (!member) return {label: '尚无常驻成员', tone: 'warning', member: null, observedAt: snapshot.observedAt,
    detail: '此快照中没有同业务、代次和角色的常驻成员。仍可保存排队；是否已被领取请查看任务记录。'};
  if (typeof member.lease_valid !== 'boolean') return {...unknown, observedAt: snapshot.observedAt};
  return {...memberPresentation(member), member, observedAt: snapshot.observedAt};
}

/** A scope epoch also rejects A→B→A replies with the same installation ID. */
export class RequestScope {
  constructor() { this.selection = null; this.epoch = 0; this.slots = new Map(); }
  setScope(selection) {
    this.abortAll(); this.selection = selection ? {...selection} : null; this.epoch += 1;
  }
  begin(slot) {
    this.abort(slot);
    const ticket = {slot, epoch: this.epoch, selection: this.selection ? {...this.selection} : null, controller: new AbortController()};
    this.slots.set(slot, ticket);
    return ticket;
  }
  current(ticket) { return ticket.epoch === this.epoch && this.slots.get(ticket.slot) === ticket; }
  finish(ticket) { if (this.current(ticket)) this.slots.delete(ticket.slot); }
  abort(slot) { this.slots.get(slot)?.controller.abort(); this.slots.delete(slot); }
  abortAll() { for (const ticket of this.slots.values()) ticket.controller.abort(); this.slots.clear(); }
}
