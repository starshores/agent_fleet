import {SnapshotClient, installationPath} from './api.mjs';
import {PAGE_NAMES, chooseInstallation, createModel, emptySnapshot, formatTime, mergeTasks, reduceSnapshot, scopeKey, selectScope} from './state.mjs';
import {el, icon} from './ui.mjs';
import {renderView} from './views.mjs';
import {CommandStore, commandGate} from './commands.mjs';
import {historyContext, historySupported, HISTORY_SECTIONS, loadHistorySnapshot} from './history.mjs';

const client = new SnapshotClient();
const commands = new CommandStore();
let model = createModel();
let frame = null, resetOnPaint = false, focusAfterPaint = null, localMessage = '';
const readingPositions = new Map();
let lastReadingKey = null, lastPaintHadContent = false;
const view = document.getElementById('view');
const scopeSelect = document.getElementById('scope-select');
const generationInput = document.getElementById('generation-input');
const refreshButton = document.getElementById('refresh-button');
const statusBar = document.getElementById('snapshot-status');
const timeLabel = document.getElementById('snapshot-time');
const nav = document.getElementById('primary-nav');
const PAGE_SIZE = 20;

function captureReadingPosition() {
  const focused = document.activeElement;
  return {windowY: window.scrollY, focusId: view.contains(focused) ? focused.id : null,
    selection: view.contains(focused) && typeof focused.selectionStart === 'number'
      ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection] : null,
    scrolls: [...view.querySelectorAll('[data-scroll-key]')].map(node => [node.dataset.scrollKey, node.scrollTop, node.scrollLeft])};
}
function restoreReadingPosition(previous) {
  for (const node of view.querySelectorAll('[data-scroll-key]')) {
    const saved = previous.scrolls.find(([key]) => key === node.dataset.scrollKey);
    if (saved) { node.scrollTop = saved[1]; node.scrollLeft = saved[2]; }
  }
  if (previous.focusId) {
    const focused = document.getElementById(previous.focusId);
    focused?.focus({preventScroll: true});
    if (previous.selection && typeof focused?.setSelectionRange === 'function') focused.setSelectionRange(...previous.selection);
  }
  window.scrollTo({top: previous.windowY, behavior: 'instant'});
}
function visibleSnapshots() {
  return [model.deployment, model.workbench, model.snapshots[model.page], ...(model.page === 'tasks' && model.selectedTaskId
    ? [model.detailTab === 'history' ? model.snapshots.history : model.snapshots.detail] : [])];
}
function readingKey() {
  return JSON.stringify([scopeKey(model.selection), model.page, model.section, model.selectedTaskId, model.detailTab,
    ...(model.detailTab === 'history' ? [model.historySection, model.historyGeneration, model.historyPage] : [])]);
}
function hasReadingContent() {
  if (model.page === 'tasks' && model.selectedTaskId) return Boolean(model.snapshots[model.detailTab === 'history' ? 'history' : 'detail'].data);
  return model.section !== 'main' || Boolean(model.snapshots[model.page].data);
}
function updateHeader() {
  const installations = model.deployment.data?.installations || [];
  const signature = JSON.stringify(installations.map(row => [row.installation_id, row.package_id, row.state]));
  if (scopeSelect.dataset.signature !== signature) {
    scopeSelect.replaceChildren(...(installations.length ? installations.map(row => el('option', {value: row.installation_id},
      `${row.package_id} · ${row.installation_id}${row.state === 'active' ? '（已启用）' : ''}`))
      : [el('option', {value: ''}, model.deployment.data ? '暂无业务' : '尚未读取部署')]));
    scopeSelect.dataset.signature = signature;
  }
  scopeSelect.disabled = !installations.length;
  scopeSelect.value = model.selection?.installationId || '';
  const selected = installations.find(row => row.installation_id === model.selection?.installationId);
  generationInput.disabled = !selected;
  generationInput.max = String(selected?.generation ?? 0);
  if (document.activeElement !== generationInput) generationInput.value = String(model.selection?.generation ?? 0);
  generationInput.title = selected ? `可查询 0 至 ${selected.generation} 代；当前代次为 ${selected.generation}` : '尚无业务代次';
  for (const item of nav.querySelectorAll('[data-page]')) {
    if (item.dataset.page === model.page) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  }
  const visible = visibleSnapshots();
  const busy = visible.some(snapshot => snapshot.status === 'loading' || snapshot.status === 'refreshing');
  refreshButton.disabled = busy;
  refreshButton.textContent = busy ? '正在读取…' : '↻ 刷新快照';
  const current = model.snapshots[model.page];
  const observation = current.observedAt || model.deployment.observedAt;
  timeLabel.textContent = observation ? `快照 ${formatTime(observation, {short: true})}` : '尚未读取';
  const failed = visible.find(snapshot => snapshot.status === 'stale' || snapshot.status === 'error');
  const currentGeneration = selected?.generation;
  let message = localMessage;
  if (!navigator.onLine) message = '浏览器报告网络离线。保留当前快照；连接恢复后将重新读取。';
  else if (failed) message = failed.data ? '刷新未完成，正在显示上次快照。请留意页面中的更新时间。' : '暂时无法读取本机数据。可以刷新重试，现有状态不会因此改变。';
  else if (!message && selected && model.selection.generation !== currentGeneration) {
    message = `正在查看第 ${model.selection.generation} 代相关记录，当前安装为第 ${currentGeneration} 代。历史查询不是过去时点的完整快照。`;
  }
  statusBar.textContent = message;
  statusBar.className = `snapshot-status${message ? ' visible' : ''}${failed || !navigator.onLine || localMessage ? ' warning' : ''}`;
  view.setAttribute('aria-busy', busy ? 'true' : 'false');
  document.title = `${PAGE_NAMES[model.page]} · MasterAgent`;
  document.getElementById('operation-mode').textContent = model.workbench.status === 'ready' && model.workbench.data?.commands?.enabled ? '公共操作已启用' : '只读模式';
}
function schedulePaint({reset = false, focus = null} = {}) {
  resetOnPaint ||= reset;
  if (focus) focusAfterPaint = focus;
  if (frame !== null) return;
  frame = requestAnimationFrame(() => {
    frame = null;
    const previous = captureReadingPosition();
    if (lastReadingKey && lastPaintHadContent) readingPositions.set(lastReadingKey, previous);
    const nextKey = readingKey(), hasContent = hasReadingContent();
    updateHeader();
    view.replaceChildren(renderView(model, actions));
    const restore = hasContent ? nextKey === lastReadingKey && lastPaintHadContent && !resetOnPaint
      ? previous : readingPositions.get(nextKey) : null;
    if (restore) restoreReadingPosition(restore);
    else window.scrollTo({top: 0, behavior: 'instant'});
    lastReadingKey = nextKey;
    lastPaintHadContent = hasContent;
    if (focusAfterPaint) document.getElementById(focusAfterPaint)?.focus({preventScroll: true});
    resetOnPaint = false; focusAfterPaint = null;
  });
}
function receive(slot, event) {
  model.snapshots[slot] = reduceSnapshot(model.snapshots[slot], event);
  if (slot === 'tasks' && event.type === 'success') model.taskPageCount = event.envelope.data.page_count || 1;
  schedulePaint();
}
function applySelection(selection) {
  if (scopeKey(selection) === scopeKey(model.selection)) return false;
  client.setScope(selection);
  commands.setScope(selection);
  model = selectScope(model, selection);
  localMessage = '';
  schedulePaint({reset: true});
  return true;
}
async function loadDeployment() {
  return client.load('deployment', '/api/v1/deployment', event => {
    model.deployment = reduceSnapshot(model.deployment, event);
    if (event.type === 'success') {
      const data = event.envelope.data;
      const next = chooseInstallation(data.installations, data.active_installation_id, model.selection, event.envelope.source.state_root);
      const rootChanged = model.selection && model.selection.stateRoot !== event.envelope.source.state_root;
      applySelection(next);
      if (rootChanged) localMessage = '本机部署来源已变化，已清除旧业务快照并重新读取。';
    }
    schedulePaint();
  }, {deployment: true});
}
function loadSlot(slot, resource) {
  if (!model.selection) return Promise.resolve(false);
  return client.load(slot, installationPath(model.selection, resource), event => receive(slot, event));
}
async function loadTasks({append = false} = {}) {
  if (!model.selection) return false;
  const snapshot = model.snapshots.tasks;
  const previousItems = snapshot.data?.items || [];
  const cursor = append ? snapshot.data?.next_cursor : null;
  if (append && !cursor) return false;
  const filters = {...model.filters};
  const targetPages = append ? 1 : model.taskPageCount;
  const previousPages = model.taskPageCount;
  return client.load('tasks', async (read, selection) => {
    let envelope, next = cursor, collected = append ? previousItems : [], count = 0;
    const seenCursors = new Set();
    do {
      if (next && seenCursors.has(next)) throw new Error('分页游标重复，请刷新后重试。');
      if (next) seenCursors.add(next);
      envelope = await read(installationPath(selection, 'tasks', {...filters, limit: PAGE_SIZE, cursor: next}));
      if (!Array.isArray(envelope.data.items)) throw new Error('任务列表格式不完整。');
      collected = mergeTasks(collected, envelope.data.items);
      next = envelope.data.next_cursor;
      count += 1;
    } while (next && count < targetPages);
    return {...envelope, data: {...envelope.data, items: collected, next_cursor: next,
      page_count: append ? previousPages + count : count}};
  }, event => receive('tasks', event));
}
function loadDetail() {
  if (!model.selection || !model.selectedTaskId) return Promise.resolve(false);
  return loadSlot('detail', `tasks/${encodeURIComponent(model.selectedTaskId)}`);
}
function loadHistory() {
  if (!model.selection || !model.selectedTaskId || !historySupported(model)) return Promise.resolve(false);
  return loadHistorySnapshot(client, {...model.selection}, historyContext(model), event => receive('history', event));
}
function resetHistory({section = 'generations', generation = null} = {}) {
  client.abort('history');
  model.snapshots.history = emptySnapshot(); model.historySection = section; model.historyGeneration = generation;
  model.historyPage = 0; model.historyCursors = [null];
}
async function loadCurrentPage() {
  if (!model.selection) return;
  if (model.page === 'tasks') {
    await Promise.allSettled([loadTasks(), model.detailTab === 'history' ? loadHistory() : loadDetail(), loadSlot('team', 'team')]);
  } else await loadSlot(model.page, model.page);
}
async function refresh() {
  const accepted = await loadDeployment();
  // A scope switch intentionally cancels all older work. The deployment
  // response itself has already been accepted before selecting its scope.
  if (accepted) await Promise.allSettled([loadWorkbench(), loadCurrentPage()]);
}
function loadWorkbench() {
  return client.load('workbench', '/api/v1/workbench', event => {
    model.workbench = reduceSnapshot(model.workbench, event);
    if (event.type === 'success' && model.page === 'tasks' && model.detailTab === 'history') {
      if (historySupported(model)) loadHistory();
      else { client.abort('history'); model.snapshots.history = emptySnapshot(); }
    }
    schedulePaint();
  }, {deployment: true});
}
function navigate(page, {updateHash = true} = {}) {
  if (!Object.hasOwn(PAGE_NAMES, page)) page = 'overview';
  const changed = model.page !== page;
  model.page = page;
  model.section = 'main';
  localMessage = '';
  if (updateHash && location.hash !== `#${page}`) history.replaceState(null, '', `#${page}`);
  schedulePaint({reset: changed});
  if (model.selection) {
    if (page === 'tasks') {
      if (!model.snapshots.tasks.data) loadTasks();
      if (!model.snapshots.team.data) loadSlot('team', 'team');
      if (model.selectedTaskId && model.detailTab === 'history') {
        if (!model.snapshots.history.data) loadHistory();
      } else if (model.selectedTaskId && !model.snapshots.detail.data) loadDetail();
    } else if (!model.snapshots[page].data) loadSlot(page, page);
  }
}
const actions = {
  navigate,
  refresh,
  refreshPage: loadCurrentPage,
  refreshTasks: () => loadTasks(),
  refreshDetail: loadDetail,
  loadMore: () => loadTasks({append: true}),
  changeSection(section) { model.section = section; schedulePaint({reset: true}); },
  toggleComposer() { model.composerOpen = !model.composerOpen; schedulePaint({focus: model.composerOpen ? 'submit-role' : null}); },
  commandState: (action, target = '', fields = {}) => commands.get(model.selection, action, target, fields),
  editCommand(action, target, field, value) {
    commands.edit(model.selection, action, target, field, value);
    if (action === 'submit' && field === 'role_id') schedulePaint();
  },
  resetCommand(action, target) { commands.reset(model.selection, action, target); schedulePaint(); },
  async sendCommand(action, keyTarget = '', target = {}) {
    const blocked = commandGate(model);
    if (blocked) { localMessage = blocked; schedulePaint(); return; }
    const selection = {...model.selection};
    await commands.send(selection, action, keyTarget, target, model.workbench.data.commands.csrf_token, () => {
      schedulePaint();
      const record = commands.get(selection, action, keyTarget);
      if (record.status === 'success') {
        loadCurrentPage();
        if (model.page !== 'overview') loadSlot('overview', 'overview');
      }
    });
  },
  filterTasks(key, value) {
    client.abort('tasks'); client.abort('detail'); resetHistory();
    model.filters = {...model.filters, [key]: value};
    model.snapshots.tasks = emptySnapshot(); model.snapshots.detail = emptySnapshot();
    model.taskPageCount = 1; model.selectedTaskId = null; model.detailTab = 'timeline';
    schedulePaint(); loadTasks();
  },
  openTask(taskId) {
    if (typeof taskId !== 'string' || !taskId) return;
    if (model.selectedTaskId !== taskId) {
      client.abort('detail'); model.selectedTaskId = taskId;
      model.snapshots.detail = emptySnapshot(); model.detailTab = 'timeline'; resetHistory();
    }
    navigate('tasks');
  },
  editTaskLookup(value) { model.taskLookup = value; },
  openTaskHistory(taskId) {
    const id = typeof taskId === 'string' ? taskId.trim() : '';
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(id) || !historySupported(model)) return;
    client.abort('detail'); model.selectedTaskId = id; model.snapshots.detail = emptySnapshot();
    resetHistory(); model.detailTab = 'history'; navigate('tasks');
  },
  changeDetailTab(tab, focus = false) {
    model.detailTab = tab;
    schedulePaint({focus: focus ? `detail-tab-${tab}` : null});
    if (tab === 'history') loadHistory();
    else if (!model.snapshots.detail.data) loadDetail();
  },
  refreshHistory: loadHistory,
  changeHistorySection(section) {
    if (!Object.hasOwn(HISTORY_SECTIONS, section)) return;
    if (section !== 'generations' && !Number.isSafeInteger(model.historyGeneration)) return;
    resetHistory({section, generation: section === 'generations' ? null : model.historyGeneration});
    schedulePaint(); loadHistory();
  },
  openHistoryGeneration(generation) {
    if (!Number.isSafeInteger(generation) || generation < 0) return;
    resetHistory({section: 'continuations', generation}); schedulePaint(); loadHistory();
  },
  historyPage(direction) {
    if (['loading', 'refreshing'].includes(model.snapshots.history.status)) return;
    const cursor = model.snapshots.history.data?.next_cursor;
    if (direction === 'next' && cursor) {
      model.historyCursors = [...model.historyCursors.slice(0, model.historyPage + 1), cursor]; model.historyPage += 1;
    } else if (direction === 'previous' && model.historyPage > 0) model.historyPage -= 1;
    else return;
    client.abort('history'); model.snapshots.history = emptySnapshot(); schedulePaint(); loadHistory();
  },
};

for (const [page, title] of Object.entries(PAGE_NAMES)) {
  nav.append(el('a', {href: `#${page}`, class: 'nav-item', 'data-page': page, title, onclick: event => {
    event.preventDefault(); navigate(page);
  }}, icon(page, 19), el('span', {}, title)));
}
scopeSelect.addEventListener('change', () => {
  const installation = model.deployment.data?.installations.find(row => row.installation_id === scopeSelect.value);
  if (!installation) return;
  applySelection({stateRoot: model.deployment.source.state_root, installationId: installation.installation_id, generation: installation.generation});
  loadCurrentPage();
});
generationInput.addEventListener('change', () => {
  const generation = Number(generationInput.value);
  const max = Number(generationInput.max);
  if (!Number.isInteger(generation) || generation < 0 || generation > max || generationInput.value.trim() === '') {
    generationInput.value = String(model.selection?.generation ?? 0);
    localMessage = `请输入 0 至 ${max} 之间的整数代次。`; schedulePaint(); return;
  }
  if (model.selection && applySelection({...model.selection, generation})) loadCurrentPage();
});
refreshButton.addEventListener('click', refresh);
window.addEventListener('hashchange', () => navigate(location.hash.slice(1), {updateHash: false}));
window.addEventListener('online', refresh);
window.addEventListener('offline', () => schedulePaint());
window.addEventListener('beforeunload', event => {
  if (commands.hasUnsaved()) { event.preventDefault(); event.returnValue = ''; }
});
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
model.page = Object.hasOwn(PAGE_NAMES, location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
schedulePaint();
refresh();
