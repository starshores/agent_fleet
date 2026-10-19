import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseInstallation, createModel, displayCount, displayValue, emptySnapshot, formatTime,
  memberPresentation, memberSnapshotPresentation, mergeTasks, reduceSnapshot, RequestScope, scopeKey, selectScope, stateLabel} from '../masteragent/workbench_assets/state.mjs';
import {installationPath, responseError, SnapshotClient, validateEnvelope} from '../masteragent/workbench_assets/api.mjs';
import {renderView} from '../masteragent/workbench_assets/views.mjs';
import {CommandStore, commandGate, commandLocked, commandPayload, commandReceipt} from '../masteragent/workbench_assets/commands.mjs';
import {FEATURE_DIRECTORY, legacyLink} from '../masteragent/workbench_assets/navigation.mjs';
import {historyContext, historyPath, historySupported, loadHistorySnapshot, validateHistory} from '../masteragent/workbench_assets/history.mjs';

const A = {stateRoot: 'synthetic/root-one', installationId: 'A', generation: 2};
const B = {...A, installationId: 'B', generation: 1};
const envelope = (selection = A, data = {items: []}) => ({api_version: '3.1-dev1',
  source: {kind: 'masteragent', state_root: selection.stateRoot, installation_id: selection.installationId,
    generation: selection.generation}, observed_at: '2026-10-15T08:00:00Z', revision: null, data});
const response = (body, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => body});
function deferredFetcher() {
  const requests = [];
  const fetcher = (path, options) => new Promise((resolve, reject) => requests.push({path, options, resolve, reject}));
  return {requests, client: new SnapshotClient(fetcher)};
}

test('unknown values and initial empty state do not become successful zero usage', () => {
  const model = createModel();
  assert.equal(model.selection, null);
  assert.equal(model.snapshots.tasks.data, null);
  assert.equal(displayValue(null), '未采集');
  assert.equal(displayValue(undefined), '未采集');
  assert.equal(displayValue(0), '0');
  assert.equal(displayCount(null), '—');
  assert.equal(displayCount(0), '0');
  assert.equal(stateLabel('future_state'), 'future_state');
  assert.equal(stateLabel(null), '状态未知');
  assert.equal(formatTime('invalid'), '时间未知');
  assert.equal(formatTime(null), '未记录');
});

test('deployment refresh preserves a valid historical generation without enumerating generations', () => {
  const rows = [{installation_id: 'A', generation: Number.MAX_SAFE_INTEGER}, {installation_id: 'B', generation: 1}];
  assert.deepEqual(chooseInstallation(rows, 'A', {...A, generation: 1}, A.stateRoot), {...A, generation: 1});
  assert.equal(chooseInstallation(rows, 'A', null, A.stateRoot).generation, Number.MAX_SAFE_INTEGER);
  assert.deepEqual(chooseInstallation(rows, 'B', {...A, generation: 1}, 'synthetic/other'),
    {stateRoot: 'synthetic/other', installationId: 'A', generation: Number.MAX_SAFE_INTEGER});
  assert.deepEqual(chooseInstallation([rows[1]], 'B', A, A.stateRoot), B);
  assert.equal(chooseInstallation([], null, A, A.stateRoot), null);
});

test('changing business, generation or deployment root clears selection, cursors and cached detail', () => {
  const model = selectScope(createModel(), A);
  model.page = 'tasks'; model.selectedTaskId = 'old-task'; model.taskPageCount = 3;
  model.filters.state = 'running';
  model.snapshots.tasks = {...emptySnapshot(), data: {items: [{task_id: 'old-task'}], next_cursor: 'old-cursor'}};
  model.snapshots.detail = {...emptySnapshot(), data: {task: {text: 'scope-bound text'}}};
  assert.equal(selectScope(model, {...A}), model);
  for (const next of [B, {...A, generation: 1}, {...A, stateRoot: 'synthetic/root-two'}]) {
    const changed = selectScope(model, next);
    assert.notEqual(scopeKey(A), scopeKey(next));
    assert.equal(changed.page, 'tasks');
    assert.equal(changed.selectedTaskId, null);
    assert.equal(changed.taskPageCount, 1);
    assert.equal(changed.snapshots.tasks.data, null);
    assert.equal(changed.snapshots.detail.data, null);
    assert.equal(changed.filters.state, '');
  }
});

test('scope epochs reject an old A response after A to B to A, even if cancellation is ignored', async () => {
  const {client, requests} = deferredFetcher();
  client.setScope(A);
  const events = [];
  const old = client.load('detail', '/old-a', event => events.push(event));
  client.setScope(B); client.setScope(A);
  const current = client.load('detail', '/new-a', event => events.push(event));
  assert.equal(requests[0].options.signal.aborted, true);
  requests[1].resolve(response(envelope(A, {task: {text: 'current A'}})));
  assert.equal(await current, true);
  requests[0].resolve(response(envelope(A, {task: {text: 'old A'}})));
  assert.equal(await old, false);
  assert.deepEqual(events.filter(event => event.type === 'success').map(event => event.envelope.data.task.text), ['current A']);
});

test('new request replaces the same slot without aborting independent slots', async () => {
  const {client, requests} = deferredFetcher(); client.setScope(A);
  const events = [];
  const first = client.load('tasks', '/first', event => events.push(event));
  const team = client.load('team', '/team', () => {});
  const latest = client.load('tasks', '/latest', event => events.push(event));
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(requests[1].options.signal.aborted, false);
  requests[2].resolve(response(envelope(A, {items: [{task_id: 'new'}]})));
  requests[1].resolve(response(envelope(A, {roles: []})));
  requests[0].resolve(response(envelope(A, {items: [{task_id: 'old'}]})));
  assert.deepEqual(await Promise.all([first, team, latest]), [false, true, true]);
  assert.deepEqual(events.filter(event => event.type === 'success').map(event => event.envelope.data.items[0].task_id), ['new']);
});

test('same business IDs from a changed deployment root cannot overwrite the prior scope', async () => {
  const {client, requests} = deferredFetcher(); client.setScope(A);
  const events = [];
  const pending = client.load('overview', '/same-port', event => events.push(event));
  requests[0].resolve(response(envelope({...A, stateRoot: 'synthetic/root-two'}, {counts: {completed: 9}})));
  assert.equal(await pending, false);
  assert.equal(events.some(event => event.type === 'success'), false);
  assert.equal(events.at(-1).type, 'error');
  assert.throws(() => validateEnvelope(envelope({...A, generation: 1}), A), /代次/);
  assert.throws(() => validateEnvelope(envelope(B), A), /代次/);
  assert.throws(() => validateEnvelope({...envelope(A), api_version: 'other'}, A), /契约/);
  assert.throws(() => validateEnvelope({...envelope(A), source: {kind: 'legacy'}}, A), /契约/);
});

test('refresh errors preserve last known data and observation time instead of successful emptiness', async () => {
  let snapshot = reduceSnapshot(emptySnapshot(), {type: 'success', envelope: envelope(A, {items: [{task_id: 'saved'}]})});
  const {client, requests} = deferredFetcher(); client.setScope(A);
  const pending = client.load('tasks', '/unavailable', event => { snapshot = reduceSnapshot(snapshot, event); });
  assert.equal(snapshot.status, 'refreshing');
  requests[0].reject(new Error('synthetic connection closed'));
  assert.equal(await pending, false);
  assert.equal(snapshot.status, 'stale');
  assert.deepEqual(snapshot.data.items, [{task_id: 'saved'}]);
  assert.equal(snapshot.observedAt, '2026-10-15T08:00:00Z');
  assert.equal(reduceSnapshot(emptySnapshot(), {type: 'error', error: 'offline'}).data, null);
});

test('pagination merges repeated task IDs, retains order and updates changed summaries', () => {
  assert.deepEqual(mergeTasks([{task_id: 'one', state: 'running'}, {task_id: 'two'}],
    [null, {task_id: 'two', state: 'completed'}, {task_id: 'three'}]),
  [{task_id: 'one', state: 'running'}, {task_id: 'two', state: 'completed'}, {task_id: 'three'}]);
});

test('paginated reads share one scope; switching midway prevents even a partial commit', async () => {
  const {client, requests} = deferredFetcher(); client.setScope(A);
  const events = [];
  let secondStarted;
  const secondReady = new Promise(resolve => { secondStarted = resolve; });
  const pending = client.load('tasks', async (read, selection) => {
    const first = await read(installationPath(selection, 'tasks', {limit: 20}));
    const secondRequest = read(installationPath(selection, 'tasks', {cursor: first.data.next_cursor}));
    secondStarted();
    const second = await secondRequest;
    return {...second, data: {items: mergeTasks(first.data.items, second.data.items)}};
  }, event => events.push(event));
  requests[0].resolve(response(envelope(A, {items: [{task_id: 'first'}], next_cursor: 'synthetic-next'})));
  await secondReady;
  client.setScope(B);
  requests[1].resolve(response(envelope(A, {items: [{task_id: 'second'}], next_cursor: null})));
  assert.equal(await pending, false);
  assert.equal(events.some(event => event.type === 'success'), false);
  assert.equal(requests[1].options.signal.aborted, true);
});

test('all requests are readonly and the query safely carries exact scope and opaque cursors', async () => {
  const selection = {...A, installationId: 'synthetic A/业务'};
  const path = installationPath(selection, 'tasks', {limit: 20, cursor: 'x+/=&', state: 'running', role_id: 'reader'});
  const parsed = new URL(path, 'http://127.0.0.1');
  assert.equal(parsed.pathname, '/api/v1/installations/synthetic%20A%2F%E4%B8%9A%E5%8A%A1/tasks');
  assert.equal(parsed.searchParams.get('cursor'), 'x+/=&');
  assert.equal(parsed.searchParams.get('generation'), '2');
  const {client, requests} = deferredFetcher(); client.setScope(selection);
  const pending = client.load('tasks', path, () => {});
  assert.equal(requests[0].options.method, 'GET');
  assert.equal(requests[0].options.cache, 'no-store');
  requests[0].resolve(response(envelope(selection)));
  assert.equal(await pending, true);
});

test('format/migration/corruption failures are distinct from normal generation change', () => {
  const error = code => responseError({error: {code, message: 'source detail'}}, 409);
  assert.match(error('migration_required'), /显式迁移/);
  assert.match(error('schema_corrupt'), /完整性/);
  assert.match(error('snapshot_corrupt'), /完整性/);
  assert.match(error('unsupported_schema'), /不兼容/);
  assert.match(error('not_initialized'), /尚未初始化/);
  assert.match(error('generation_mismatch'), /代次已变化/);
  assert.equal(error('future_error'), 'source detail');
});

test('PID and online fields cannot stand in for a valid lease or model connectivity', () => {
  const old = memberPresentation({state: 'stopped', online: true, pid: 12345, lease_valid: false, accepting_tasks: true});
  assert.equal(old.label, '已停止');
  assert.match(old.detail, /没有有效成员租约/);
  const current = memberPresentation({lease_valid: true, accepting_tasks: true});
  assert.equal(current.label, '可接单');
  assert.match(current.detail, /尚未验证/);
  assert.equal(stateLabel('sending'), '发送中');
});

// Small in-memory DOM: rendering is exercised without opening a real server,
// private data, native processes or a browser session. HTML parsing is forbidden.
class SyntheticNode {
  constructor(tag, text = '') { this.tag = tag; this.text = text; this.children = []; this.attributes = {}; this.events = {}; }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.events[key] = fn; }
  set innerHTML(_) { throw new Error('Untrusted content must not use HTML parsing'); }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
}
function renderSynthetic(model, actions = new Proxy({}, {get: () => () => {}})) {
  const savedNode = globalThis.Node, savedDocument = globalThis.document;
  globalThis.Node = SyntheticNode;
  globalThis.document = {createElement: tag => new SyntheticNode(tag), createElementNS: (_, tag) => new SyntheticNode(tag),
    createTextNode: text => new SyntheticNode('#text', text), createDocumentFragment: () => new SyntheticNode('#fragment')};
  try { return renderView(model, actions); }
  finally { globalThis.Node = savedNode; globalThis.document = savedDocument; }
}
function nodes(root) { return [root, ...root.children.flatMap(nodes)]; }
function ready(data) { return reduceSnapshot(emptySnapshot(), {type: 'success', envelope: envelope(A, data)}); }

test('task body and saved result display literal untrusted text, never executable nodes', () => {
  const malicious = '<img src=x onerror="throw 1"><script>throw 2</script>';
  const model = selectScope(createModel(), A); model.page = 'tasks'; model.selectedTaskId = 'synthetic-task'; model.detailTab = 'result';
  model.snapshots.tasks = ready({items: [], next_cursor: null});
  model.snapshots.detail = ready({task: {task_id: 'synthetic-task', text: malicious, state: 'completed', role_id: 'reader', generation: 2, dispatch_generation: 2},
    attempts: [], events: [], inputs: [], decisions: [], history_available: true,
    result: {output: malicious, status: 'success'}, truncated: {}});
  const dom = renderSynthetic(model);
  assert.equal(dom.textContent.split(malicious).length - 1, 2);
  assert.equal(nodes(dom).some(node => ['script', 'img', 'iframe'].includes(node.tag)), false);
  assert.match(dom.textContent, /当前详情不包含完整跨代执行链/);
});

test('connection cards keep unknown actual model and usage visible and Claude unavailable', () => {
  const model = selectScope(createModel(), A); model.page = 'connections';
  model.snapshots.connections = ready({items: [
    {runtime: 'codex', label: 'Codex', supported: true, status: 'not_verified', model_requested: null, model_observed: null, usage: null},
    {runtime: 'claude', supported: false, status: 'unavailable', model_observed: null, usage: null},
  ]});
  const text = renderSynthetic(model).textContent;
  assert.match(text, /请求模型未采集/);
  assert.match(text, /实际模型未采集/);
  assert.match(text, /用量记录未采集/);
  assert.match(text, /Claude Code.*暂不可用/);
  assert.doesNotMatch(text, /请求模型未配置|Codex 在线|用量记录0/);
});

function definitionValues(dom) {
  return new Map(nodes(dom).filter(node => node.tag === 'dl').flatMap(node =>
    node.children.filter((_, index) => index % 2 === 0).map((label, index) => [label.textContent, node.children[index * 2 + 1].textContent])));
}
function runtimeTaskModel(runtime, events = []) {
  const model = selectScope(createModel(), A); model.page = 'tasks'; model.selectedTaskId = 'synthetic-task';
  model.snapshots.tasks = ready({items: []});
  model.snapshots.detail = ready({task: {task_id: 'synthetic-task', text: 'Synthetic work', state: 'completed', role_id: 'reader',
    generation: 2, dispatch_generation: 2}, attempts: [{attempt_id: 'synthetic-attempt', ordinal: 1, generation: 2,
    state: 'completed', runtime}], events, notifications: [], truncated: {}});
  return model;
}

function resultTaskModel(deliveries) {
  const model = runtimeTaskModel({}); model.detailTab = 'result';
  model.snapshots.detail.data.result = {attempt_id: 'synthetic-attempt', generation: 2,
    output: 'Synthetic saved final text', status: 'success', created_at: '2026-10-19T03:00:00Z'};
  if (deliveries !== undefined) model.snapshots.detail.data.deliveries = deliveries;
  return model;
}
const deliveryFixture = (state, overrides = {}) => ({delivery_id: 'delivery-01', attempt_id: 'synthetic-attempt',
  task_id: 'synthetic-task', generation: 2, destination_namespace: 'synthetic-local', destination_id: 'receiver-01',
  state, reason: null, send_count: 1, created_at: '2026-10-19T03:01:00Z', updated_at: '2026-10-19T03:02:00Z',
  received_at: null, receipt: null, agent_processed: null, user_read: null, ...overrides});

test('saved results and durable destination receipts stay separate from agent processing and user read', () => {
  const delivery = deliveryFixture('received', {received_at: '2026-10-19T03:02:00Z',
    receipt: {receipt_id: 'receipt-01', text_bytes: 24001, text_sha256: 'not-for-generic-ui', storage_revision: 6,
      received_at: '2026-10-19T03:02:00Z'}, agent_processed: true, user_read: true,
    claim_token: 'never-display-claim', envelope: 'never-display-envelope'});
  const dom = renderSynthetic(resultTaskModel({items: [delivery], truncated: false})), values = definitionValues(dom);
  assert.match(dom.textContent, /已保存成果/);
  assert.match(dom.textContent, /目的端已持久保存/);
  assert.equal(values.get('平台成果'), '已保存');
  assert.equal(values.get('目的端接收'), '已持久保存');
  assert.equal(values.get('agent 处理'), '未知');
  assert.equal(values.get('用户已读'), '未知');
  assert.equal(values.get('持久回执'), 'receipt-01');
  assert.equal(values.get('接收正文大小'), '24001 UTF-8 字节');
  assert.equal(values.get('接收存储修订'), '6');
  assert.match(dom.textContent, /四项独立事实/);
  assert.doesNotMatch(dom.textContent, /not-for-generic-ui|never-display/);
  const card = nodes(dom).find(node => node.attributes['aria-label'] === '结果投递');
  assert.equal(nodes(card).some(node => ['button', 'form', 'input', 'textarea'].includes(node.tag)), false);
});

test('pending, claimed, sending, held and rejected delivery states never claim destination receipt or retry a model', () => {
  const labels = {pending: '待发送', claimed: '已领取发送', sending: '发送中，待回执', held: '待核对', rejected: '已拒绝投递'};
  for (const [state, label] of Object.entries(labels)) {
    const delivery = deliveryFixture(state, {send_count: 0, receipt: {receipt_id: 'unconfirmed-receipt'}});
    const dom = renderSynthetic(resultTaskModel({items: [delivery], truncated: false})), values = definitionValues(dom);
    assert.match(dom.textContent, new RegExp(label));
    assert.equal(values.get('平台成果'), '已保存');
    assert.equal(values.get('目的端接收'), '尚未确认');
    assert.equal(values.get('agent 处理'), '未知'); assert.equal(values.get('用户已读'), '未知');
    assert.equal(values.get('发送调用次数'), '0');
    assert.equal(values.has('持久回执'), false);
    if (state === 'held') assert.match(dom.textContent, /停止自动发送.*核对原投递记录.*不要重跑模型/);
    const card = nodes(dom).find(node => node.attributes['aria-label'] === '结果投递');
    assert.equal(nodes(card).some(node => node.tag === 'button' || node.tag === 'form'), false);
  }
});

test('old servers, empty delivery lists and missing results remain distinguishable without invented receipt counts', () => {
  const oldModel = resultTaskModel();
  const oldDom = renderSynthetic(oldModel);
  assert.match(oldDom.textContent, /此服务未提供结果投递记录.*接收状态未知/);
  assert.doesNotMatch(oldDom.textContent, /没有结果投递记录|发送调用次数0/);
  oldModel.snapshots.detail.data.result = null;
  assert.match(renderSynthetic(oldModel).textContent, /尚未保存成果.*结果投递.*此服务未提供/);
  const emptyModel = resultTaskModel({items: [], truncated: false}); emptyModel.snapshots.detail.data.result = null;
  const emptyDom = renderSynthetic(emptyModel);
  assert.match(emptyDom.textContent, /尚未保存成果.*结果投递.*当前所选记录范围没有结果投递记录/);
  assert.doesNotMatch(emptyDom.textContent, /此服务未提供|发送调用次数0/);
});

test('bounded delivery summaries preserve unknown fields and render untrusted destination and reason as text', () => {
  const untrusted = '<img src=x onerror="throw 1"><script>throw 2</script>';
  const delivery = deliveryFixture('future-delivery-state', {destination_id: untrusted, reason: untrusted,
    send_count: null, generation: 0, receipt: null});
  const dom = renderSynthetic(resultTaskModel({items: [delivery], truncated: true})), values = definitionValues(dom);
  assert.match(dom.textContent, /此版本无法解释该投递状态/);
  assert.match(dom.textContent, /只展示部分投递记录.*未显示的记录不能视为不存在/);
  assert.equal(values.get('发送调用次数'), '未记录'); assert.equal(values.get('记录代次'), '0');
  assert.equal(values.get('目的端接收'), '尚未确认');
  assert.equal(dom.textContent.split(untrusted).length - 1, 2);
  assert.equal(nodes(dom).some(node => ['script', 'img', 'iframe'].includes(node.tag)), false);
  for (const state of ['constructor', 'toString', '__proto__']) {
    const futureDom = renderSynthetic(resultTaskModel({items: [deliveryFixture(state)], truncated: false}));
    assert.match(futureDom.textContent, /此版本无法解释该投递状态/);
  }
});

test('connection model reports remain separate from actual inference and show numeric usage with its evidence scope', () => {
  const model = selectScope(createModel(), A); model.page = 'connections';
  model.snapshots.connections = ready({items: [{runtime: 'codex', label: 'Codex', supported: true, model_requested: 'requested-model',
    model_reported: 'server-configured-model', model_reported_source: 'thread/start', model_observed: null,
    usage: {input_tokens: 12345, cached_input_tokens: 4321, cache_write_input_tokens: 0, output_tokens: 678,
      reasoning_output_tokens: 90, total_tokens: 13023}, usage_source: 'thread/tokenUsage/updated', usage_scope: 'fresh_thread_total',
    latest_attempt: {attempt_id: 'synthetic-attempt'}}]});
  const dom = renderSynthetic(model), values = definitionValues(dom);
  assert.equal(values.get('请求模型'), 'requested-model');
  assert.equal(values.get('服务端报告模型'), 'server-configured-model');
  assert.equal(values.get('实际模型'), '未采集');
  assert.equal(values.get('输入 token'), '12,345');
  assert.equal(values.get('缓存命中 token'), '4,321');
  assert.equal(values.get('缓存写入 token'), '0');
  assert.equal(values.get('输出 token'), '678');
  assert.equal(values.get('推理输出 token'), '90');
  assert.equal(values.get('总 token'), '13,023');
  assert.match(values.get('模型报告来源'), /会话启动报告.*thread\/start/);
  assert.match(values.get('用量来源'), /thread\/tokenUsage\/updated/);
  assert.match(values.get('统计范围'), /新建原生会话累计.*非单轮增量/);
  assert.match(dom.textContent, /不证明实际推理所用模型/);
  assert.match(dom.textContent, /不是当前连接状态/);
  assert.doesNotMatch(dom.textContent, /"input_tokens"|\[object Object\]|实际模型server-configured-model/);
});

test('task observation and denied native requests render facts without granting approval or exposing request bodies', () => {
  const runtime = {runtime: 'codex', model_requested: 'requested-model', model_reported: 'rerouted-model', model_reported_source: 'model/rerouted',
    model_observed: null, usage: {input_tokens: 100, output_tokens: 20, total_tokens: 120},
    usage_source: 'thread/tokenUsage/updated', usage_scope: 'fresh_thread_total', last_observation_at: '2026-10-19T06:00:00Z',
    native_requests_denied: {count: 2, last_method: 'item/fileChange/requestApproval', last_at: '2026-10-19T06:01:00Z'}};
  const dom = renderSynthetic(runtimeTaskModel(runtime, [
    {kind: 'runtime_observation', created_at: '2026-10-19T06:00:00Z', detail: runtime},
    {kind: 'runtime_native_request_denied', created_at: '2026-10-19T06:01:00Z',
      detail: {method: 'item/fileChange/requestApproval', reason: 'text_only_mode', command: 'SECRET_COMMAND', patch: 'SECRET_PATCH'}},
  ])), values = definitionValues(dom);
  assert.equal(values.get('服务端报告模型'), 'rerouted-model');
  assert.equal(values.get('实际模型'), '未采集');
  assert.match(values.get('模型报告来源'), /模型改道报告.*model\/rerouted/);
  assert.equal(values.get('自动拒绝次数'), '2 次');
  assert.equal(values.get('最近请求类型'), 'item/fileChange/requestApproval');
  assert.notEqual(values.get('最近事实记录'), '未记录');
  assert.match(dom.textContent, /运行事实已更新/);
  assert.match(dom.textContent, /输入 token：100.*输出 token：20.*总 token：120/);
  assert.match(dom.textContent, /原生工具请求已自动拒绝/);
  assert.match(dom.textContent, /请求类型：item\/fileChange\/requestApproval/);
  assert.match(dom.textContent, /说明：text_only_mode/);
  assert.match(dom.textContent, /纯文本模式自动拒绝.*未开放人工批准/);
  assert.doesNotMatch(dom.textContent, /SECRET_COMMAND|SECRET_PATCH|"input_tokens"/);
  assert.equal(nodes(dom).some(node => node.tag === 'button' && /批准/.test(node.textContent)), false);
});

test('legacy runtime records preserve known values while missing model reports, totals and evidence stay unknown', () => {
  const dom = renderSynthetic(runtimeTaskModel({runtime: 'codex', model_requested: 'legacy-model', model_observed: null,
    usage: {input_tokens: 50, cached_input_tokens: 0, output_tokens: 7, reasoning_output_tokens: 2}}));
  const values = definitionValues(dom);
  assert.equal(values.get('服务端报告模型'), '未采集');
  assert.equal(values.get('模型报告来源'), '未采集');
  assert.equal(values.get('实际模型'), '未采集');
  assert.equal(values.get('输入 token'), '50');
  assert.equal(values.get('缓存命中 token'), '0');
  assert.equal(values.get('缓存写入 token'), '未采集');
  assert.equal(values.get('总 token'), '未采集');
  assert.equal(values.get('用量来源'), '未采集');
  assert.equal(values.get('统计范围'), '未采集');
  assert.equal(values.get('最近事实记录'), '未记录');
  assert.equal(values.get('自动拒绝次数'), '未记录');
  assert.doesNotMatch(dom.textContent, /总 token57|服务端报告模型legacy-model/);
});

test('unavailable or invalid token observations never render as successful zero usage', () => {
  for (const usage of [null, undefined, {}, {input_tokens: null, cached_input_tokens: true, cache_write_input_tokens: -1,
    output_tokens: '123', reasoning_output_tokens: Number.NaN, total_tokens: 1.5}]) {
    const dom = renderSynthetic(runtimeTaskModel({runtime: 'codex', usage})), values = definitionValues(dom);
    if (usage == null) assert.equal(values.get('用量记录'), '未采集');
    else for (const label of ['输入 token', '缓存命中 token', '缓存写入 token', '输出 token', '推理输出 token', '总 token'])
      assert.equal(values.get(label), '未采集');
    assert.doesNotMatch(dom.textContent, /token0|用量记录0/);
  }
});

test('team chooses the newest nonlive member when no current valid lease exists', () => {
  const model = selectScope(createModel(), A); model.page = 'team';
  model.snapshots.team = ready({roles: [{role_id: 'reader', display_name: '资料员', tools: []}], members: [
    {role_id: 'reader', generation: 2, lease_valid: false, state: 'stopped', active_attempt_id: 'newest00'},
    {role_id: 'reader', generation: 2, lease_valid: false, state: 'expired', active_attempt_id: 'oldest00'},
  ], truncated: {members: false}});
  const text = renderSynthetic(model).textContent;
  assert.match(text, /newest00/); assert.doesNotMatch(text, /oldest00/);
});

test('empty deployment and failed task list render different outcomes', () => {
  const emptyModel = createModel(); emptyModel.deployment = ready({initialized: false, installations: []});
  assert.match(renderSynthetic(emptyModel).textContent, /尚未初始化/);
  const errorModel = selectScope(createModel(), A); errorModel.page = 'tasks';
  errorModel.snapshots.tasks = reduceSnapshot(emptySnapshot(), {type: 'error', error: 'synthetic offline'});
  const text = renderSynthetic(errorModel).textContent;
  assert.match(text, /synthetic offline/); assert.doesNotMatch(text, /没有符合/);
});

function writableModel() {
  const model = selectScope(createModel(), A);
  model.workbench = ready({commands: {enabled: true, csrf_token: 'synthetic-csrf'}, legacy_console: {configured: false, url: null}});
  model.deployment = ready({active_installation_id: 'A', installations: [{installation_id: 'A', state: 'active', generation: 2}]});
  return model;
}
function deferredCommands() {
  const requests = []; let serial = 0;
  const store = new CommandStore((path, options) => new Promise((resolve, reject) => requests.push({path, options, resolve, reject})), () => `synthetic-${++serial}`);
  store.setScope(A);
  return {store, requests};
}
const commandEnvelope = (action = 'submit', selection = A, result = {task_id: 'saved-task'}) => envelope(selection, {action, result});

test('writes require fresh same-root metadata, an enabled service and current active generation', () => {
  const model = writableModel(); assert.equal(commandGate(model), null);
  model.workbench.data.commands.enabled = false; assert.match(commandGate(model), /只读/);
  model.workbench.data.commands.enabled = true;
  model.workbench.status = 'stale'; assert.match(commandGate(model), /配置尚未确认/);
  model.workbench.status = 'ready'; model.selection = {...A, generation: 1}; assert.match(commandGate(model), /历史/);
  model.selection = B; assert.match(commandGate(model), /未启用/);
  model.selection = {...A, stateRoot: 'another/root'}; assert.match(commandGate(model), /配置尚未确认/);
  model.selection = A; model.deployment.status = 'refreshing'; assert.match(commandGate(model), /部署快照/);
});

test('decision choice is explicit, generation is bound, and stop does not fabricate an exit receipt', () => {
  assert.throws(() => commandPayload(A, 'answer', {}, {task_id: 't', decision_id: 'd', expected_revision: 2}, () => 'answer'), /明确选择/);
  assert.deepEqual(commandPayload(A, 'answer', {choice: 'reject'}, {task_id: 't', decision_id: 'd', expected_revision: 2}, () => 'answer'),
    {generation: 2, task_id: 't', decision_id: 'd', expected_revision: 2, choice: 'reject', answer_id: 'answer'});
  assert.throws(() => commandPayload(A, 'stop', {reason: '  '}, {}, () => 'unused'), /停止原因/);
  assert.match(commandReceipt('stop', {state: 'stopping'}), /尚未确认运行进程退出/);
  assert.match(commandReceipt('input', {}), /已保存.*不构成审批/);
});

test('duplicate command clicks make one same-origin POST and lock the submitted payload', async () => {
  const {store, requests} = deferredCommands();
  store.edit(A, 'submit', '', 'role_id', 'reader'); store.edit(A, 'submit', '', 'text', 'synthetic work');
  const pending = store.send(A, 'submit', '', {}, 'synthetic-token');
  assert.equal(await store.send(A, 'submit', '', {}, 'synthetic-token'), false);
  assert.equal(requests.length, 1); assert.equal(store.hasUnsaved(), true);
  assert.equal(store.edit(A, 'submit', '', 'text', 'changed after submit'), false);
  assert.equal(store.reset(A, 'submit'), false);
  assert.equal(commandLocked(store.get(A, 'submit')), true);
  assert.equal(requests[0].path, '/api/v1/installations/A/commands/submit');
  assert.equal(requests[0].options.method, 'POST');
  assert.equal(requests[0].options.credentials, 'same-origin');
  assert.equal(requests[0].options.headers['X-Workbench-CSRF'], 'synthetic-token');
  assert.equal(requests[0].options.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(requests[0].options.body), {generation: 2, role_id: 'reader', text: 'synthetic work', request_id: 'synthetic-1'});
  requests[0].resolve(response(commandEnvelope())); assert.equal(await pending, true);
  assert.equal(store.hasUnsaved(), false); assert.equal(await store.send(A, 'submit', '', {}, 'synthetic-token'), false);
});

test('lost command replies retain exact IDs and content for explicit retry, never auto replay', async () => {
  const {store, requests} = deferredCommands();
  store.edit(A, 'input', 't-a', 'text', 'supplement');
  const first = store.send(A, 'input', 't-a', {task_id: 't', attempt_id: 'a'}, 'token');
  requests[0].reject(new Error('connection lost after commit')); assert.equal(await first, false);
  assert.equal(requests.length, 1); assert.equal(store.get(A, 'input', 't-a').status, 'uncertain');
  assert.equal(store.edit(A, 'input', 't-a', 'text', 'different'), false);
  assert.equal(store.reset(A, 'input', 't-a'), false);
  const retry = store.send(A, 'input', 't-a', {task_id: 'other', attempt_id: 'other'}, 'new-token');
  assert.equal(requests[0].options.body, requests[1].options.body);
  requests[1].resolve(response(commandEnvelope('input', A, {state: 'pending'}))); assert.equal(await retry, true);
  assert.match(store.get(A, 'input', 't-a').message, /已保存/);
});

test('server errors and malformed or wrong-scope receipts stay uncertain instead of creating new IDs', async () => {
  for (const reply of [response({error: {message: 'server error'}}, 503), response(commandEnvelope('submit', B)),
    {ok: true, status: 200, json: async () => { throw new Error('truncated'); }}]) {
    const {store, requests} = deferredCommands();
    store.edit(A, 'submit', '', 'role_id', 'reader'); store.edit(A, 'submit', '', 'text', 'work');
    const pending = store.send(A, 'submit', '', {}, 'token'); requests[0].resolve(reply); await pending;
    assert.equal(store.get(A, 'submit').status, 'uncertain');
    assert.equal(store.get(A, 'submit').payload.request_id, 'synthetic-1');
  }
});

test('explicit client rejection permits a deliberate new draft with a new identity', async () => {
  const {store, requests} = deferredCommands();
  store.edit(A, 'submit', '', 'role_id', 'reader'); store.edit(A, 'submit', '', 'text', 'first');
  const first = store.send(A, 'submit', '', {}, 'token'); requests[0].resolve(response({error: {message: 'role rejected'}}, 409));
  await first; assert.equal(store.get(A, 'submit').status, 'rejected');
  assert.equal(store.edit(A, 'submit', '', 'text', 'second'), false);
  assert.equal(store.reset(A, 'submit'), true);
  store.edit(A, 'submit', '', 'role_id', 'reader'); store.edit(A, 'submit', '', 'text', 'second');
  const second = store.send(A, 'submit', '', {}, 'token');
  assert.equal(JSON.parse(requests[1].options.body).request_id, 'synthetic-2');
  requests[1].resolve(response(commandEnvelope())); await second;
});

test('a rejected retry cannot erase an earlier uncertain commit or unlock a fresh identity', async () => {
  const {store, requests} = deferredCommands();
  store.edit(A, 'input', 't-a', 'text', 'one supplement');
  const first = store.send(A, 'input', 't-a', {task_id: 't', attempt_id: 'a'}, 'token');
  requests[0].reject(new Error('reply lost after commit')); await first;
  const retry = store.send(A, 'input', 't-a', {task_id: 't', attempt_id: 'a'}, 'token');
  requests[1].resolve(response({error: {code: 'generation_mismatch', message: 'generation changed'}}, 409)); await retry;
  assert.equal(store.get(A, 'input', 't-a').status, 'uncertain');
  assert.match(store.get(A, 'input', 't-a').message, /原提交的结果仍未确认/);
  assert.equal(store.reset(A, 'input', 't-a'), false);
  assert.equal(store.edit(A, 'input', 't-a', 'text', 'new duplicate'), false);
  assert.equal(store.get(A, 'input', 't-a').payload.request_id, 'synthetic-1');
  assert.equal(store.hasUnsaved(), true);
});

test('drafts are partitioned by root, business, generation, task and action; late replies do not notify a new scope', async () => {
  const {store, requests} = deferredCommands();
  store.edit(A, 'input', 'task-one-attempt-one', 'text', 'private to A scope');
  for (const selection of [B, {...A, generation: 1}, {...A, stateRoot: 'second/root'}]) assert.deepEqual(store.get(selection, 'input', 'task-one-attempt-one').fields, {});
  assert.deepEqual(store.get(A, 'input', 'task-two-attempt-one').fields, {});
  assert.deepEqual(store.get(A, 'submit', 'task-one-attempt-one').fields, {});
  let notices = 0;
  const pending = store.send(A, 'input', 'task-one-attempt-one', {task_id: 'task-one', attempt_id: 'attempt-one'}, 'token', () => notices++);
  assert.equal(notices, 1);
  store.setScope(B); store.setScope(A);
  requests[0].resolve(response(commandEnvelope('input'))); await pending;
  assert.equal(notices, 1);
  assert.equal(store.get(A, 'input', 'task-one-attempt-one').fields.text, 'private to A scope');
  assert.equal(store.get(B, 'input', 'task-one-attempt-one').status, 'draft');
});

test('all 21 original entries have a destination and safe explicit compatibility links', () => {
  assert.deepEqual(FEATURE_DIRECTORY.map(row => row.id), ['fleet', 'guide', 'agentprofile', 'bi', 'usage', 'caps', 'ctx', 'waitreg', 'prompts', 'archive',
    'tasklines', 'groups', 'mechanisms', 'roles', 'bizroles', 'longrange', 'tasks', 'live', 'paths', 'fleetlog', 'asr']);
  assert.equal(new Set(FEATURE_DIRECTORY.map(row => row.id)).size, 21);
  assert.equal(legacyLink({configured: true, url: 'http://127.0.0.1:8888/'}, 'longrange'), 'http://127.0.0.1:8888/#longrange');
  for (const url of ['https://example.org/', 'http://evil.example/', 'javascript:alert(1)', 'http://localhost@evil.example/', 'http://user:secret@localhost/', 'http://127.0.0.1/?token=x', 'http://127.0.0.1/#evil',
    'http://127.0.0.1/', 'http://localhost:8888/private/path', 'http://[::1]:8888/', 'http://localhost:0/', 'http://localhost:99999/'])
    assert.equal(legacyLink({configured: true, url}, 'fleet'), null);
  assert.equal(legacyLink({configured: false, url: 'http://localhost/'}, 'fleet'), null);
  assert.equal(legacyLink({configured: true, url: 'http://localhost/'}, 'unlisted'), null);
  const model = writableModel(); model.section = 'directory'; model.workbench.data.legacy_console = {configured: true, url: 'http://127.0.0.1:8888/'};
  const dom = renderSynthetic(model); const links = nodes(dom).filter(node => node.tag === 'a');
  assert.equal(links.length, 21);
  for (const link of links) { assert.equal(link.attributes.target, '_blank'); assert.equal(link.attributes.rel, 'noopener noreferrer'); }
  assert.equal(nodes(dom).some(node => ['iframe', 'script'].includes(node.tag)), false);
  assert.match(dom.textContent, /未验证旧服务是否在线/);
  assert.equal(FEATURE_DIRECTORY.some(row => row.coverage === 'complete'), false);
});

test('readonly composer is disabled and refresh renders the same saved draft without turning choice into approval', () => {
  const model = writableModel(); model.page = 'tasks'; model.composerOpen = true;
  model.snapshots.tasks = ready({items: []}); model.snapshots.team = ready({roles: [{role_id: 'reader'}]});
  const {store} = deferredCommands(); store.edit(A, 'submit', '', 'role_id', 'reader'); store.edit(A, 'submit', '', 'text', 'keep my draft');
  const actions = new Proxy({commandState: (action, target) => store.get(A, action, target)}, {get: (target, key) => target[key] || (() => {})});
  for (let refresh = 0; refresh < 2; refresh++) {
    const dom = renderSynthetic(model, actions);
    const textarea = nodes(dom).find(node => node.tag === 'textarea'); assert.equal(textarea.value, 'keep my draft');
  }
  model.workbench.data.commands.enabled = false;
  const dom = renderSynthetic(model, actions);
  assert.equal(nodes(dom).find(node => node.tag === 'textarea').disabled, true);
  assert.match(dom.textContent, /只读模式/);
});

test('empty deployments can still reach local help and the full migration directory', () => {
  const model = createModel(); model.deployment = ready({initialized: false, installations: []});
  model.section = 'help'; assert.match(renderSynthetic(model).textContent, /不是运行时|原生工具审批/);
  model.section = 'directory'; const dom = renderSynthetic(model);
  assert.match(dom.textContent, /语音转写/); assert.match(dom.textContent, /兼容入口未配置/);
  assert.equal(nodes(dom).filter(node => node.tag === 'a').length, 0);
});


test('handoff absence requires complete fresh same-scope members; unknown, stale and truncated never say nobody', () => {
  const complete = ready({members: [], truncated: {members: false}});
  assert.equal(memberSnapshotPresentation(complete, A, 'reader').label, '尚无常驻成员');
  assert.match(memberSnapshotPresentation(complete, A, 'reader').detail, /仍可保存排队/);
  for (const snapshot of [emptySnapshot(), {...complete, status: 'stale'}, {...complete, status: 'refreshing'},
    ready({members: [], truncated: {members: true}}), ready({members: []}),
    {...complete, source: envelope(B).source}, {...complete, source: envelope({...A, generation: 1}).source},
    {...complete, source: envelope({...A, stateRoot: 'other/root'}).source}]) {
    assert.equal(memberSnapshotPresentation(snapshot, A, 'reader').label, '接单状态未知');
  }
  const mixed = ready({members: [
    {role_id: 'other', generation: 2, lease_valid: true, accepting_tasks: true},
    {role_id: 'reader', generation: 1, lease_valid: true, accepting_tasks: true},
  ], truncated: {members: false}});
  assert.equal(memberSnapshotPresentation(mixed, A, 'reader').member, null);
  const invalid = ready({members: [{role_id: 'reader', generation: 2, accepting_tasks: true}], truncated: {members: false}});
  assert.equal(memberSnapshotPresentation(invalid, A, 'reader').label, '接单状态未知');
});

test('saving without members stays available and opens the exact saved task only on explicit click', () => {
  const model = writableModel(); model.page = 'tasks'; model.composerOpen = true;
  model.snapshots.tasks = ready({items: []});
  model.snapshots.team = ready({roles: [{role_id: 'reader'}], members: [], truncated: {members: false}});
  const {store} = deferredCommands(); store.edit(A, 'submit', '', 'role_id', 'reader'); store.edit(A, 'submit', '', 'text', 'queued text');
  let opened = null;
  const actions = new Proxy({commandState: (action, target) => store.get(A, action, target), openTask: id => { opened = id; }},
    {get: (target, key) => target[key] || (() => {})});
  const draft = renderSynthetic(model, actions);
  assert.notEqual(nodes(draft).find(node => node.tag === 'button' && node.attributes.type === 'submit').disabled, true);
  assert.match(draft.textContent, /尚无常驻成员.*仍可保存排队/);
  const record = store.get(A, 'submit'); record.status = 'success'; record.result = {task_id: 'exact-saved-task'};
  record.message = commandReceipt('submit', record.result);
  const saved = renderSynthetic(model, actions);
  assert.equal(opened, null);
  nodes(saved).find(node => node.tag === 'button' && node.textContent === '查看已保存任务').events.click();
  assert.equal(opened, 'exact-saved-task');
  assert.match(saved.textContent, /没有启动模型/);
});

test('details keep their own observation time and exact claim association separate from member readiness', () => {
  const model = runtimeTaskModel({});
  model.snapshots.detail.observedAt = '2026-10-19T03:14:00Z'; model.snapshots.tasks.observedAt = '2026-10-19T05:59:00Z';
  model.snapshots.detail.data.task.state = 'running';
  model.snapshots.detail.data.attempts = [{attempt_id: 'new-attempt', ordinal: 4, created_at: '2026-10-19T03:00:00Z'},
    {attempt_id: 'old-attempt', ordinal: 1, created_at: '2026-10-18T03:00:00Z'}];
  model.snapshots.team = ready({members: [{role_id: 'reader', generation: 2, lease_valid: true,
    member_id: 'exact-member', active_attempt_id: 'new-attempt', owned_attempt_active: true}], truncated: {members: false}});
  const dom = renderSynthetic(model), values = definitionValues(dom);
  assert.match(dom.textContent, new RegExp(`详情快照：${formatTime(model.snapshots.detail.observedAt)}`));
  assert.match(dom.textContent, /公开事件快照，需刷新/);
  assert.match(values.get('领取执行'), /第 4 次.*new-atte/);
  assert.match(values.get('成员归属'), /exact-me/);
  assert.equal(values.get('模型连通'), '尚未验证');
  model.snapshots.team.data.members[0].active_attempt_id = 'unrelated-attempt';
  assert.match(definitionValues(renderSynthetic(model)).get('成员归属'), /未在此快照中提供精确成员关联/);
});

test('running one-shot attempts remain visible with absent, expired or stale persistent member snapshots', () => {
  const model = runtimeTaskModel({});
  model.snapshots.detail.data.task.state = 'running';
  model.snapshots.detail.data.attempts = [{attempt_id: 'one-shot-attempt', ordinal: 1, state: 'running',
    created_at: '2026-10-19T03:00:00Z'}];
  const noMembers = ready({members: [], truncated: {members: false}});
  const expired = ready({members: [{role_id: 'reader', generation: 2, member_id: 'expired-member',
    lease_valid: false, state: 'expired', active_attempt_id: null}], truncated: {members: false}});
  for (const snapshot of [noMembers, expired, {...noMembers, status: 'stale'}]) {
    model.snapshots.team = snapshot;
    const dom = renderSynthetic(model), values = definitionValues(dom);
    assert.match(values.get('领取执行'), /第 1 次.*one-shot/);
    assert.match(values.get('领取状态'), /执行中/);
    assert.match(values.get('成员归属'), /未在此快照中提供精确成员关联/);
    assert.doesNotMatch(dom.textContent, /执行服务尚未就绪|服务未就绪|尚未领取|没有执行/);
  }
  model.snapshots.team = noMembers;
  assert.match(renderSynthetic(model).textContent, /没有同业务、代次和角色的常驻成员.*是否已被领取请查看任务记录/);
});

const historyData = (context, items = []) => ({scope: {kind: 'task_history', task_id: context.taskId},
  task: {task_id: context.taskId, role_id: 'reader', generation: 0, dispatch_generation: 2, state: 'queued', text: 'synthetic task'},
  section: context.section, record_generation: context.recordGeneration, readonly: true, items, next_cursor: null});
const directoryContext = {taskId: 'continued-task', section: 'generations', recordGeneration: null, cursor: null};
function historyModel(context = directoryContext, items = []) {
  const model = writableModel(); model.page = 'tasks'; model.detailTab = 'history'; model.selectedTaskId = context.taskId;
  model.workbench.data.capabilities = {task_history: true};
  model.historySection = context.section; model.historyGeneration = context.recordGeneration;
  model.snapshots.history = ready(historyData(context, items));
  return model;
}

test('older workbench responses remain valid and do not advertise unsupported history', () => {
  const model = writableModel(); model.page = 'tasks'; model.selectedTaskId = 'continued-task';
  assert.equal(historySupported(model), false);
  assert.doesNotThrow(() => validateEnvelope(envelope(A, model.workbench.data), A));
  assert.equal(nodes(renderSynthetic(model)).some(node => node.attributes.id === 'detail-tab-history'), false);
  model.detailTab = 'history'; model.composerOpen = true;
  assert.match(renderSynthetic(model).textContent, /尚未声明跨代任务链能力/);
  assert.equal(nodes(renderSynthetic(model)).some(node => node.tag === 'form'), false);
  model.workbench.data.capabilities = {task_history: true}; assert.equal(historySupported(model), true);
  model.workbench.status = 'stale'; assert.equal(historySupported(model), false);
  model.workbench.status = 'ready'; model.workbench.source.state_root = 'other/root'; assert.equal(historySupported(model), false);
});

test('a current-detail 404 still exposes independent history access and complete ID lookup', () => {
  const model = historyModel(); model.detailTab = 'timeline';
  model.snapshots.detail = reduceSnapshot(emptySnapshot(), {type: 'error', error: '所选记录不存在'});
  let selected = null;
  const actions = new Proxy({changeDetailTab: tab => { selected = tab; }}, {get: (target, key) => target[key] || (() => {})});
  const dom = renderSynthetic(model, actions);
  assert.match(dom.textContent, /所选记录不存在/);
  assert.match(dom.textContent, /旧代详情可能不可用/);
  nodes(dom).find(node => node.attributes.id === 'detail-tab-history').events.click();
  assert.equal(selected, 'history');
  assert.ok(nodes(dom).find(node => node.attributes.id === 'history-task-id'));
});

test('history directory labels task ownership and opens queued-only generations without broadening top-level scope', () => {
  const model = historyModel(directoryContext, [{generation: 2, is_original: false, is_current_dispatch: true, is_installation_current: true},
    {generation: 0, is_original: true, is_current_dispatch: false, is_installation_current: false}]);
  model.selection = {...A, generation: 0};
  let opened = null;
  const actions = new Proxy({openHistoryGeneration: generation => { opened = generation; }}, {get: (target, key) => target[key] || (() => {})});
  const dom = renderSynthetic(model, actions);
  assert.match(dom.textContent, /任务当前归属.*第 2 代/);
  assert.match(dom.textContent, /顶部查询上下文第 0 代/);
  assert.match(dom.textContent, /任务创建代次/);
  nodes(dom).filter(node => node.tag === 'button' && node.textContent === '查看此代记录')[1].events.click();
  assert.equal(opened, 0); assert.equal(model.selection.generation, 0);
});

test('every history section renders readonly even for current-generation pending decisions and active attempts', () => {
  const fixtures = {
    continuations: [{continuation_id: 'edge-01', from_generation: 0, to_generation: 2, reason: 'queued resume'}],
    attempts: [{attempt_id: 'attempt-01', generation: 2, ordinal: 1, state: 'running', operations: {can_input: true, can_stop: true}, runtime: {}}],
    decisions: [{decision_id: 'decision-01', generation: 2, state: 'pending', revision: 1, title: 'Proceed?'}],
    results: [{output: '<script>untrusted history</script>', status: 'success', generation: 2, attempt_id: 'attempt-01'}],
    inputs: [{sequence: 1, text: 'supplement', state: 'accepted', attempt_id: 'attempt-01'}],
    events: [{kind: 'runtime_progress', detail: {thread_id: 'native-thread'}, attempt_id: 'attempt-01'}],
    notifications: [{state: 'delivered', kind: 'result_ready'}],
  };
  for (const [section, rows] of Object.entries(fixtures)) {
    const context = {...directoryContext, section, recordGeneration: 2};
    const model = historyModel(context, rows); model.composerOpen = true;
    assert.match(commandGate(model), /跨代任务链仅供查看/);
    const dom = renderSynthetic(model);
    // The independent ID lookup is a GET navigation form; no operation form is rendered.
    assert.equal(nodes(dom).some(node => node.tag === 'form' && String(node.className).includes('command-')), false);
    assert.equal(nodes(dom).some(node => node.tag === 'textarea' || node.tag === 'script'), false);
    assert.equal(nodes(dom).some(node => node.tag === 'button' && /请求停止|提交明确决定|保存补充信息|保存新任务/.test(node.textContent)), false);
    assert.match(dom.textContent, /跨代任务链只读/);
    if (section === 'notifications') assert.match(dom.textContent, /账本.*不证明目的端接收/);
    if (section === 'results') assert.match(dom.textContent, /<script>untrusted history<\/script>/);
  }
});

test('historical results display their own readonly delivery and do not borrow current result receipt evidence', () => {
  const context = {...directoryContext, section: 'results', recordGeneration: 0};
  const model = historyModel(context, [{output: 'Historical final text', status: 'success', generation: 0,
    attempt_id: 'old-attempt', delivery: deliveryFixture('held', {generation: 0, attempt_id: 'old-attempt',
      reason: 'synthetic-receiver-unavailable'})}]);
  model.snapshots.detail = resultTaskModel({items: [deliveryFixture('received', {
    receipt: {receipt_id: 'unrelated-current-receipt', text_bytes: 12, storage_revision: 9}})], truncated: false}).snapshots.detail;
  const dom = renderSynthetic(model), values = definitionValues(dom);
  assert.match(dom.textContent, /Historical final text/);
  assert.match(dom.textContent, /待核对.*synthetic-receiver-unavailable/);
  assert.equal(values.get('记录代次'), '0'); assert.match(values.get('执行'), /old-atte/);
  assert.equal(values.get('目的端接收'), '尚未确认');
  assert.doesNotMatch(dom.textContent, /unrelated-current-receipt|目的端已持久保存/);
  const card = nodes(dom).find(node => node.attributes['aria-label'] === '结果投递');
  assert.equal(nodes(card).some(node => ['button', 'form', 'textarea'].includes(node.tag)), false);
  delete model.snapshots.history.data.items[0].delivery;
  assert.match(renderSynthetic(model).textContent, /此服务未提供结果投递记录/);
  model.snapshots.history.data.items[0].delivery = null;
  assert.match(renderSynthetic(model).textContent, /当前所选记录范围没有结果投递记录/);
});

test('history pagination reports bounded pages rather than silently claiming the full chain is displayed', () => {
  const model = historyModel(); model.snapshots.history.data.next_cursor = 'opaque-next';
  model.historyPage = 1; model.historyCursors = [null, 'opaque-first'];
  let direction = null;
  const actions = new Proxy({historyPage: value => { direction = value; }}, {get: (target, key) => target[key] || (() => {})});
  const dom = renderSynthetic(model, actions);
  assert.match(dom.textContent, /第 2 页 · 还有记录/);
  assert.match(dom.textContent, /本页没有记录/);
  nodes(dom).find(node => node.tag === 'button' && node.textContent === '下一页').events.click(); assert.equal(direction, 'next');
  nodes(dom).find(node => node.tag === 'button' && node.textContent === '上一页').events.click(); assert.equal(direction, 'previous');
  model.snapshots.history.status = 'stale';
  const stale = renderSynthetic(model, actions);
  for (const node of nodes(stale).filter(node => node.tag === 'button' && ['上一页', '下一页'].includes(node.textContent))) assert.equal(node.disabled, true);
});

test('history reads carry current UI scope separately from record generation and opaque cursor', async () => {
  const context = {...directoryContext, section: 'events', recordGeneration: 0, cursor: 'opaque+/=&'};
  const path = new URL(historyPath(A, context), 'http://127.0.0.1');
  assert.equal(path.searchParams.get('generation'), '2');
  assert.equal(path.searchParams.get('record_generation'), '0');
  assert.equal(path.searchParams.get('cursor'), 'opaque+/=&');
  assert.equal(path.pathname, '/api/v1/installations/A/task-history/continued-task');
  assert.equal(new URL(historyPath(A, directoryContext), path).searchParams.has('record_generation'), false);
  assert.throws(() => historyPath(A, {...context, recordGeneration: null}), /有效任务/);
  assert.throws(() => historyPath(A, {...context, section: 'unrecognized'}), /有效任务/);
  const {client, requests} = deferredFetcher(); client.setScope(A);
  const pending = loadHistorySnapshot(client, A, context, () => {});
  assert.equal(requests[0].options.method, 'GET');
  requests[0].resolve(response(envelope(A, historyData(context)))); assert.equal(await pending, true);
});

test('history rejects wrong task, section, record generation and malformed contracts after envelope validation', async () => {
  const context = {...directoryContext, section: 'events', recordGeneration: 0};
  const good = historyData(context);
  for (const data of [{...good, scope: {kind: 'task_history', task_id: 'other'}}, {...good, task: {...good.task, task_id: 'other'}},
    {...good, section: 'decisions'}, {...good, record_generation: 1}, {...good, readonly: false}, {...good, items: {}},
    {...good, next_cursor: undefined}]) assert.throws(() => validateHistory(envelope(A, data), context), /不一致/);
  const {client, requests} = deferredFetcher(); client.setScope(A); const events = [];
  const pending = loadHistorySnapshot(client, A, context, event => events.push(event));
  requests[0].resolve(response(envelope(A, {...good, section: 'decisions'})));
  assert.equal(await pending, false); assert.equal(events.at(-1).type, 'error');
});

test('late history responses cannot cross task, section, record generation, page or A to B to A navigation', async () => {
  const firstContext = {...directoryContext, section: 'events', recordGeneration: 0};
  for (const nextContext of [{...firstContext, taskId: 'another-task'}, {...firstContext, section: 'inputs'},
    {...firstContext, recordGeneration: 1}, {...firstContext, cursor: 'next-page'}, firstContext]) {
    const {client, requests} = deferredFetcher(); client.setScope(A); const events = [];
    const first = loadHistorySnapshot(client, A, firstContext, event => events.push(event));
    if (nextContext === firstContext) { client.setScope(B); client.setScope(A); }
    const second = loadHistorySnapshot(client, A, nextContext, event => events.push(event));
    requests[1].resolve(response(envelope(A, historyData(nextContext, [{event_id: 'new-response'}]))));
    assert.equal(await second, true);
    requests[0].resolve(response(envelope(A, historyData(firstContext, [{event_id: 'late-response'}]))));
    assert.equal(await first, false); assert.equal(requests[0].options.signal.aborted, true);
    assert.deepEqual(events.filter(event => event.type === 'success').map(event => event.envelope.data.items[0].event_id), ['new-response']);
  }
});

test('changing business resets history context, and returning from readonly history preserves current write guards', () => {
  const model = historyModel({...directoryContext, section: 'events', recordGeneration: 0});
  model.historyCursors = [null, 'old-page']; model.historyPage = 1;
  assert.deepEqual(historyContext(model), {taskId: 'continued-task', section: 'events', recordGeneration: 0, cursor: 'old-page'});
  for (const scope of [B, {...A, generation: 1}, {...A, stateRoot: 'other'}]) {
    const changed = selectScope(model, scope);
    assert.equal(changed.historySection, 'generations'); assert.equal(changed.historyGeneration, null);
    assert.equal(changed.historyPage, 0); assert.deepEqual(changed.historyCursors, [null]);
    assert.equal(changed.snapshots.history.data, null);
  }
  assert.match(commandGate(model), /跨代任务链/);
  model.detailTab = 'decisions'; assert.equal(commandGate(model), null);
  model.selection = {...A, generation: 0}; assert.match(commandGate(model), /历史代次/);
});
