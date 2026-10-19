import {PAGE_NAMES} from './state.mjs';
import {el, badge, button, info, panel} from './ui.mjs';

/** The legacy labels are navigation compatibility, never business branches. */
export const FEATURE_DIRECTORY = Object.freeze([
  ['fleet', '舰队/运维', 'team', 'partial', '当前角色与成员租约已有局部视图；宿主、自醒与恢复仍属原控制台。'],
  ['guide', '操作说明', 'overview', 'help', '本机帮助已提供；旧指南仍可从兼容入口查阅。'],
  ['agentprofile', '数据画像', 'team', 'missing', '历史画像和分析窗口尚未迁入。'],
  ['bi', '运营/资源', 'overview', 'missing', '运行成本与效率统计尚未迁入；任务数量不是资源统计。'],
  ['usage', '历史用量', 'connections', 'partial', '可查看公开记录中的 token 数值、来源与新会话累计范围；趋势与跨执行历史汇总尚未迁入。'],
  ['caps', '能力池', 'team', 'partial', '角色声明可见；授权矩阵、风险与缺口尚未迁入。'],
  ['ctx', '上下文', 'connections', 'missing', '精确会话占用与压缩预警尚未迁入。'],
  ['waitreg', 'wait 注册表', 'connections', 'missing', '旧信使投递链和 wait 生命周期仍在兼容控制台。'],
  ['prompts', '提示词审核', 'resources', 'partial', '业务包角色指令可见；全局注入来源审计尚未迁入。'],
  ['archive', '归档/退役', 'resources', 'missing', '生命周期档案、接替者与恢复引用尚未迁入。'],
  ['tasklines', '任务线看板', 'tasks', 'missing', '跨任务线和阶段看板尚未迁入。'],
  ['groups', '工作组', 'team', 'missing', '工作组成员、组队列与并行看板尚未迁入。'],
  ['mechanisms', '机制', 'connections', 'missing', '平台机制注册表尚未迁入。'],
  ['roles', '角色', 'team', 'partial', '包内角色、职责与权限声明可见；完整生命周期档案尚未迁入。'],
  ['bizroles', '业务角色', 'team', 'partial', '当前业务包角色可见；旧注册表不自动导入。'],
  ['longrange', '长程任务', 'tasks', 'missing', '里程碑、任务图与批量裁决尚未迁入。'],
  ['tasks', '任务/协作', 'tasks', 'partial', '已有 V3 任务、保存后交接提示及跨代只读任务链；持久目的端回执、独立 TODO、旧回报池尚未迁入。'],
  ['live', '实时', 'tasks', 'partial', '可查看公开执行、模型报告、用量及原生请求拒绝事件快照；原生日志流与持久订阅尚未迁入。'],
  ['paths', '路径', 'resources', 'partial', '部署和工作区路径可见；存储全景、文件索引与目录操作尚未迁入。'],
  ['fleetlog', '舰队日志', 'connections', 'missing', '平台编年日志尚未迁入；单任务事件不是舰队日志。'],
  ['asr', '语音转写', 'resources', 'missing', '设备、转写和回放保留在本机独立扩展。'],
].map(([id, label, page, coverage, description]) => Object.freeze({id, label, page, coverage, description})));

export function legacyLink(config, id) {
  if (!config?.configured || !FEATURE_DIRECTORY.some(item => item.id === id)) return null;
  try {
    const explicit = /^http:\/\/(127\.0\.0\.1|localhost):([0-9]+)\/?$/i.exec(config.url);
    if (!explicit || Number(explicit[2]) < 1 || Number(explicit[2]) > 65535) return null;
    const url = new URL(config.url);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)
        || url.username || url.password || url.search || url.hash) return null;
    url.hash = id; return url.href;
  } catch { return null; }
}
export function compatibilityLink(config, id, label = '打开原控制台') {
  const href = legacyLink(config, id);
  return href ? el('a', {class: 'button button-secondary', href, target: '_blank', rel: 'noopener noreferrer'}, label)
    : button('兼容入口未配置', null, {secondary: true, disabled: true, title: '由本机管理员配置旧控制台地址后可用。'});
}
export function secondaryNavigation(model, actions) {
  return el('nav', {class: 'secondary-nav', 'aria-label': '二级功能'},
    [['main', `${PAGE_NAMES[model.page]}概览`], ['directory', '原功能目录 · 21'], ['help', '操作说明']].map(([key, label]) =>
      el('button', {type: 'button', class: 'secondary-item', 'aria-current': model.section === key ? 'page' : null,
        onclick: () => actions.changeSection(key)}, label)));
}
export function featureDirectory(model, actions) {
  const config = model.workbench?.data?.legacy_console;
  return el('div', {class: 'stack'}, info('原有 21 个入口逐项保留去向。“已提供兼容链接”只表示已提供链接，未验证旧服务是否在线，也不代表功能已迁入。'),
    config?.configured ? info('若旧服务尚未加载更新后的页面，兼容链接可能仍进入上次页签；可在原控制台手动切换。') : null,
    !config?.configured ? info('尚未配置原控制台地址。由管理员使用 --legacy-console-url 显式设置本机地址；这里不会自动探测或启动旧服务。') : null,
    ...Object.entries(PAGE_NAMES).map(([page, label]) => panel(label, '新工作台内的归属分类',
      el('div', {class: 'feature-directory'}, FEATURE_DIRECTORY.filter(row => row.page === page).map(row =>
        el('article', {class: 'feature-row'}, el('div', {class: 'feature-description'}, el('h3', {}, row.label),
          el('p', {}, row.description), el('div', {class: 'feature-badges'},
            badge(null, row.coverage === 'help' ? '本机帮助已接入' : row.coverage === 'partial' ? '原生局部对应' : '尚未迁入', row.coverage === 'help' ? 'success' : 'neutral'),
            legacyLink(config, row.id) ? badge(null, '已提供兼容链接', 'accent') : badge(null, '兼容入口未配置', 'warning'))),
          el('div', {class: 'feature-actions'}, row.coverage !== 'missing' ? button(row.coverage === 'help' ? '查看本机帮助' : '查看新视图',
            () => row.coverage === 'help' ? actions.changeSection('help') : actions.navigate(page), {quiet: true}) : null,
            compatibilityLink(config, row.id))))))));
}
export function localHelp(model) {
  const enabled = model.workbench?.data?.commands?.enabled === true;
  return el('div', {class: 'stack'}, panel('在本机工作台处理工作', '3.1 dev.3 · 公共操作与迁移过渡',
    el('div', {class: 'panel-body help-content'},
      el('p', {}, '先在顶部选择业务和代次。五个主入口负责分类，原有功能可在“原功能目录”逐项找到。兼容链接在新标签页打开，不会混合两套队列。'),
      el('h3', {}, '新任务与待办'), el('p', {}, '任务页选择角色，填写内容并保存后进入 V3 执行队列。它不是独立 TODO，也不会启动模型；需要独立运行的服务接单。保存后可直接打开任务，按同业务、代次、角色核对成员快照与领取记录。没有成员仍可保存排队；快照过期或不完整时不能认定无人接单。模型连接状态仍须核实。'),
      el('h3', {}, '补充信息与明确决定'), el('p', {}, '运行中补充只用于纠正或补充文本。“已保存”“发送中”“已接收”分别表示不同阶段，不能作为批准。决定页必须明确选择批准或拒绝，未选择不会提交；公共决定不等于原生工具审批。'),
      el('h3', {}, '模型、用量与原生工具'), el('p', {}, '请求模型、服务端报告模型和实际模型分别展示。服务端报告表示会话配置或改道目标，不证明实际推理所用模型；未知值不会替换为请求模型或 0。用量显示通知来源与本次新建原生会话累计范围，不代表单轮增量或账户总额度。当前纯文本模式自动拒绝原生工具请求，未开放人工批准；拒绝记录只展示请求类型与原因，不展示命令或补丁内容。'),
      el('h3', {}, '请求停止'), el('p', {}, '停止请求保存停止意图，由原执行服务继续处理，不会立即撤销执行归属或确认进程退出。只有后续退出证据才能确认进程已停止；请求回执不能证明原生进程已经退出。'),
      el('h3', {}, '刷新、草稿与结果不明'), el('p', {}, '刷新会保留本页未提交内容、焦点与文本选择。草稿按状态目录、业务、代次和任务分别保存在本标签页内存；关闭或重载页面会丢失未提交草稿。提交后内容锁定；若回执不明，只能以原内容和原标识重试，先刷新核对可避免误解。'),
      el('h3', {}, '查看历史与原控制台'), el('p', {}, '历史代次只能查看相关记录。支持跨代任务链的服务还可按完整任务 ID 打开只读历史，查看代次目录及每代接续、执行、平台决定、成果、补充、公开事件和通知；当前代详情不可读时仍可打开。各分类分别分页，任务状态是当前事实，分页并非过去时点快照。详情和历史都有独立快照时间；需刷新读取后续记录。原控制台有独立来源与权限；链接失败时请管理员核查旧服务，不会自动降级代发。'),
      el('h3', {}, '当前操作模式'), el('p', {}, enabled ? '服务已显式启用公共操作；只有当前启用业务的当前代次允许操作，服务端会再次校验。'
        : '当前只读。管理员可用 --enable-commands 重启工作台以显式开放公共操作；页面不会自行启用。'),
      el('p', {}, '查看页面不会迁移状态、读取认证或导入私人会话。Claude Code 暂不可用，真实混合团队尚未验收。'))));
}
