/**
 * Local, in-memory reproduction of the current CommandStore request lifetime.
 *
 * Run from any directory:
 *   node product_uncertain_request_reproduction.mjs
 *
 * The real frontend CommandStore is imported. Its injected fetcher never uses
 * HTTP: a Map simulates persistence keyed by request_id, followed by a lost
 * response. Recreating CommandStore approximates losing JavaScript heap state;
 * this is NOT a browser-crash test or an end-to-end test of the real backend.
 * No model, service, browser profile, authentication, or runtime state is used.
 * The script reads only product source files and writes JSON to stdout.
 */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {CommandStore} from '../../../masteragent/workbench_assets/commands.mjs';

const sourcePaths = [
  'masteragent/workbench_assets/commands.mjs',
  'masteragent/workbench_assets/state.mjs',
  'masteragent/workbench_assets/api.mjs',
  'masteragent/workbench_assets/app.mjs',
  'masteragent/service.py',
];
const sourceHashes = [];
for (const path of sourcePaths) {
  const bytes = await readFile(new URL(`../../../${path}`, import.meta.url));
  sourceHashes.push({
    path,
    bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
  });
}

// Deliberately non-filesystem identifiers. No directory is opened or created.
const selection = Object.freeze({
  stateRoot: 'synthetic-root',
  installationId: 'synthetic-business',
  generation: 1,
});
const taskText = 'Synthetic identical task body / 合成任务';
const savedRequests = new Map();
const fetchCalls = [];

async function simulatedSaveThenLoseResponse(url, options) {
  const payload = JSON.parse(options.body);
  assert.equal(options.method, 'POST');
  assert.equal(url, '/api/v1/installations/synthetic-business/commands/submit');
  const existing = savedRequests.get(payload.request_id);
  if (existing) assert.deepEqual(payload, existing);
  else savedRequests.set(payload.request_id, structuredClone(payload));
  fetchCalls.push({
    request_id: payload.request_id,
    identical_body: payload.text === taskText,
    simulated_persisted_request_count: savedRequests.size,
  });
  throw new Error('Synthetic response lost after simulated persistence.');
}

function prepareStore(requestId) {
  const store = new CommandStore(simulatedSaveThenLoseResponse, () => requestId);
  store.setScope(selection);
  store.edit(selection, 'submit', '', 'role_id', 'synthetic-worker');
  store.edit(selection, 'submit', '', 'text', taskText);
  return store;
}

function snapshot(store) {
  const record = store.get(selection, 'submit');
  return {
    status: record.status,
    request_id: record.payload?.request_id ?? null,
    simulated_persisted_request_count: savedRequests.size,
  };
}

const original = prepareStore('synthetic-before-recreation');
await original.send(selection, 'submit', '', {}, 'synthetic-csrf');
const firstSubmit = snapshot(original);
assert.equal(firstSubmit.status, 'uncertain');
assert.equal(firstSubmit.simulated_persisted_request_count, 1);

await original.send(selection, 'submit', '', {}, 'synthetic-csrf');
const sameStoreRetry = snapshot(original);
assert.equal(sameStoreRetry.status, 'uncertain');
assert.equal(sameStoreRetry.request_id, firstSubmit.request_id);
assert.equal(sameStoreRetry.simulated_persisted_request_count, 1);

const recreated = prepareStore('synthetic-after-recreation');
await recreated.send(selection, 'submit', '', {}, 'synthetic-csrf');
const recreatedStoreSubmit = snapshot(recreated);
assert.equal(recreatedStoreSubmit.status, 'uncertain');
assert.notEqual(recreatedStoreSubmit.request_id, firstSubmit.request_id);
assert.equal(recreatedStoreSubmit.simulated_persisted_request_count, 2);
assert.equal(fetchCalls.length, 3);
assert.ok(fetchCalls.every(call => call.identical_body));

console.log(JSON.stringify({
  evidence_id: 'product-uncertain-request-recreation',
  generated_at_utc: new Date().toISOString(),
  evidence_kind: 'actual_frontend_module_with_in_memory_backend_double',
  result: 'current_behavior_reproduced',
  description: '同一 CommandStore 的未确认重试复用原 ID；新建 CommandStore 后重填相同任务使用新 ID。',
  environment: {node: process.version, platform: process.platform},
  source_hashes: sourceHashes,
  scenarios: {
    first_submit_response_lost: firstSubmit,
    same_store_retry: sameStoreRetry,
    recreated_store_identical_submit: recreatedStoreSubmit,
  },
  calls: fetchCalls,
  observed: {
    same_store_retry_reuses_request_id: true,
    recreated_store_uses_different_request_id: true,
    simulated_distinct_saved_requests: savedRequests.size,
  },
  interpretation: {
    observed_fact: '未确认请求 ID 的保留依赖原 CommandStore 实例仍存在。',
    static_backend_basis: 'masteragent/service.py 的 Platform.submit 按 installation_id + client_request_id 去重；该函数未在本脚本中执行。',
    inferred_risk: '若真实提交已保存但响应丢失，页面内存丢失后用户重填相同任务可得到新请求 ID，因此可能形成第二个 Task。',
  },
  limits: [
    '模拟持久层为内存 Map，未连接真实 SQLite 或调用 Platform.submit。',
    '新建 CommandStore 仅模拟 JavaScript 内存丢失，未启动浏览器或执行真实崩溃。',
    '不是 HTTP、浏览器、后端或真实模型端到端验收。',
    '不证明生产环境已发生重复执行；本脚本没有执行任何任务。',
    '没有网络请求、模型调用、服务启动、浏览器用户数据读取或运行状态写入。',
  ],
}, null, 2));
