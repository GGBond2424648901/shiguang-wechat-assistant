const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function boundary() {
  const source = fs.readFileSync(require.resolve('../electron/main.cjs'), 'utf8');
  const handlers = {}, main = {}, prompt = {};
  let resolve, reject, stopped = 0;
  const held = new Promise((yes, no) => { resolve = yes; reject = no; });
  const context = vm.createContext({
    ipcMain: { handle: (key, handler) => { handlers[key] = handler; } },
    main, prompt, identify: event => event.from, stopping: false, readOnly: false,
    drafts: new Set(), writes: 0, status: { tray: true }, broker: { call: () => held },
    stopResident: async () => { stopped++; }, refreshStatus() {},
    acknowledgeTrayExit: async () => {}, app: { quit() {} },
  });
  const declarations = ['draftPages', 'allowed', 'mutatingMethods'].map(name =>
    source.match(new RegExp(`const ${name} = new Set\\([\\s\\S]*?\\);`))?.[0] || '').join('\n');
  vm.runInContext(declarations + '\n' + source.slice(source.indexOf('function registerIpc()'), source.indexOf('function registerMedia()')) + '\nregisterIpc();', context);
  return { handlers, context, main, prompt, resolve, reject, stopped: () => stopped };
}

test('usage pricing is a native guarded draft, with invalid keys still rejected', async () => {
  const b = boundary(), draft = b.handlers['shiguang:draft'];
  draft({ from: b.main }, 'usage-pricing', true);
  assert.equal(b.context.drafts.has('usage-pricing'), true);
  await assert.rejects(b.handlers['shiguang:window']({ from: b.prompt }, 'exit'), /未保存/);
  assert.equal(b.stopped(), 0);
  assert.throws(() => draft({ from: b.main }, 'invented-page', true), /无效/);
  assert.throws(() => draft({ from: b.main }, 'usage-pricing', 'yes'), /无效/);
  draft({ from: b.main }, 'usage-pricing', false);
  await b.handlers['shiguang:window']({ from: b.prompt }, 'exit');
  assert.equal(b.stopped(), 1);
});

for (const method of ['tasks.savePrices', 'sessions.rest', 'sessions.cancelRest', 'studio.sendSummary',
  'news.feedback', 'news.clearFeedback', 'news.delete', 'studio.chat', 'studio.summary', 'studio.cancel']) {
  for (const rejected of [false, true]) test(`${method} protects close while pending and releases on ${rejected ? 'rejection' : 'success'}`, async () => {
    const b = boundary();
    const pending = b.handlers['shiguang:request']({ from: b.main }, method, {});
    assert.equal(b.context.writes, 1);
    await assert.rejects(b.handlers['shiguang:window']({ from: b.prompt }, 'discardExit'), /等待当前操作/);
    await assert.rejects(b.handlers['shiguang:window']({ from: b.prompt }, 'background'), /等待操作/);
    assert.equal(b.stopped(), 0);
    if (rejected) {
      b.reject(new Error('request rejected'));
      await assert.rejects(pending, /request rejected/);
    } else {
      b.resolve({ saved: true });
      assert.deepEqual(await pending, { saved: true });
    }
    assert.equal(b.context.writes, 0);
    await b.handlers['shiguang:window']({ from: b.prompt }, 'exit');
    assert.equal(b.stopped(), 1);
  });
}
