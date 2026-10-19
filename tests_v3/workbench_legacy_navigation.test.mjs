import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../ui/page_js_tabs.py', import.meta.url), 'utf8');
const section = source.slice(source.indexOf('TABS = r"""') + 'TABS = r"""'.length, source.indexOf('// 🙋'));
const body = readFileSync(new URL('../ui/page_body.py', import.meta.url), 'utf8');
const ids = [...body.matchAll(/<button class="tab" data-t="([^"]+)"/g)].map(match => match[1]);

function setup(hash, stored = 'fleet') {
  const events = {}, loads = [];
  const context = {location: {hash}, localStorage: {getItem: () => stored, setItem: (_key, value) => {stored = value;}},
    document: {querySelectorAll: () => []},
    window: {scrollTo() {}, addEventListener: (name, callback) => {events[name] = callback;}},
    history: {replaceState: (_state, _title, value) => {context.location.hash = value;}},
    _promptsLoaded: false, _archLoaded: false};
  for (const fn of ['loadBI', 'loadDispatch', 'loadUsage', 'loadCaps', 'loadCtx', 'loadWaitreg', 'loadPrompts',
    'loadArchive', 'loadTasklines', 'loadGroups', 'loadMechanisms', 'loadRoles', 'loadBizRoles', 'loadLongrange',
    'load', 'loadGuide', 'loadAgentProfile', 'loadPaths', 'loadStorage', 'loadFleetLog', 'loadASR']) {
    context[fn] = () => loads.push(fn);
  }
  vm.createContext(context);
  vm.runInContext(section, context);
  return {context, events, loads, tab: () => vm.runInContext('curTab', context), stored: () => stored};
}

test('all 21 explicit legacy deep links override the last selected tab without running actions', () => {
  assert.equal(ids.length, 21);
  for (const id of ids) {
    const state = setup('#' + id, 'tasks');
    assert.equal(state.tab(), id);
    assert.deepEqual(state.loads, []);
  }
});

test('invalid and prototype hashes cannot select pages or run work', () => {
  for (const hash of ['#constructor', '#__proto__', '#toString', '#../tasks', '#javascript:alert(1)', '#unknown']) {
    const state = setup(hash, 'usage');
    assert.equal(state.tab(), 'usage');
    state.events.hashchange();
    assert.deepEqual(state.loads, []);
  }
  assert.equal(setup('', 'constructor').tab(), 'fleet');
});

test('hash navigation loads the selected view and normal menu navigation updates its link', () => {
  const state = setup('#fleet');
  state.context.location.hash = '#usage';
  state.events.hashchange();
  assert.equal(state.tab(), 'usage');
  assert.deepEqual(state.loads, ['loadUsage']);
  state.events.hashchange();
  assert.deepEqual(state.loads, ['loadUsage']);
  vm.runInContext("showTab('roles')", state.context);
  assert.equal(state.context.location.hash, '#roles');
  assert.equal(state.stored(), 'roles');
});
