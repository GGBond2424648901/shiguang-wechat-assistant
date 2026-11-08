const { test } = require('node:test');
const assert = require('node:assert/strict');
const { rebaseSessions, sameOrder } = require('../src/session-draft.ts');
const row = (id, fields = {}) => ({ id, name: id, kind: 'private', bound: true, paused: false,
  calibrated: false, replyMode: null, stickerFrequency: null, ...fields });
const saved = (items, fields = {}) => ({ items, monitorAll: true, stickersEnabled: true,
  defaultReplyMode: 'natural', defaultStickerFrequency: 'natural', accountLabel: '示例账号', accountKey: 'account-a', revision: '1', ...fields });
const draft = (base, fields = {}) => ({ order: base.items, monitor: base.monitorAll, stickers: base.stickersEnabled,
  defaultMode: base.defaultReplyMode, rules: {}, ...fields });

test('remote imports and calibration update while local rule edits survive', () => {
  const base = saved([row('a'), row('b')]);
  const next = saved([row('a', { calibrated: true }), row('b', { stickerFrequency: 'expressive' }), row('c')]);
  const merged = rebaseSessions(base, draft(base, { rules: { b: { replyMode: 'mentions', stickerFrequency: null } } }), next);
  assert.deepEqual(merged.order.map(item => item.id), ['a', 'b', 'c']);
  assert.equal(merged.order[0].calibrated, true);
  assert.deepEqual(merged.rules, { b: { replyMode: 'mentions', stickerFrequency: 'expressive' } });
  assert.equal(sameOrder(merged.order, next.items), true);
});
test('local reorder and removal coexist with remote additions and settings', () => {
  const base = saved([row('a'), row('b'), row('c')]);
  const next = saved([row('a'), row('b'), row('c'), row('d')], { defaultReplyMode: 'all', stickersEnabled: false });
  const merged = rebaseSessions(base, draft(base, { order: [base.items[2], base.items[0]], monitor: false }), next);
  assert.deepEqual(merged.order.map(item => item.id), ['c', 'a', 'd']);
  assert.equal(merged.monitor, false);
  assert.equal(merged.stickers, false);
  assert.equal(merged.defaultMode, 'all');
});
test('a directory draft that has already been saved remotely is not duplicated', () => {
  const base = saved([row('a')]);
  const next = saved([row('a'), row('bound-b', { contactId: 'contact_b' })]);
  const merged = rebaseSessions(base, draft(base, { order: [...base.items, row('contact_b')] }), next);
  assert.deepEqual(merged.order.map(item => item.id), ['a', 'bound-b']);
  assert.equal(sameOrder(merged.order, next.items), true);
});
test('saved rules and refreshed metadata do not create a false range edit', () => {
  const base = saved([row('a')]);
  const next = saved([row('a', { replyMode: 'all', paused: true })]);
  const merged = rebaseSessions(base, draft(base, { rules: { a: { replyMode: 'all', stickerFrequency: null } } }), next);
  assert.deepEqual(merged.rules, {});
  assert.equal(sameOrder(merged.order, next.items), true);
  assert.equal(merged.order[0].paused, true);
});
test('remote removals do not resurrect old contacts or their draft rules', () => {
  const base = saved([row('a'), row('b')]);
  const next = saved([row('a'), row('c')]);
  const merged = rebaseSessions(base, draft(base, { order: [base.items[1], base.items[0]],
    rules: { b: { replyMode: 'all', stickerFrequency: null } } }), next);
  assert.deepEqual(merged.order.map(item => item.id), ['a', 'c']);
  assert.deepEqual(merged.rules, {});
});
test('appending a contact does not overwrite a remotely changed primary conversation', () => {
  const base = saved([row('a'), row('b')]);
  const next = saved([row('b'), row('a')]);
  const merged = rebaseSessions(base, draft(base, { order: [...base.items, row('new')] }), next);
  assert.deepEqual(merged.order.map(item => item.id), ['b', 'a', 'new']);
});
test('an explicitly promoted unsaved contact remains first', () => {
  const base = saved([row('a'), row('b')]);
  const next = saved([row('b'), row('a')]);
  const merged = rebaseSessions(base, draft(base, { order: [row('new'), ...base.items] }), next);
  assert.deepEqual(merged.order.map(item => item.id), ['new', 'a', 'b']);
});
test('a different account never inherits the previous accounts unsaved settings', () => {
  const base = saved([row('a')]);
  const next = saved([row('other')], { accountKey: 'account-b' });
  const merged = rebaseSessions(base, draft(base, { monitor: false, defaultMode: 'all' }), next);
  assert.deepEqual(merged.order, next.items);
  assert.equal(merged.monitor, true);
  assert.equal(merged.defaultMode, 'natural');
});
test('opposing removals leave a recoverable list and report their conflict', () => {
  const base = saved([row('a'), row('b')]);
  const next = saved([row('a')]);
  const merged = rebaseSessions(base, draft(base, { order: [base.items[1]] }), next);
  assert.deepEqual(merged.order, next.items);
  assert.equal(merged.orderConflict, true);
});
