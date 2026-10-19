import {installationPath} from './api.mjs';

export const HISTORY_SECTIONS = Object.freeze({generations: '代次目录', continuations: '接续记录',
  attempts: '执行尝试', decisions: '平台决定', results: '成果', inputs: '补充信息', events: '公开事件', notifications: '平台通知'});

export function historySupported(model) {
  return model.workbench?.status === 'ready'
    && model.workbench.source?.state_root === model.selection?.stateRoot
    && model.workbench.data?.capabilities?.task_history === true;
}
export function historyContext(model) {
  return {taskId: model.selectedTaskId, section: model.historySection,
    recordGeneration: model.historySection === 'generations' ? null : model.historyGeneration,
    cursor: model.historyCursors[model.historyPage] ?? null};
}
export function historyPath(selection, context) {
  if (typeof context.taskId !== 'string' || !context.taskId || !Object.hasOwn(HISTORY_SECTIONS, context.section)
      || context.section !== 'generations' && (!Number.isSafeInteger(context.recordGeneration) || context.recordGeneration < 0)) {
    throw new Error('请选择有效任务、历史分类及记录代次。');
  }
  return installationPath(selection, `task-history/${encodeURIComponent(context.taskId)}`, {
    section: context.section, record_generation: context.section === 'generations' ? null : context.recordGeneration,
    limit: 20, cursor: context.cursor,
  });
}
export function validateHistory(envelope, context) {
  const data = envelope.data;
  if (data.readonly !== true || data.scope?.kind !== 'task_history' || data.scope.task_id !== context.taskId
      || data.task?.task_id !== context.taskId || data.section !== context.section
      || data.record_generation !== context.recordGeneration || !Array.isArray(data.items)
      || !(data.next_cursor === null || typeof data.next_cursor === 'string' && data.next_cursor.length > 0)) {
    throw new Error('历史记录与当前任务或分类不一致，请重新读取。');
  }
  return envelope;
}
/** One replaceable slot covers task, section, generation and page changes. */
export function loadHistorySnapshot(client, selection, context, notify) {
  const captured = {...context};
  return client.load('history', async read => validateHistory(await read(historyPath(selection, captured)), captured), notify);
}
