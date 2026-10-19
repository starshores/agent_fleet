import {stateLabel, stateTone, formatTime} from './state.mjs';

export function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === 'class') node.className = value;
    else if (name.startsWith('on') && typeof value === 'function') node.addEventListener(name.slice(2), value);
    else if (name === 'disabled' || name === 'selected') node[name] = value;
    else node.setAttribute(name, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const ICONS = {
  overview: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
  tasks: ['M8 5H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1h-3', 'M8 3h8v4H8z', 'M8 12h8', 'M8 16h5'],
  team: ['M15 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2', 'M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0', 'M21 21v-2a4 4 0 0 0-3-3.87', 'M17 3.13a4 4 0 0 1 0 7.75'],
  resources: ['M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z', 'M7 11h10', 'M7 15h7'],
  connections: ['M4 6h16', 'M4 12h16', 'M4 18h16', 'M8 3v6', 'M16 9v6', 'M10 15v6'],
  arrow: ['M5 12h14', 'm13 6 6 6-6 6'],
  clock: ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', 'M12 6v6l4 2'],
  check: ['M20 6 9 17l-5-5'],
  info: ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', 'M12 11v6', 'M12 7h.01'],
  attention: ['M12 3 2 21h20L12 3z', 'M12 9v5', 'M12 17h.01'],
  spark: ['m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3L12 3z'],
  link: ['M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2', 'M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2'],
  file: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6', 'M8 13h8', 'M8 17h5'],
  storage: ['M3 4h18v6H3z', 'M3 14h18v6H3z', 'M7 7h.01', 'M7 17h.01'],
  offline: ['M2 2l20 20', 'M8 8a7 7 0 0 0-3 3', 'M10 3a17 17 0 0 1 12 6', 'M2 9a17 17 0 0 1 3-3', 'M8 16a6 6 0 0 1 8 0', 'M12 20h.01'],
};
export function icon(name, size = 18) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({viewBox: '0 0 24 24', width: size, height: size,
    fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round',
    'stroke-linejoin': 'round', 'aria-hidden': 'true'})) svg.setAttribute(key, String(value));
  for (const d of ICONS[name] ?? ICONS.info) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', d); svg.append(path);
  }
  return svg;
}
export function button(text, onclick, {quiet = false, secondary = false, ...attributes} = {}) {
  return el('button', {type: 'button', class: `button${quiet ? ' button-quiet' : secondary ? ' button-secondary' : ''}`, onclick, ...attributes}, text);
}
export function badge(state, label = null, tone = null) {
  return el('span', {class: `badge ${tone ?? stateTone(state)}`}, label ?? stateLabel(state));
}
export function heading(title, subtitle, eyebrow = 'WORKSPACE', aside = null) {
  return el('div', {class: 'page-heading'}, el('div', {}, el('div', {class: 'eyebrow'}, eyebrow),
    el('h1', {}, title), el('p', {}, subtitle)), aside);
}
export function panel(title, subtitle, content, {action = null, className = ''} = {}) {
  return el('section', {class: `panel ${className}`}, el('div', {class: 'panel-header'},
    el('div', {}, el('h2', {}, title), subtitle ? el('p', {}, subtitle) : null), action), content);
}
export function empty(title, text, {compact = false, symbol = 'resources', action = null} = {}) {
  return el('div', {class: `empty-state${compact ? ' compact' : ''}`}, el('div', {class: 'empty-icon'}, icon(symbol, 23)),
    el('h3', {}, title), el('p', {}, text), action);
}
export function info(text, symbol = 'info') { return el('div', {class: 'info-strip'}, icon(symbol, 16), el('span', {}, text)); }
export function loading(label = '正在读取快照…') {
  return el('div', {class: 'loading-inline', role: 'status'}, el('span', {class: 'loading-orbit', 'aria-hidden': 'true'}), label);
}
export function snapshotNotice(snapshot, retry) {
  if (snapshot.status === 'stale') return el('div', {class: 'error-note', role: 'status'},
    `刷新未完成，正在显示 ${formatTime(snapshot.observedAt)} 的旧快照。${snapshot.error || ''}`,
    retry ? button('重试', retry, {quiet: true}) : null);
  return null;
}
export function unavailable(snapshot, retry) {
  if (snapshot.status === 'idle' || snapshot.status === 'loading') return el('section', {class: 'panel'}, loading());
  return el('section', {class: 'panel'}, empty('暂时无法读取', snapshot.error || '请检查本机服务并重试。',
    {symbol: 'offline', action: retry ? button('重新读取', retry, {secondary: true}) : null}));
}
export function definitions(items) {
  return el('dl', {class: 'definition-grid'}, items.flatMap(([label, value]) => [el('dt', {}, label), el('dd', {}, value)]));
}
export function truncation(value) {
  const truncated = value === true || (value && Object.values(value).some(item => item === true));
  return truncated ? el('p', {class: 'truncation'}, '当前显示有界记录，较早内容未包含在此快照中。') : null;
}
