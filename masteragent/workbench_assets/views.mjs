import {displayCount, displayValue, formatTime, memberSnapshotPresentation, shortId, stateLabel} from './state.mjs';
import {el, icon, button, badge, heading, panel, empty, info, loading, snapshotNotice, unavailable, definitions, truncation} from './ui.mjs';
import {COMMAND_LABELS, commandGate, commandLocked, emptyCommand} from './commands.mjs';
import {compatibilityLink, featureDirectory, localHelp, secondaryNavigation} from './navigation.mjs';
import {HISTORY_SECTIONS, historySupported} from './history.mjs';

const TITLES = {
  overview: ['工作台', '了解当前进展，把需要关注的工作放在眼前。', 'YOUR WORKSPACE'],
  tasks: ['任务', '从任务到成果，每一步都有记录。', 'TASKS & PROGRESS'],
  team: ['团队', '角色负责什么，谁在接单，执行归属是否清楚。', 'PEOPLE & ROLES'],
  resources: ['资料', '当前业务的知识、流程与成果来源。', 'KNOWLEDGE & REFERENCES'],
  connections: ['运行设置', '查看运行时、部署与数据边界。', 'RUNTIME & DEPLOYMENT'],
};
const EVENT_NAMES = {
  claimed: '执行已领取', attempt_claimed: '执行已领取', renewed: '执行租约续期', lease_renewed: '执行租约续期',
  completed: '结果已保存', failed: '执行未成功', expired: '执行租约过期', reconciled: '已记录核对结果',
  runtime_start_requested: '已请求启动运行时', runtime_started: '运行时已启动', runtime_thread: '已记录原生会话',
  runtime_progress: '执行进展', runtime_stopped: '运行时已结束', runtime_uncertain: '执行结果需要核对',
  runtime_observation: '运行事实已更新', runtime_native_request_denied: '原生工具请求已自动拒绝',
  runtime_cancel_requested: '已记录停止请求', completion_rejected: '结果发布被拒绝',
  runtime_input_pending: '补充信息已保存', runtime_input_sending: '补充信息发送中',
  runtime_input_accepted: '补充信息已接收', runtime_input_held: '补充信息无法确认',
  member_started: '成员已登记', member_stopped: '成员已停止', decision_answered: '平台决定已记录',
};
const list = value => Array.isArray(value) ? value : [];
const entries = value => value && typeof value === 'object' && !Array.isArray(value) ? Object.entries(value) : [];
const scalar = value => typeof value === 'string' || typeof value === 'number' ? String(value) : '未记录';
const USAGE_FIELDS = [['input_tokens', '输入 token'], ['cached_input_tokens', '缓存命中 token'],
  ['cache_write_input_tokens', '缓存写入 token'], ['output_tokens', '输出 token'],
  ['reasoning_output_tokens', '推理输出 token'], ['total_tokens', '总 token']];
const tokenCount = value => Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString('zh-CN') : '未采集';
function reportedModelSource(value) {
  return value === 'thread/start' ? '会话启动报告（thread/start）'
    : value === 'model/rerouted' ? '模型改道报告（model/rerouted）' : '未采集';
}
function usageSource(value) { return value === 'thread/tokenUsage/updated' ? '原生用量通知（thread/tokenUsage/updated）' : '未采集'; }
function usageScope(value) { return value === 'fresh_thread_total' ? '本次新建原生会话累计，非单轮增量' : '未采集'; }
function usageDefinitions(runtime) {
  const usage = runtime.usage;
  return [...(usage && typeof usage === 'object' && !Array.isArray(usage)
    ? USAGE_FIELDS.map(([key, label]) => [label, tokenCount(usage[key])]) : [['用量记录', '未采集']]),
    ['用量来源', usageSource(runtime.usage_source)], ['统计范围', usageScope(runtime.usage_scope)]];
}
function modelDefinitions(runtime) {
  return [['请求模型', displayValue(runtime.model_requested)], ['服务端报告模型', displayValue(runtime.model_reported)],
    ['模型报告来源', reportedModelSource(runtime.model_reported_source)], ['实际模型', displayValue(runtime.model_observed)]];
}
const MODEL_EVIDENCE_NOTE = '服务端报告模型表示配置或改道目标，不证明实际推理所用模型；实际模型无独立证据时仍显示未采集。';
const NATIVE_DENIAL_NOTE = '当前纯文本模式自动拒绝原生工具请求，未开放人工批准；平台公共决定不能批准原生工具。';

const OPERATION_REASONS = {inactive_installation: '业务未启用', historical_generation: '历史代次仅供查看',
  attempt_not_running: '执行不在运行状态', task_not_running: '任务不在运行状态', lease_expired: '执行租约已失效',
  owner_unavailable: '执行归属不可用', member_unavailable: '接单成员不可用', runtime_not_started: '尚无运行时启动记录',
  runtime_input_unsupported: '当前运行时不支持执行中补充', stop_requested: '已记录停止请求，尚待退出证据', runtime_finished: '运行时已结束'};
function commandForm(model, actions, action, keyTarget = '', target = {}, {available = true, reason = '', roles = []} = {}) {
  const record = actions.commandState(action, keyTarget) || emptyCommand();
  const locked = commandLocked(record), blocked = commandGate(model);
  const prefix = `command-${action}-${keyTarget || 'new'}`;
  const field = (name, label, {multiline = false, options = null} = {}) => {
    const id = action === 'submit' && name === 'role_id' ? 'submit-role' : `${prefix}-${name}`;
    const attrs = {id, name, disabled: locked || Boolean(blocked) || !available,
      oninput: event => actions.editCommand(action, keyTarget, name, event.target.value)};
    let input;
    if (options) input = el('select', {...attrs, onchange: attrs.oninput}, options.map(([value, title]) =>
      el('option', {value, selected: (record.fields[name] || '') === value}, title)));
    else if (multiline) { input = el('textarea', {...attrs, rows: 4, maxlength: 100000}); input.value = record.fields[name] || ''; }
    else input = el('input', {...attrs, type: 'text', maxlength: 2000, value: record.fields[name] || ''});
    return el('div', {class: 'command-field'}, el('label', {for: id}, label), input);
  };
  const fields = action === 'submit' ? [field('role_id', '接单角色', {options: [['', '请选择角色'], ...roles.map(role => [role.role_id, role.display_name || role.role_id])]}),
    field('text', '任务内容', {multiline: true})] : action === 'input' ? [field('text', '补充内容', {multiline: true})]
      : action === 'answer' ? [field('choice', '明确选择', {options: [['', '请选择批准或拒绝'], ['approve', '批准'], ['reject', '拒绝']]})]
        : [field('reason', '停止原因')];
  const retry = record.status === 'uncertain';
  const disabled = Boolean(blocked) || (!available && !retry) || ['pending', 'success', 'rejected'].includes(record.status);
  return el('form', {class: `command-form command-${action}`, onsubmit: event => {
    event.preventDefault(); if (!disabled) actions.sendCommand(action, keyTarget, target);
  }}, fields,
    blocked || !available ? el('p', {class: 'command-note'}, blocked || OPERATION_REASONS[reason] || reason || '当前记录不允许此操作。') : null,
    record.message ? el('p', {class: `command-feedback ${record.status}`, role: 'status'}, record.message) : null,
    el('div', {class: 'command-actions'}, el('button', {type: 'submit', class: 'button', disabled},
      record.status === 'pending' ? '正在保存…' : retry ? '以原内容重试' : COMMAND_LABELS[action]),
      ['success', 'rejected'].includes(record.status) ? button(action === 'submit' ? '填写另一项任务' : '重新填写',
        () => actions.resetCommand(action, keyTarget), {secondary: true}) : null,
      action === 'submit' && record.status === 'success' && typeof record.result?.task_id === 'string'
        ? button('查看已保存任务', () => actions.openTask(record.result.task_id), {secondary: true}) : null,
      retry ? button('先刷新核对', actions.refresh, {secondary: true}) : null));
}
function handoffStatus(model, roleId, task = null, attempts = []) {
  const status = memberSnapshotPresentation(model.snapshots.team, model.selection, roleId);
  const latest = attempts.reduce((current, row) => !current || row.ordinal > current.ordinal ? row : current, null);
  const associated = latest && status.member?.active_attempt_id === latest.attempt_id ? status.member : null;
  return el('section', {class: 'handoff-status'}, el('div', {class: 'record-heading'}, '任务交接', badge(null, status.label, status.tone)),
    el('p', {class: 'command-note'}, status.detail),
    definitions([['接单角色', displayValue(roleId, '请先选择角色')], ['成员快照', formatTime(status.observedAt)],
      ['成员租约', status.member ? (status.member.lease_valid ? `有效至 ${formatTime(status.member.expires_at)}` : '已失效') : '未确认'],
      ['领取状态', task?.state === 'queued' ? '本详情快照中仍在排队，尚未领取'
        : task ? `任务状态：${stateLabel(task.state)}；领取归属见执行记录` : '保存后打开任务，核对是否已被领取'],
      ...(latest ? [['领取执行', `第 ${latest.ordinal} 次 · ${shortId(latest.attempt_id)} · ${formatTime(latest.created_at)}`],
        ['成员归属', associated ? `成员 ${shortId(associated.member_id)}（本快照中的当前执行）` : '未在此快照中提供精确成员关联']] : []),
      ['模型连通', '尚未验证']]),
    el('p', {class: 'record-footer'}, '保存成功只证明平台已收下任务；成员租约与模型连通分别核对。'));
}
function taskComposer(model, actions) {
  const record = actions.commandState('submit', '') || emptyCommand();
  return panel('新任务', '保存到当前 V3 队列；不会自动启动执行服务，也不等同于独立待办。',
    model.composerOpen ? el('div', {class: 'panel-body'}, commandForm(model, actions, 'submit', '', {}, {
      roles: list(model.snapshots.team.data?.roles), available: model.snapshots.team.status === 'ready', reason: '角色快照尚未确认，请刷新。'}),
    record.fields.role_id ? handoffStatus(model, record.fields.role_id) : null) : null,
    {action: button(model.composerOpen ? '收起表单' : '填写新任务', actions.toggleComposer, {secondary: true})});
}

function taskSummary(task, actions) {
  return el('button', {type: 'button', class: 'task-summary', onclick: () => actions.openTask(task.task_id),
    'aria-label': `查看任务 ${shortId(task.task_id)}，${stateLabel(task.state)}`},
  el('div', {}, el('div', {class: 'task-name'}, '任务 ', shortId(task.task_id)),
    el('div', {class: 'task-sub'}, el('span', {}, task.role_id), el('span', {}, formatTime(task.created_at)))),
  el('div', {class: 'task-summary-end'}, badge(task.state), icon('arrow', 15)));
}

function overview(model, actions) {
  const data = model.snapshots.overview.data, counts = data.counts || {};
  const tasks = list(data.recent_tasks), decisions = list(data.pending_decisions);
  const focus = counts.needs_reconcile > 0 ? `${displayCount(counts.needs_reconcile)} 项工作需要核对`
    : counts.pending_decisions > 0 ? `${displayCount(counts.pending_decisions)} 项平台决定等待确认` : '当前业务，一览清楚。';
  const hero = el('section', {class: 'hero'}, el('div', {},
    el('span', {class: 'eyebrow'}, '当前业务快照'), el('h2', {}, focus),
    el('p', {}, `${data.installation?.package_id || model.selection.installationId} · 第 ${model.selection.generation} 代。查看进展与已保存成果，公共操作需由本机服务显式启用。`)),
    el('div', {class: 'hero-emblem', 'aria-hidden': 'true'}, icon('spark', 44)));
  const metrics = el('div', {class: 'metric-grid'}, [
    ['执行中', counts.running, '已领取的平台任务', 'clock', 'accent'],
    ['待确认平台决定', counts.pending_decisions, '平台任务决定，不含原生工具审批', 'info', 'warning'],
    ['需要核对', counts.needs_reconcile, '未确认的执行不会自动重做', 'attention', 'warning'],
    ['已完成', counts.completed, '当前查询代次中的任务', 'check', ''],
  ].map(([label, value, caption, symbol, tone]) => el('section', {class: `panel metric-card ${tone}`},
    el('div', {class: 'metric-label'}, label, icon(symbol, 17)), el('div', {class: 'metric-value'}, displayCount(value)),
    el('p', {class: 'metric-caption'}, caption))));
  const pending = decisions.length ? decisions.map(decision => el('div', {class: 'decision-row'},
    el('h3', {}, decision.title || '待确认平台决定'), el('div', {class: 'task-sub'},
      badge(decision.state || 'pending', decision.state === 'expired' ? '已过期' : '待确认', 'warning'),
      el('span', {}, `任务 ${shortId(decision.task_id)}`)),
    button('查看相关任务', () => actions.openTask(decision.task_id), {quiet: true})))
    : empty('没有待确认平台决定', '新的平台决定会在下次快照中显示；原生工具审批当前未开放。', {compact: true, symbol: 'check'});
  return [hero, metrics, el('div', {class: 'dashboard-columns'},
    panel('最近任务', '摘要不包含任务正文，选择后查看详情。',
      tasks.length ? tasks.map(task => taskSummary(task, actions)) : empty('还没有任务', '当前业务的任务将在这里出现。', {symbol: 'tasks'}),
      {action: button('查看全部', () => actions.navigate('tasks'), {quiet: true})}),
    el('div', {class: 'stack'}, panel('需要你关注', '平台决定 · 进入任务查看并明确答复', pending),
      info('数据来自所选业务的协调记录。成员租约、任务执行和原生模型连通是不同状态。'))),
  ];
}

function selectFilter(id, label, value, options, onchange) {
  const select = el('select', {id, onchange: event => onchange(event.target.value)},
    options.map(([optionValue, text]) => el('option', {value: optionValue, selected: value === optionValue}, text)));
  return el('div', {class: 'filter-field'}, el('label', {for: id}, label), select);
}

function taskBrowser(model, actions) {
  const snapshot = model.snapshots.tasks, rows = list(snapshot.data?.items);
  const roles = [...new Set([...list(model.snapshots.team.data?.roles).map(role => role.role_id),
    ...rows.map(task => task.role_id), model.filters.role_id].filter(Boolean))].sort();
  const stateOptions = ['queued', 'running', 'waiting', 'awaiting_decision', 'needs_reconcile', 'completed', 'failed', 'cancelled'];
  const filters = el('div', {class: 'task-filters'},
    selectFilter('task-state-filter', '状态', model.filters.state, [['', '全部状态'], ...stateOptions.map(state => [state, stateLabel(state)])],
      value => actions.filterTasks('state', value)),
    selectFilter('task-role-filter', '角色', model.filters.role_id, [['', '全部角色'], ...roles.map(role => [role, role])],
      value => actions.filterTasks('role_id', value)));
  const contents = rows.length ? rows.map(task => el('button', {type: 'button', id: `task-row-${task.task_id}`,
    class: `task-row${model.selectedTaskId === task.task_id ? ' selected' : ''}`,
    'aria-pressed': model.selectedTaskId === task.task_id ? 'true' : 'false', onclick: () => actions.openTask(task.task_id)},
    el('div', {class: 'task-row-top'}, el('span', {class: 'task-row-id'}, '任务 ', shortId(task.task_id)), badge(task.state)),
    el('div', {class: 'task-sub'}, el('span', {}, task.role_id), el('span', {}, formatTime(task.created_at)))))
    : snapshot.data ? empty('没有匹配的任务', '可以调整状态或角色筛选。', {compact: true, symbol: 'tasks'})
      : snapshot.status === 'error' ? empty('任务暂时不可读', snapshot.error, {compact: true, symbol: 'offline'}) : loading();
  return el('section', {class: 'panel task-browser', 'aria-label': '任务摘要列表'},
    el('div', {class: 'panel-header'}, el('h2', {}, '任务列表'), el('span', {class: 'subtle-label'}, '按需读取正文')),
    historySupported(model) ? el('form', {class: 'task-lookup', onsubmit: event => {
      event.preventDefault(); actions.openTaskHistory(model.taskLookup);
    }}, el('label', {for: 'history-task-id'}, '按完整任务 ID 查看跨代任务链'),
      el('div', {}, el('input', {id: 'history-task-id', value: model.taskLookup, maxlength: 64,
        pattern: '[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', required: true, placeholder: '粘贴已保存任务的 ID',
        oninput: event => actions.editTaskLookup(event.target.value)}), el('button', {type: 'submit', class: 'button button-secondary'}, '查看'))) : null,
    filters, snapshotNotice(snapshot, actions.refreshTasks),
    el('div', {class: 'task-list', 'data-scroll-key': 'task-list'}, contents),
    el('div', {class: 'list-footer'}, el('span', {}, snapshot.data ? `已显示 ${rows.length} 项` : '尚未读取'),
      snapshot.data?.next_cursor ? button(snapshot.status === 'refreshing' ? '读取中…' : '加载更多', actions.loadMore,
        {secondary: true, id: 'load-more-tasks', disabled: snapshot.status === 'refreshing'}) : el('span', {}, snapshot.data ? '已到末尾' : '')));
}

function eventInfo(event) {
  const detail = event.detail || {}, parts = [];
  if (detail.outcome) parts.push(`结果：${scalar(detail.outcome)}`);
  if (detail.reason) parts.push(`说明：${scalar(detail.reason)}`);
  if (detail.model_requested) parts.push(`请求模型：${scalar(detail.model_requested)}`);
  if ('model_reported' in detail) parts.push(`服务端报告模型：${displayValue(detail.model_reported)}`);
  if (detail.model_reported_source) parts.push(`模型报告来源：${reportedModelSource(detail.model_reported_source)}`);
  if ('model_observed' in detail) parts.push(`实际模型：${displayValue(detail.model_observed, '未采集')}`);
  if ('usage' in detail) parts.push(...usageDefinitions(detail).map(([label, value]) => `${label}：${value}`));
  if (event.kind === 'runtime_native_request_denied') {
    if (detail.method) parts.push(`请求类型：${scalar(detail.method)}`);
    parts.push('纯文本模式自动拒绝，未开放人工批准');
  }
  if (detail.thread_id) parts.push(`原生会话 ${shortId(detail.thread_id)}`);
  if (event.attempt_id) parts.push(`执行 ${shortId(event.attempt_id)}`);
  return parts.join(' · ');
}
function timeline(data) {
  const rows = [{key: 'created', kind: 'task_created', title: '任务已保存', created_at: data.task.created_at,
    description: `创建于第 ${data.task.generation} 代，当前执行归属第 ${data.task.dispatch_generation} 代。`},
  ...list(data.events).map((event, index) => ({...event, key: event.event_id || `event-${index}`,
    title: EVENT_NAMES[event.kind] || event.kind || '平台事件', description: eventInfo(event)}))];
  rows.sort((left, right) => (Date.parse(left.created_at) || 0) - (Date.parse(right.created_at) || 0));
  return el('div', {}, el('ol', {class: 'timeline'}, rows.map(row => el('li', {class: 'timeline-item'},
    el('span', {class: 'timeline-dot', 'aria-hidden': 'true'}), el('div', {},
      el('div', {class: 'timeline-title'}, el('span', {}, row.title), el('time', {class: 'timeline-time'}, formatTime(row.created_at))),
      row.description ? el('p', {class: 'timeline-info'}, row.description) : null)))),
    truncation(data.truncated?.events),
    list(data.attempts).length ? el('div', {}, el('h3', {class: 'eyebrow'}, '执行尝试'),
      list(data.attempts).map(attempt => el('div', {class: 'record-box'},
        el('div', {class: 'record-heading'}, `第 ${attempt.ordinal} 次执行`, badge(attempt.state)),
        el('div', {class: 'task-sub'}, `执行 ${shortId(attempt.attempt_id)}`, `第 ${attempt.generation} 代`, formatTime(attempt.created_at))))) : null,
    truncation(data.truncated?.attempts));
}
function resultView(data) {
  const result = data.result;
  const failed = result && (result.status === 'error' || result.error !== null && result.error !== undefined);
  const content = result ? el('div', {}, el('div', {class: 'record-heading'}, badge(failed ? 'failed' : 'completed', failed ? '已记录错误' : '已保存成果'),
    el('time', {class: 'subtle-label'}, formatTime(result.created_at))),
    result.output !== null && result.output !== undefined ? el('pre', {class: 'result-text', 'data-scroll-key': 'result-text'}, result.output) : null,
    failed ? el('div', {class: 'record-box'}, el('pre', {}, typeof result.error === 'string' ? result.error :
      [result.error?.code, result.error?.message].filter(Boolean).join('\n') || '执行未成功，未保存成功结果。')) : null,
    el('p', {class: 'record-footer'}, `来源：平台结果记录 · 执行 ${shortId(result.attempt_id)} · 第 ${result.generation} 代`))
    : empty('尚未保存成果', '执行完成并确认后，已保存的文本结果会在这里显示。', {symbol: 'file'});
  return el('div', {class: 'stack'}, content, deliveriesView(data.deliveries));
}
const DELIVERY_STATES = {
  pending: ['待发送', '平台已保存结果与投递意图，尚未授权发送。', 'neutral'],
  claimed: ['已领取发送', '发送者已取得领取记录，不表示字节已经发出。', 'neutral'],
  sending: ['发送中，待回执', '目的端可能已经保存，当前尚未确认持久回执。', 'warning'],
  received: ['目的端已持久保存', '合法的目的端持久回执已由平台校验并保存。', 'success'],
  held: ['待核对', '投递效果或归属尚不明确，已停止自动发送。请核对原投递记录，不要重跑模型。', 'warning'],
  rejected: ['已拒绝投递', '本次投递已明确拒绝，平台结果仍保留；更改目标或内容需要新的明确决定。', 'danger'],
};
function deliveriesView(deliveries) {
  const rows = list(deliveries?.items);
  return el('section', {class: 'record-box', 'aria-label': '结果投递'}, el('h3', {}, '结果投递'),
    el('p', {class: 'command-note'}, '平台保存、目的端持久接收、agent 处理和用户已读是四项独立事实。此处只读，查看不会领取、发送或确认投递。'),
    !deliveries || !Array.isArray(deliveries.items)
      ? info('此服务未提供结果投递记录，目的端接收状态未知；不能从平台已保存成果推断送达。')
      : !rows.length ? info('当前所选记录范围没有结果投递记录；目的端是否接收仍未知。')
        : rows.map(row => {
          const [label, explanation, tone] = Object.hasOwn(DELIVERY_STATES, row.state) ? DELIVERY_STATES[row.state]
            : ['状态未知', '此版本无法解释该投递状态，请保留原记录核对。', 'neutral'];
          const received = row.state === 'received';
          const receipt = row.receipt && typeof row.receipt === 'object' ? row.receipt : null;
          return el('article', {class: 'record-box'}, el('div', {class: 'record-heading'},
            el('strong', {}, `投递 ${shortId(row.delivery_id)}`), badge(null, label, tone)),
          el('p', {class: 'timeline-info'}, explanation),
          definitions([['平台成果', '已保存'], ['目的端', `${scalar(row.destination_namespace)} / ${scalar(row.destination_id)}`],
            ['目的端接收', received ? '已持久保存' : '尚未确认'], ['agent 处理', '未知'], ['用户已读', '未知'],
            ['发送调用次数', Number.isSafeInteger(row.send_count) && row.send_count >= 0 ? String(row.send_count) : '未记录'],
            ['执行', shortId(row.attempt_id)], ['记录代次', Number.isSafeInteger(row.generation) ? String(row.generation) : '未记录']]),
          row.reason ? el('p', {class: 'timeline-info'}, `说明：${scalar(row.reason)}`) : null,
          received && receipt ? definitions([['持久回执', scalar(receipt.receipt_id)],
            ['接收正文大小', Number.isSafeInteger(receipt.text_bytes) && receipt.text_bytes >= 0 ? `${receipt.text_bytes} UTF-8 字节` : '未记录'],
            ['接收存储修订', scalar(receipt.storage_revision)], ['持久接收时间', formatTime(receipt.received_at || row.received_at)]]) : null,
          el('p', {class: 'record-footer'}, `创建 ${formatTime(row.created_at)} · 更新 ${formatTime(row.updated_at)}`));
        }),
    deliveries?.truncated === true ? info('此页只展示部分投递记录；未显示的记录不能视为不存在。请按执行代次查看跨代任务链。') : null);
}
function inputsView(data, model, actions) {
  const inputs = list(data.inputs).slice().sort((a, b) => a.sequence - b.sequence);
  const attempt = list(data.attempts).find(row => row.operations?.can_input) || list(data.attempts).at(-1);
  return el('div', {}, info('“已接收”表示运行时返回了接收回执，不代表模型已经采纳，也不构成审批。'),
    attempt ? commandForm(model, actions, 'input', `${data.task.task_id}-${attempt.attempt_id}`,
      {task_id: data.task.task_id, attempt_id: attempt.attempt_id}, {available: attempt.operations?.can_input === true && model.snapshots.detail.status === 'ready',
        reason: attempt.operations?.input_reason || '当前没有可验证的补充入口。'}) : null,
    inputs.length ? el('div', {class: 'input-records'}, inputs.map(input => el('article', {class: 'record-box'},
      el('div', {class: 'record-heading'}, `补充 ${input.sequence}`, badge(input.state)), el('pre', {}, input.text),
      el('p', {class: 'record-footer'}, `${formatTime(input.created_at)} · 执行 ${shortId(input.attempt_id)} · 第 ${input.generation} 代`))))
      : empty('没有补充信息', '有可用的运行尝试时，可在此保存补充文本；不会自动视为审批。', {symbol: 'tasks'}),
    truncation(data.truncated?.inputs));
}
function decisionsView(data, model, actions) {
  const decisions = list(data.decisions);
  if (!decisions.length) return empty('没有决定记录', '涉及此任务的明确决定会集中保存在这里。', {symbol: 'check'});
  return el('div', {}, info('这是平台公共决定，不是运行时的原生工具审批。必须明确选择批准或拒绝；未选择不会提交。'),
    decisions.map(decision => el('article', {class: 'record-box'},
      el('div', {class: 'record-heading'}, decision.title || '决定记录',
        badge(decision.state, decision.state === 'pending' ? '待确认' : null, decision.state === 'pending' ? 'warning' : null)),
      el('p', {class: 'timeline-info'}, `创建于 ${formatTime(decision.created_at)} · 修订 ${decision.revision}`),
      decision.answer ? el('p', {class: 'timeline-info'}, `已记录答复：${decision.answer === 'approve' ? '批准' : decision.answer === 'reject' ? '拒绝' : decision.answer}`) : null,
      decision.expires_at ? el('p', {class: 'timeline-info'}, `有效期至 ${formatTime(decision.expires_at)}`) : null,
      decision.state === 'pending' ? commandForm(model, actions, 'answer', `${data.task.task_id}-${decision.decision_id}`,
        {task_id: data.task.task_id, decision_id: decision.decision_id, expected_revision: decision.revision},
        {available: data.task.state === 'awaiting_decision' && Number.isSafeInteger(decision.revision) && model.snapshots.detail.status === 'ready',
          reason: '决定或任务状态已变化，请刷新核对。'}) : null)),
    truncation(data.truncated?.decisions));
}
function detailTabs(model, actions) {
  const tabs = [['timeline', '时间线'], ['result', '成果'], ['inputs', '补充信息'], ['decisions', '平台决定'],
    ...(historySupported(model) || model.detailTab === 'history' ? [['history', '跨代任务链']] : [])];
  return el('div', {class: 'detail-tabs', role: 'tablist', 'aria-label': '任务详情内容'}, tabs.map(([key, label], index) =>
    el('button', {type: 'button', class: 'detail-tab', id: `detail-tab-${key}`, role: 'tab',
      'aria-selected': model.detailTab === key ? 'true' : 'false', 'aria-controls': 'detail-tab-content',
      tabindex: model.detailTab === key ? 0 : -1, onclick: () => actions.changeDetailTab(key),
      onkeydown: event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        actions.changeDetailTab(tabs[next][0], true);
      }}, label)));
}
function detailSnapshotTime(snapshot, label = '详情快照') {
  return el('p', {class: 'snapshot-evidence'}, `${label}：${formatTime(snapshot.observedAt)} · 公开事件快照，需刷新读取后续记录。`);
}
function historyRows(data, actions) {
  const rows = data.items;
  if (!rows.length) return empty('本页没有记录', '只表示本分类与记录代次的本页为空；其他代次和分类请分别查看。', {compact: true});
  if (data.section === 'generations') return rows.map(row => el('article', {class: 'record-box'},
    el('div', {class: 'record-heading'}, `第 ${row.generation} 代`, button('查看此代记录', () => actions.openHistoryGeneration(row.generation), {secondary: true})),
    el('div', {class: 'feature-badges'}, row.is_original ? badge(null, '任务创建代次') : null,
      row.is_current_dispatch ? badge(null, '任务当前归属', 'accent') : null,
      row.is_installation_current ? badge(null, '安装当前代次') : null)));
  if (data.section === 'continuations') return rows.map(row => el('article', {class: 'record-box'},
    el('h3', {}, `第 ${row.from_generation} 代 → 第 ${row.to_generation} 代`),
    el('p', {class: 'timeline-info'}, displayValue(row.reason, '未记录接续说明')),
    el('p', {class: 'record-footer'}, `${formatTime(row.created_at)} · 接续 ${shortId(row.continuation_id)}`)));
  if (data.section === 'attempts') return rows.map(row => el('article', {class: 'record-box'},
    el('div', {class: 'record-heading'}, `第 ${row.ordinal} 次执行`, badge(row.state)),
    el('p', {class: 'timeline-info'}, `执行 ${shortId(row.attempt_id)} · 第 ${row.generation} 代 · ${formatTime(row.created_at)}`),
    runtimeFacts({attempts: [row]})));
  if (data.section === 'decisions') return rows.map(row => el('article', {class: 'record-box'},
    el('div', {class: 'record-heading'}, row.title || '平台决定', badge(row.state, row.state === 'pending' ? '待确认平台决定' : null)),
    el('p', {class: 'timeline-info'}, `修订 ${displayValue(row.revision, '未记录')} · ${formatTime(row.created_at)}`),
    row.answer ? el('p', {class: 'timeline-info'}, `已记录答复：${row.answer === 'approve' ? '批准' : row.answer === 'reject' ? '拒绝' : scalar(row.answer)}`) : null,
    row.expires_at ? el('p', {class: 'record-footer'}, `有效期至 ${formatTime(row.expires_at)}`) : null));
  if (data.section === 'results') return rows.map(row => el('article', {class: 'record-box'}, resultView({result: row,
    deliveries: Object.hasOwn(row, 'delivery') ? {items: row.delivery ? [row.delivery] : [], truncated: false} : undefined})));
  if (data.section === 'inputs') return rows.map(row => el('article', {class: 'record-box'},
    el('div', {class: 'record-heading'}, `补充 ${row.sequence}`, badge(row.state)), el('pre', {}, row.text),
    el('p', {class: 'record-footer'}, `执行 ${shortId(row.attempt_id)} · ${formatTime(row.created_at)}`)));
  if (data.section === 'events') return rows.map(row => el('article', {class: 'record-box'},
    el('h3', {}, EVENT_NAMES[row.kind] || row.kind || '公开事件'), el('p', {class: 'timeline-info'}, eventInfo(row)),
    el('p', {class: 'record-footer'}, formatTime(row.created_at))));
  return rows.map(row => el('article', {class: 'record-box'},
    el('h3', {}, row.kind || row.event_kind || '平台通知'), el('p', {class: 'timeline-info'},
      `${row.state === 'delivered' ? '账本标记已投递' : stateLabel(row.state)} · ${formatTime(row.created_at)}`)));
}
function taskHistory(model, actions) {
  const snapshot = model.snapshots.history;
  if (!historySupported(model)) return info('此服务尚未声明跨代任务链能力。当前代详情仍可查看；请使用支持此能力的本机版本读取历史。');
  const header = el('div', {class: 'history-header'}, info('跨代任务链只读。任务正文与状态显示当前事实；分页限定新增记录上界，不是过去时点的完整快照。'),
    el('div', {class: 'command-actions'}, model.historySection !== 'generations'
      ? button('返回代次目录', () => actions.changeHistorySection('generations'), {secondary: true}) : null,
    model.historyPage > 0 ? button('从本分类首页读取', () => actions.changeHistorySection(model.historySection), {secondary: true}) : null,
    button('刷新本页历史', actions.refreshHistory, {quiet: true})));
  const tabs = model.historySection !== 'generations' ? el('div', {class: 'history-sections', 'aria-label': '历史记录分类'},
    el('strong', {}, `记录代次 ${model.historyGeneration}`), Object.entries(HISTORY_SECTIONS).filter(([key]) => key !== 'generations').map(([key, label]) =>
      button(label, () => actions.changeHistorySection(key), {quiet: true, 'aria-pressed': model.historySection === key ? 'true' : 'false'}))) : null;
  if (!snapshot.data) return el('div', {}, header, tabs, unavailable(snapshot, actions.refreshHistory),
    model.historyPage > 0 ? button('上一页', () => actions.historyPage('previous'),
      {secondary: true, disabled: ['loading', 'refreshing'].includes(snapshot.status)}) : null);
  const data = snapshot.data;
  return el('div', {}, header, tabs, snapshotNotice(snapshot, actions.refreshHistory), detailSnapshotTime(snapshot, '历史快照'),
    definitions([['任务', data.task.task_id], ['任务当前状态', stateLabel(data.task.state)],
      ['创建代次', String(data.task.generation)], ['任务当前归属', `第 ${data.task.dispatch_generation} 代`],
      ['顶部查询上下文', `第 ${model.selection.generation} 代`]]),
    el('details', {class: 'history-task-text'}, el('summary', {}, '查看任务正文'), el('pre', {}, data.task.text)),
    data.section === 'notifications' ? info('账本中的投递状态不证明目的端接收、用户已读或模型采纳；查看历史不会确认投递。') : null,
    data.section === 'decisions' ? info('这里只读展示平台决定；当前代记录也不能在历史视图答复。原生工具审批未开放。') : null,
    el('div', {class: 'history-records'}, historyRows(data, actions)),
    el('div', {class: 'list-footer'}, button('上一页', () => actions.historyPage('previous'),
      {secondary: true, disabled: model.historyPage === 0 || snapshot.status !== 'ready'}),
    el('span', {}, `第 ${model.historyPage + 1} 页${data.next_cursor ? ' · 还有记录' : ' · 本分类已到末尾'}`),
    button('下一页', () => actions.historyPage('next'), {secondary: true, disabled: !data.next_cursor || snapshot.status !== 'ready'})));
}
function taskDetail(model, actions) {
  const snapshot = model.snapshots.detail;
  const shell = el('section', {class: 'panel detail-panel', id: 'detail-panel', 'aria-label': '任务详情'});
  if (!model.selectedTaskId) { shell.append(empty('选择一项任务', '查看时间线、已保存成果和运行中的补充信息。', {symbol: 'tasks'})); return shell; }
  if (model.detailTab === 'history') {
    shell.append(el('div', {class: 'detail-top'}, el('h2', {}, `任务 ${shortId(model.selectedTaskId)}`)), detailTabs(model, actions),
      el('div', {class: 'detail-content', id: 'detail-tab-content', role: 'tabpanel', 'aria-labelledby': 'detail-tab-history',
        'data-scroll-key': `history-${model.selectedTaskId}-${model.historySection}-${model.historyGeneration}-${model.historyPage}`, tabindex: 0}, taskHistory(model, actions)));
    return shell;
  }
  if (!snapshot.data) {
    shell.append(el('div', {class: 'detail-top'}, el('h2', {}, `任务 ${shortId(model.selectedTaskId)}`)), detailTabs(model, actions),
      snapshot.status === 'error' ? empty('此代详情暂时不可读', snapshot.error,
        {symbol: 'offline', action: button('重试当前代详情', actions.refreshDetail, {secondary: true})}) : loading('正在读取任务详情…'));
    if (historySupported(model)) shell.append(info('任务续接后，旧代详情可能不可用。可直接选择“跨代任务链”读取仍在库中的各代记录。'));
    return shell;
  }
  const data = snapshot.data, task = data.task;
  const stopAttempt = list(data.attempts).find(row => row.operations?.can_stop)
    || list(data.attempts).find(row => row.runtime?.stop_requested && !row.runtime?.termination_confirmed);
  shell.append(el('div', {class: 'detail-top'}, snapshotNotice(snapshot, actions.refreshDetail),
    el('div', {class: 'detail-title'}, el('div', {}, el('div', {class: 'eyebrow'}, 'TASK DETAIL'),
      el('h2', {}, '任务 ', shortId(task.task_id))), badge(task.state)),
    el('div', {class: 'detail-meta'}, el('span', {}, task.role_id),
      el('span', {}, `第 ${task.dispatch_generation} 代`), el('time', {}, formatTime(task.created_at))),
    el('div', {class: 'task-text', 'data-scroll-key': `task-text-${task.task_id}`}, task.text),
    detailSnapshotTime(snapshot),
    el('p', {class: 'scope-note'}, `仅展示第 ${model.selection.generation} 代相关记录，不是过去时点的完整快照。${data.history_available ? '该任务另有其他代次的记录，当前详情不包含完整跨代执行链。' : ''}`),
    data.history_available && !historySupported(model) ? el('p', {class: 'command-note'}, '此服务未声明跨代任务链能力，需使用支持此能力的版本查看完整历史。') : null), detailTabs(model, actions),
    el('div', {class: 'detail-content', id: 'detail-tab-content', role: 'tabpanel',
      'aria-labelledby': `detail-tab-${model.detailTab}`, 'data-scroll-key': `detail-${task.task_id}-${model.detailTab}`, tabindex: 0},
      model.detailTab === 'result' ? resultView(data) : model.detailTab === 'inputs' ? inputsView(data, model, actions)
        : model.detailTab === 'decisions' ? decisionsView(data, model, actions) : [handoffStatus(model, task.role_id, task, list(data.attempts)), timeline(data), runtimeFacts(data),
          stopAttempt ? el('section', {class: 'operation-section'}, el('h3', {}, '请求停止当前执行'),
            el('p', {class: 'command-note'}, '请求不会自动确认进程退出，请继续核对公开执行记录。'),
            commandForm(model, actions, 'stop', `${task.task_id}-${stopAttempt.attempt_id}`, {task_id: task.task_id, attempt_id: stopAttempt.attempt_id},
              {available: stopAttempt.operations?.can_stop === true && snapshot.status === 'ready', reason: stopAttempt.operations?.stop_reason})) : null]));
  return shell;
}
function runtimeFacts(data) {
  return el('div', {class: 'runtime-facts'}, list(data.attempts).filter(row => row.runtime).map(attempt => {
    const runtime = attempt.runtime;
    return el('section', {class: 'record-box'}, el('h3', {}, `执行 ${shortId(attempt.attempt_id)} 的公开记录`),
      definitions([['运行时', displayValue(runtime.runtime)], ...modelDefinitions(runtime), ...usageDefinitions(runtime),
        ['最近事实记录', formatTime(runtime.last_observation_at)],
        ['启动证据', runtime.started ? '已有启动记录' : '未记录'], ['停止请求', runtime.stop_requested ? '已请求' : '未记录'],
        ['退出证据', runtime.termination_confirmed ? '已记录确认' : '尚未确认']]),
      el('p', {class: 'command-note'}, MODEL_EVIDENCE_NOTE),
      el('div', {class: 'native-request-record'}, el('h4', {}, '原生工具请求'),
        definitions([['自动拒绝次数', Number.isSafeInteger(runtime.native_requests_denied?.count) && runtime.native_requests_denied.count >= 0
          ? `${runtime.native_requests_denied.count.toLocaleString('zh-CN')} 次` : '未记录'],
        ['最近请求类型', displayValue(runtime.native_requests_denied?.last_method, '未记录')],
        ['最近拒绝时间', formatTime(runtime.native_requests_denied?.last_at)]]), el('p', {class: 'command-note'}, NATIVE_DENIAL_NOTE)),
      el('p', {class: 'record-footer'}, '来源：已持久化的公开执行事件；不是即时模型连接探测。'));
  }), list(data.notifications).length ? el('section', {class: 'record-box'}, el('h3', {}, '平台通知'),
    el('p', {class: 'command-note'}, '以下为平台投递记录；没有目的端接收、用户已读或模型采纳证据。'), list(data.notifications).map(row =>
      el('p', {class: 'timeline-info'}, `${row.kind || row.event_kind || '公开通知'} · ${row.state === 'delivered' ? '账本标记已投递' : stateLabel(row.state)} · ${formatTime(row.created_at)}`)),
    truncation(data.truncated?.notifications)) : null);
}
function tasks(model, actions) { return el('div', {class: 'stack'}, model.detailTab !== 'history' ? taskComposer(model, actions) : null,
  el('div', {class: 'task-layout'}, taskBrowser(model, actions), taskDetail(model, actions))); }

function team(model) {
  const data = model.snapshots.team.data, roles = list(data.roles);
  if (!roles.length) return el('section', {class: 'panel'}, empty('尚无角色配置', '当前业务包还没有可显示的角色。', {symbol: 'team'}));
  return [el('div', {class: 'team-grid'}, roles.map(role => {
    const status = memberSnapshotPresentation(model.snapshots.team, model.selection, role.role_id);
    const member = status.member;
    return el('article', {class: 'panel role-card'}, el('div', {class: 'role-header'},
      el('div', {class: 'avatar', 'aria-hidden': 'true'}, Array.from(role.display_name || role.role_id || '?')[0]),
      el('div', {}, el('h2', {}, role.display_name || role.role_id), el('p', {class: 'role-id'}, role.role_id)),
      el('div', {class: 'role-status'}, badge(null, status.label, status.tone))),
      el('p', {class: 'role-instructions', 'data-scroll-key': `role-instructions-${role.role_id}`}, role.instructions),
      definitions([
        ['工作区', el('code', {}, displayValue(role.workspace, '未配置'))],
        ['访问上限', ({none: '不开放文件访问', 'read-only': '只读', 'read-write': '可读写'})[role.access] || displayValue(role.access, '未知')],
        ['声明能力', list(role.tools).length ? list(role.tools).join(' · ') : '无'],
        ['当前执行', member?.active_attempt_id ? `执行 ${shortId(member.active_attempt_id)}` : '未在快照中记录'],
        ['成员快照', formatTime(status.observedAt)],
        ['模型连通', '尚未验证'],
      ]), el('p', {class: 'role-footer'}, status.detail,
        member ? ` 最近更新 ${formatTime(member.updated_at)}。` : ''));
  })), truncation(data.truncated),
  info('成员状态来自本部署租约。显示“可接单”不等于模型在线；工作区访问上限也不代表已开放文件工具。')];
}

function resources(model, actions) {
  const data = model.snapshots.resources.data, knowledge = entries(data.knowledge);
  const workflowRows = entries(data.workflows), toolRows = entries(data.tools);
  return [el('section', {class: 'hero'}, el('div', {}, el('div', {class: 'eyebrow'}, 'BUSINESS PACKAGE'),
    el('h2', {}, data.package_id || '业务资料'), el('p', {}, `版本 ${displayValue(data.version)} · 资料来自当前安装的业务包快照。`)),
    el('div', {class: 'hero-emblem', 'aria-hidden': 'true'}, icon('resources', 40))),
  el('div', {class: 'resource-columns'}, el('div', {class: 'stack'},
    knowledge.length ? knowledge.map(([name, text]) => el('article', {class: 'panel knowledge-card'},
      el('div', {class: 'eyebrow'}, 'KNOWLEDGE'), el('h2', {}, name),
      el('pre', {'data-scroll-key': `knowledge-${name}`}, scalar(text))))
      : panel('知识资料', null, empty('没有知识条目', '此业务包未提供知识文本。', {compact: true, symbol: 'file'}))),
    el('div', {class: 'stack'},
      panel('业务流程', '来自业务包声明', workflowRows.length ? workflowRows.map(([id, workflow]) => el('div', {class: 'resource-row'},
        el('h3', {}, id), el('p', {}, workflow.description || '未提供流程说明'),
        el('p', {}, `参与角色：${list(workflow.roles).join(' · ') || '未记录'}`))) : empty('没有流程条目', '尚未声明业务流程。', {compact: true})),
      panel('通用能力', '声明不等于当前连接已通过验证', toolRows.length ? toolRows.map(([id, tool]) => el('div', {class: 'resource-row'},
        el('h3', {}, id), el('p', {}, tool.description || '未提供能力说明'), el('p', {}, `适配器：${displayValue(tool.adapter)}`)))
        : empty('没有能力条目', '业务包未声明工具能力。', {compact: true})),
      panel('成果引用', '平台结果与文件目录分开管理', empty('文件成果索引尚未接入',
        '当前可在任务详情查看已保存的文本成果。此处不会自动扫描工作区或原生会话。',
        {compact: true, symbol: 'file', action: button('查看任务成果', () => actions.navigate('tasks'), {quiet: true})}))))];
}

function connections(model) {
  const data = model.snapshots.connections.data, items = list(data.items);
  const cards = items.map(item => {
    const claude = item.runtime === 'claude';
    const available = item.supported === true;
    const statusText = claude || item.status === 'unavailable' ? '暂不可用' : '尚未验证';
    const reason = claude ? 'Claude Code 接入仍在设计中，当前暂不可用，尚未进行真实验收。'
      : available ? '已提供 Codex 执行适配。当前账户、连接与模型连通没有可用的验证记录。'
        : '当前没有可用的运行连接记录，不能据此发起任务执行。';
    const capabilities = item.capabilities?.codex_app_server || {};
    const capabilityLabels = [['mid_turn_steering', '运行中补充'], ['native_resume', '原生恢复'],
      ['business_file_tools', '业务文件工具'], ['interactive_approval', '原生审批']];
    return el('article', {class: 'panel connection-card'}, el('div', {class: 'connection-heading'},
      el('div', {class: 'provider-name'}, el('span', {class: 'provider-logo', 'aria-hidden': 'true'}, claude ? 'C' : '◎'),
        el('div', {}, el('h2', {}, claude ? 'Claude Code' : item.label || 'Codex'), el('small', {}, claude ? '规划接入' : '原生执行适配'))),
      badge(null, statusText, 'neutral')), el('p', {class: 'connection-reason'}, reason),
      definitions([['账户与登录', '尚未验证'], ['模型连通', item.connectivity === 'unavailable' ? '暂不可用' : '尚未验证'],
        ...modelDefinitions(item), ...usageDefinitions(item)]),
      el('p', {class: 'command-note'}, MODEL_EVIDENCE_NOTE),
      item.latest_attempt ? el('p', {class: 'record-footer'}, `来源：当前查询范围内执行 ${shortId(item.latest_attempt.attempt_id)} 的公开记录；不是当前连接状态。`) : null,
      claude ? el('p', {class: 'subtle-label'}, '真实 Claude 与混合团队验收尚待完成。') : el('div', {},
        el('p', {class: 'command-note'}, NATIVE_DENIAL_NOTE),
        el('p', {class: 'subtle-label'}, 'App Server 能力声明 · 不代表当前连接已验证'),
        el('div', {class: 'capabilities'}, capabilityLabels.map(([key, label]) => el('span', {class: 'capability'},
          `${label}：${capabilities[key] === true ? '支持声明' : capabilities[key] === false ? '未开放' : '未记录'}`)))));
  });
  const deployment = model.deployment.data;
  return [cards.length ? el('div', {class: 'connection-grid'}, cards)
    : el('section', {class: 'panel'}, empty('没有连接记录', '当前快照未提供运行时状态。', {symbol: 'link'})),
    el('div', {class: 'settings-grid'},
      panel('本机部署', '只展示非秘密的部署信息', el('div', {class: 'panel-body'}, definitions([
        ['平台版本', displayValue(deployment?.platform_version)], ['状态格式', displayValue(deployment?.schema_version)],
        ['当前业务', model.selection.installationId], ['业务代次', String(model.selection.generation)],
        ['状态目录', el('code', {}, displayValue(model.snapshots.connections.source?.state_root, '未提供'))],
      ]))),
      panel('数据与兼容边界', '协调记录、原生历史与文件成果分别管理', [
        el('div', {class: 'storage-row'}, icon('storage'), el('div', {}, el('strong', {}, '本部署协调状态'),
          el('p', {}, '任务、成员、决定与补充信息来自同一份平台记录。查看工作台不会修改这些记录。'))),
        el('div', {class: 'storage-row'}, icon('resources'), el('div', {}, el('strong', {}, '原生历史与认证'),
          el('p', {}, '由运行时独立管理。工作台不读取认证内容，不复制原生登录，也不自动导入私人会话。'))),
        el('div', {class: 'storage-row'}, icon('link'), el('div', {}, el('strong', {}, '旧 Agent Bus 兼容入口'),
          el('p', {}, '旧控制台的数据和任务属于独立来源。原功能目录保留全部 21 个入口；配置链接不等于旧服务已验证在线。'),
          compatibilityLink(model.workbench?.data?.legacy_console, 'fleet'))),
      ])),
    el('div', {class: 'read-only-footnote'}, info(commandGate(model) || '公共操作已启用：支持保存任务、运行中补充、明确决定和请求停止。执行服务仍由管理员独立启动。'))];
}

export function renderView(model, actions) {
  const fragment = document.createDocumentFragment();
  const [title, subtitle, eyebrow] = TITLES[model.page];
  fragment.append(heading(title, subtitle, eyebrow, el('span', {class: 'subtle-label'}, '本机 · 3.1 dev.3')),
    secondaryNavigation(model, actions));
  if (model.section === 'directory') { fragment.append(featureDirectory(model, actions)); return fragment; }
  if (model.section === 'help') { fragment.append(localHelp(model)); return fragment; }
  if (!model.selection) {
    if (!model.deployment.data) fragment.append(unavailable(model.deployment, actions.refresh));
    else fragment.append(el('section', {class: 'panel'}, empty(model.deployment.data.initialized ? '还没有安装业务' : '当前部署尚未初始化',
      '工作台已就绪。当前没有可查看的业务；浏览此页面不会创建状态、安装业务或启动执行器。', {symbol: 'overview'})));
    return fragment;
  }
  const snapshot = model.snapshots[model.page];
  if (model.page === 'tasks') { fragment.append(tasks(model, actions)); return fragment; }
  if (!snapshot.data) { fragment.append(unavailable(snapshot, actions.refreshPage)); return fragment; }
  const notice = snapshotNotice(snapshot, actions.refreshPage); if (notice) fragment.append(notice);
  const content = model.page === 'overview' ? overview(model, actions) : model.page === 'team' ? team(model)
    : model.page === 'resources' ? resources(model, actions) : connections(model);
  for (const child of [content].flat(Infinity)) if (child) fragment.append(child);
  return fragment;
}
