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

for (const method of ['tasks.savePrices', 'sessions.rest', 'sessions.cancelRest', 'studio.sendSummary', 'fabric.uninstall',
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

test('reference playback does not block closing the desktop', async () => {
  const b = boundary();
  const pending = b.handlers['shiguang:request']({ from: b.main }, 'fabric.playReference', { roleId: 'example', reference: '0:1' });
  assert.equal(b.context.writes, 0);
  b.resolve({ id: 'a'.repeat(32) });
  assert.deepEqual(await pending, { id: 'a'.repeat(32) });
});

test('the voice protocol serves original OGG bytes and rejects non-audio MIME types', async () => {
  const source = fs.readFileSync(require.resolve('../electron/main.cjs'), 'utf8');
  const id = 'a'.repeat(32), bytes = Buffer.from('OggS fixture');
  const audio = { data: bytes.toString('base64'), mime: 'audio/ogg' };
  let handler;
  const context = vm.createContext({
    Buffer, URL, Response,
    protocol: { handle: (scheme, callback) => { assert.equal(scheme, 'sg-media'); handler = callback; } },
    broker: { call: (method, params) => {
      assert.equal(method, 'fabric.previewMedia');
      assert.equal(params.id, id);
      return audio;
    } },
  });
  vm.runInContext(source.slice(source.indexOf('function registerMedia()'), source.indexOf('function showMain()')) +
    '\nregisterMedia();', context);
  const request = new Request('sg-media://voice/' + id, { headers: { range: 'bytes=0-3' } });
  const response = await handler(request);
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('content-type'), 'audio/ogg');
  assert.equal(await response.text(), 'OggS');
  audio.mime = 'text/html';
  assert.equal((await handler(request)).status, 415);
});
