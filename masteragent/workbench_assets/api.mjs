import {RequestScope} from './state.mjs';

export function installationPath(selection, resource, query = {}) {
  if (!selection || !Number.isInteger(selection.generation)) throw new Error('请先选择有效的业务代次。');
  const params = new URLSearchParams({generation: String(selection.generation)});
  for (const [key, value] of Object.entries(query)) if (value !== '' && value !== null && value !== undefined) params.set(key, String(value));
  return `/api/v1/installations/${encodeURIComponent(selection.installationId)}/${resource}?${params}`;
}

export function validateEnvelope(value, selection, deployment = false) {
  if (!value || value.api_version !== '3.1-dev1' || value.source?.kind !== 'masteragent'
      || typeof value.source.state_root !== 'string' || !value.source.state_root
      || !value.data || typeof value.data !== 'object' || Array.isArray(value.data)) {
    throw new Error('服务返回的数据契约不完整，请刷新或检查版本。');
  }
  if (!deployment && (value.source.state_root !== selection?.stateRoot
      || value.source.installation_id !== selection?.installationId
      || value.source.generation !== selection?.generation)) {
    throw new Error('业务或代次已变化，请刷新部署信息。');
  }
  return value;
}

export function responseError(body, status) {
  const message = typeof body?.error === 'string' ? body.error : body?.error?.message;
  const errors = {
    not_initialized: '所选状态目录尚未初始化，工作台不会自动创建状态。',
    migration_required: '此状态格式需要显式迁移，请先使用平台迁移流程。',
    unsupported_schema: '此状态格式与当前平台版本不兼容。',
    schema_corrupt: '状态结构完整性检查未通过，无法继续读取。',
    snapshot_corrupt: '业务快照完整性检查未通过，无法继续读取。',
    unsupported_journal_mode: '当前数据库日志模式不受此只读入口支持。',
    invalid_generation: '所选业务代次不可用，请核对代次范围。',
    generation_mismatch: '业务代次已变化，请刷新部署后重试。',
  };
  if (errors[body?.error?.code]) return errors[body.error.code];
  if (status === 404) return '所选记录不存在，或已不属于当前查询范围。';
  return message || `读取未完成（HTTP ${status}）。`;
}

export class SnapshotClient {
  constructor(fetcher = globalThis.fetch.bind(globalThis)) { this.fetcher = fetcher; this.requests = new RequestScope(); }
  setScope(selection) { this.requests.setScope(selection); }
  abort(slot) { this.requests.abort(slot); }
  async load(slot, request, notify, {deployment = false} = {}) {
    const ticket = this.requests.begin(slot);
    notify({type: 'loading'});
    const read = async path => {
      const response = await this.fetcher(path, {method: 'GET', signal: ticket.controller.signal,
        cache: 'no-store', credentials: 'same-origin', headers: {Accept: 'application/json'}});
      const body = await response.json();
      if (!this.requests.current(ticket)) throw new DOMException('Stale request', 'AbortError');
      if (!response.ok) throw new Error(responseError(body, response.status));
      return validateEnvelope(body, ticket.selection, deployment);
    };
    try {
      const envelope = typeof request === 'function' ? await request(read, ticket.selection) : await read(request);
      if (!this.requests.current(ticket)) return false;
      notify({type: 'success', envelope});
      return true;
    } catch (error) {
      if (!this.requests.current(ticket)) return false;
      if (error?.name !== 'AbortError') notify({type: 'error', error: error?.message || '暂时无法连接本机服务。'});
      return false;
    } finally { this.requests.finish(ticket); }
  }
}
