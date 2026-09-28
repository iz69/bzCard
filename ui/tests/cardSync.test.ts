import assert from 'node:assert/strict';
import test from 'node:test';
import { mergeServerCard } from '../src/cardSync.ts';

const queued = { id: 'a', status: 'queued', person_name: '', memo: '' };
const ready = { ...queued, status: 'ready', person_name: '解析結果' };
const editable = ['person_name', 'memo'] as const;

test('queued becomes ready and extracted fields appear', () => {
  assert.deepEqual(mergeServerCard(queued, queued, ready, editable), ready);
});
test('background refresh preserves unsaved fields but updates status and untouched fields', () => {
  const draft = { ...queued, memo: '入力中' };
  assert.deepEqual(mergeServerCard(draft, queued, ready, editable), { ...ready, memo: '入力中' });
});
test('switching cards does not copy edits from the previous card', () => {
  const next = { ...ready, id: 'b' };
  assert.deepEqual(mergeServerCard({ ...queued, memo: '未保存' }, queued, next, editable), next);
});
test('an explicitly cleared field remains cleared', () => {
  const previous = { ...ready, memo: 'old memo' };
  assert.equal(mergeServerCard({ ...previous, memo: '' }, previous, { ...previous, memo: 'server memo' }, editable).memo, '');
});
