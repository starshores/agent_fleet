import {scopeKey} from './state.mjs';
import {responseError, validateEnvelope} from './api.mjs';

export const COMMAND_LABELS = Object.freeze({submit: '保存新任务', input: '保存补充信息', answer: '提交明确决定', stop: '请求停止'});
export function commandKey(selection, action, target = '') { return JSON.stringify([scopeKey(selection), action, target]); }
export function commandGate(model) {
  if (!model.selection) return '请先选择业务。';
  if (model.page === 'tasks' && model.detailTab === 'history') return '跨代任务链仅供查看。请返回当前代任务详情后操作。';
  if (model.workbench?.status !== 'ready' || model.workbench.source?.state_root !== model.selection.stateRoot) return '操作配置尚未确认，请刷新。';
  if (!model.workbench.data?.commands?.enabled) return '当前服务为只读模式。需由本机管理员显式启用公共操作。';
  if (typeof model.workbench.data.commands.csrf_token !== 'string' || !model.workbench.data.commands.csrf_token) return '操作凭据尚未确认，请刷新。';
  if (model.deployment.status !== 'ready') return '部署快照尚未确认，请刷新。';
  const installation = model.deployment.data?.installations?.find(row => row.installation_id === model.selection.installationId);
  if (installation?.state !== 'active' || model.deployment.data.active_installation_id !== model.selection.installationId) return '当前业务未启用，只能查看记录。';
  if (installation.generation !== model.selection.generation) return '历史代次仅供查看，请切回当前代次操作。';
  return null;
}
export function emptyCommand(fields = {}) { return {fields: {...fields}, status: 'draft', payload: null, result: null, message: '', action: null}; }
export function commandLocked(record) { return Boolean(record?.payload); }
export function commandPayload(selection, action, fields, target, makeId) {
  const generation = selection.generation;
  if (!Number.isSafeInteger(generation)) throw new Error('业务代次无效。');
  const text = String(fields.text ?? '');
  if (['submit', 'input'].includes(action) && !text.trim()) throw new Error('请填写内容。');
  if (action === 'submit') {
    if (!fields.role_id) throw new Error('请选择接单角色。');
    return {generation, role_id: fields.role_id, text, request_id: makeId()};
  }
  if (action === 'input') return {generation, task_id: target.task_id, attempt_id: target.attempt_id, text, request_id: makeId()};
  if (action === 'answer') {
    if (!['approve', 'reject'].includes(fields.choice)) throw new Error('请明确选择批准或拒绝；未选择不会自动视为拒绝。');
    return {generation, task_id: target.task_id, decision_id: target.decision_id,
      expected_revision: target.expected_revision, choice: fields.choice, answer_id: makeId()};
  }
  if (action === 'stop') {
    if (!String(fields.reason ?? '').trim()) throw new Error('请说明停止原因。');
    return {generation, task_id: target.task_id, attempt_id: target.attempt_id, reason: fields.reason};
  }
  throw new Error('不支持此操作。');
}
export function commandReceipt(action, result) {
  if (action === 'submit') return '任务已保存，等待独立执行服务领取；此页面没有启动模型。';
  if (action === 'input') return '补充信息已保存，是否发送或接收请查看后续记录；这不构成审批。';
  if (action === 'answer') return '平台决定已记录；这不是 Codex 或 Claude 的原生工具审批。';
  return result?.termination_confirmed === true ? '平台已记录停止结果。请查看执行记录中的退出证据。' : '停止请求已记录，尚未确认运行进程退出。';
}

/** Commands are never automatically replayed or cancelled on navigation. A lost
 * reply can still mean a committed write, so its exact payload stays locked. */
export class CommandStore {
  constructor(fetcher = globalThis.fetch.bind(globalThis), makeId = () => globalThis.crypto.randomUUID()) {
    this.fetcher = fetcher; this.makeId = makeId; this.records = new Map(); this.selection = null; this.epoch = 0;
  }
  setScope(selection) { this.selection = selection ? {...selection} : null; this.epoch += 1; }
  get(selection, action, target = '', fields = {}) {
    const key = commandKey(selection, action, target);
    if (!this.records.has(key)) this.records.set(key, emptyCommand(fields));
    return this.records.get(key);
  }
  edit(selection, action, target, field, value) {
    const record = this.get(selection, action, target);
    if (commandLocked(record)) return false;
    record.fields[field] = value; record.message = ''; return true;
  }
  reset(selection, action, target = '') {
    const record = this.get(selection, action, target);
    if (['pending', 'uncertain'].includes(record.status)) return false;
    this.records.delete(commandKey(selection, action, target)); return true;
  }
  hasUnsaved() {
    return [...this.records.values()].some(record => ['pending', 'uncertain'].includes(record.status)
      || record.status === 'draft' && Object.values(record.fields).some(value => typeof value === 'string' && value.trim()));
  }
  async send(selection, action, keyTarget, target, token, notify = () => {}) {
    const record = this.get(selection, action, keyTarget);
    if (['pending', 'success', 'rejected'].includes(record.status)) return false;
    if (!token || scopeKey(this.selection) !== scopeKey(selection)) return false;
    try { record.payload ??= Object.freeze(commandPayload(selection, action, record.fields, target, this.makeId)); }
    catch (error) { record.message = error.message; notify(); return false; }
    const retryUncertain = record.status === 'uncertain';
    const epoch = this.epoch;
    const current = () => epoch === this.epoch && scopeKey(this.selection) === scopeKey(selection);
    record.action = action; record.status = 'pending'; record.message = '正在保存，请勿重复提交…';
    if (current()) notify();
    try {
      const response = await this.fetcher(`/api/v1/installations/${encodeURIComponent(selection.installationId)}/commands/${action}`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {
          Accept: 'application/json', 'Content-Type': 'application/json', 'X-Workbench-CSRF': token}, body: JSON.stringify(record.payload),
      });
      let body;
      try { body = await response.json(); } catch { throw new Error('回复无法解析。'); }
      if (!response.ok) {
        // An explicit client rejection did not accept this operation. Server or
        // transport errors may occur after commit, so keep the same retry key.
        record.status = !retryUncertain && response.status >= 400 && response.status < 500 ? 'rejected' : 'uncertain';
        record.message = responseError(body, response.status) + (retryUncertain
          ? ' 本次重试未被接受，但原提交的结果仍未确认；原内容和标识继续保留，请核对已有记录。' : '');
      } else {
        validateEnvelope(body, selection);
        if (body.data.action !== action || !Object.hasOwn(body.data, 'result')) throw new Error('操作回执不完整。');
        record.status = 'success'; record.result = body.data.result; record.message = commandReceipt(action, record.result);
      }
    } catch {
      record.status = 'uncertain'; record.message = '未收到可确认的回执。操作可能已保存，请先刷新核对；如需重试，将使用完全相同的内容和标识。';
    }
    if (current()) notify();
    return record.status === 'success';
  }
}
