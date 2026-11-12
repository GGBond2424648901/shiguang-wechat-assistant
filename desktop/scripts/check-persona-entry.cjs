/* Rendered entry, draft preservation, contrast and zoom regression; isolated RPC data. */
const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const dist = path.resolve(__dirname, '../dist');
const output = process.env.SG_PERSONA_OUTPUT;
const contrastOnly = process.argv.includes('--contrast-only');
const longModules = process.argv.includes('--long-modules');
const server = http.createServer((req, res) => {
  const name = req.url === '/' ? '/index.html' : decodeURIComponent(req.url.split('?')[0]);
  if (name === '/favicon.ico') { res.writeHead(204).end(); return; }
  const file = path.resolve(dist, '.' + name);
  if (!file.startsWith(dist + path.sep)) { res.writeHead(403).end(); return; }
  try {
    res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.png') ? 'image/png' : 'text/html');
    res.end(fs.readFileSync(file));
  } catch { res.writeHead(404).end(); }
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: 'chrome' });
    const page = await browser.newPage({ viewport: { width: 1488, height: 960 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.addInitScript(longNames => {
      const persona = { format: 'modules', title: '测试人格', revision: 'persona-1', modules: Array.from({ length: longNames ? 48 : 16 }, (_, index) => ({
        id: index ? 'module-' + index : 'main', title: index ? longNames ? 'PERSONA'.repeat(14) + index : '人格模块与语气设定 ' + index : '基础人格',
        text: index ? '模块原文 ' + index : '请自然、友好地回答。', enabled: longNames || index !== 15, origin: '隔离示例',
      })) };
      const status = { connected: true, readOnly: false, mode: 'observe', reason: '', primary: '', sessions: [], revision: 'account-1', tray: false,
        stats: { pending: 0, verified: 0, unknown: 0, failed: 0, skipped: 0 } };
      let job;
      window.personaEntryCalls = [];
      window.shiguang = { draft: async () => {}, watch: () => () => {}, windowState: () => () => {},
        appInfo: async () => ({ maximized: false, hasWallpaper: false }), zoom: async () => {}, windowAction: async () => {},
        request: async (method, params = {}) => {
          window.personaEntryCalls.push({ method, params });
          if (method === 'view.status') return status;
          if (method === 'view.queue') return { total: 0, receiptTotal: 0, note: '', tasks: [], receipts: [] };
          if (method === 'appearance.read') return { transparency: 65, reduceMotion: false, zoomPercent: 100, revision: 'appearance-1' };
          if (method === 'persona.read') return persona;
          if (method === 'studio.chat') return job = { job: crypto.randomUUID(), kind: 'chat', conversation: 'trial-1', status: 'completed',
            result: { text: '这是隔离试聊的模型回复。' + '中文长段落和链接边界 '.repeat(50), turns: 1, contextLimit: 24, enabledModules: params.modules.filter(row => row.enabled).map(row => row.title) } };
          if (method === 'studio.state') return job;
          if (method === 'studio.cancel') return { ...job, status: 'canceled' };
          throw new Error('Unsupported fixture route: ' + method);
        } };
    }, longModules);
    const url = 'http://127.0.0.1:' + server.address().port;
    await page.goto(url);
    assert.equal(await page.title(), '拾光微信聊天助手');
    await page.getByRole('button', { name: '人格', exact: true }).click();
    await page.getByLabel('人格提示词').fill('尚未保存的完整人格草稿');
    if (!contrastOnly) {
      const workspaces = page.getByRole('group', { name: '人格工作区', exact: true });
      assert.equal(await workspaces.count(), 1, 'Persona needs a distinct always-visible editor/trial workspace switch');
      assert.equal(await workspaces.getByRole('button', { name: '人格编辑', exact: true }).getAttribute('aria-pressed'), 'true');
    }
    await page.getByRole('button', { name: '人格试聊', exact: true }).click();
    const enabledCount = longModules ? 48 : 15;
    if (!contrastOnly) {
      assert.equal(await page.getByRole('heading', { name: '整套人格试聊', exact: true }).count(), 1, 'Trial must clearly identify the whole persona');
      const composition = page.locator('.persona-composition');
      assert.equal(await composition.count(), 1);
      assert.equal(await composition.locator('.persona-item.active, [aria-current], .persona-index').count(), 0, 'Composition must not imply a single selected or ranked module');
      assert.equal(await composition.getByRole('button').count(), 0, 'Trial composition is a list, not an editor selection');
      assert.equal(await page.getByRole('button', { name: '模块上移', exact: true }).count(), 0);
      await page.locator('.studio-trial').getByText('已启用' + enabledCount + '个模块', { exact: true }).waitFor();
      await page.getByLabel('搜索模块', { exact: true }).fill('基础人格');
      assert.equal(await composition.locator('.persona-item').count(), 1);
    }
    await page.getByLabel('试聊消息').fill('试聊入口测试');
    await page.getByRole('button', { name: '发送试聊', exact: true }).click();
    await page.locator('.studio-bubble.assistant').waitFor();
    if (!contrastOnly) {
      const submitted = await page.evaluate(() => window.personaEntryCalls.find(call => call.method === 'studio.chat').params.modules);
      assert.equal(submitted.length, longModules ? 48 : 16, 'Search must not narrow the submitted persona');
      assert.equal(submitted.filter(row => row.enabled).length, enabledCount);
      await page.getByLabel('搜索模块', { exact: true }).fill('');
    }
    const contrast = await page.locator('.studio-bubble.assistant').evaluate(element => {
      const style = getComputedStyle(element);
      const rgba = value => (value.match(/[\d.]+/g) || []).map(Number);
      const fg = rgba(style.color), bg = rgba(style.backgroundColor);
      const luminance = rgb => rgb.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
      return [0, 255].map(under => {
        const background = bg.slice(0, 3).map(v => v * (bg[3] ?? 1) + under * (1 - (bg[3] ?? 1)));
        const a = luminance(fg.slice(0, 3)), b = luminance(background);
        return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
      });
    });
    assert(contrast.every(ratio => ratio >= 4.5), 'Trial reply must remain readable over light and dark wallpaper: ' + contrast);
    if (!contrastOnly) {
      const geometry = [], layoutFailures = [];
      for (const percent of [100, 125, 150, 200]) {
        await page.setViewportSize({ width: Math.round(1488 * 100 / percent), height: Math.round(960 * 100 / percent) });
        const switcher = page.getByRole('group', { name: '人格工作区', exact: true });
        await switcher.scrollIntoViewIfNeeded();
        const bounds = await switcher.boundingBox();
        assert(bounds && bounds.x >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1, 'Workspace switch clipped at ' + percent);
        assert.equal(await switcher.getByRole('button', { name: '人格试聊', exact: true }).getAttribute('aria-pressed'), 'true');
        const inner = await page.evaluate(() => {
          const panel = document.querySelector('.studio-trial'), overview = document.querySelector('.studio-module-overview');
          const clear = panel.querySelector('.panel-title button'), panelRect = panel.getBoundingClientRect(), clearRect = clear.getBoundingClientRect();
          const names = [...document.querySelectorAll('.persona-item')].slice(0, 5).map(row => {
            const bounds = row.getBoundingClientRect(), text = row.querySelector('b').getBoundingClientRect();
            return { inside: text.bottom <= bounds.bottom + 1 && text.right <= bounds.right + 1, nameHeight: text.height, rowHeight: bounds.height };
          });
          return { overviewHeight: overview.getBoundingClientRect().height, panelWidth: panel.clientWidth, panelScroll: panel.scrollWidth,
            clearContained: clearRect.left >= panelRect.left && clearRect.right <= panelRect.right + 1, names };
        });
        if (inner.overviewHeight < 20) layoutFailures.push(percent + '%: module overview collapsed');
        if (inner.panelScroll > inner.panelWidth + 1 || !inner.clearContained) layoutFailures.push(percent + '%: trial panel/control spills horizontally');
        if (inner.names.some(row => !row.inside)) layoutFailures.push(percent + '%: module name crosses its row');
        await page.getByRole('button', { name: '发送试聊', exact: true }).scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'Horizontal overflow at ' + percent);
        geometry.push({ percent, bounds, inner });
        if (output) { fs.mkdirSync(output, { recursive: true }); await page.screenshot({ path: path.join(output, 'trial-' + percent + '.png') }); }
      }
      await page.getByRole('switch', { name: '基础人格启用', exact: true }).click();
      await page.locator('.studio-trial').getByText('已启用' + (enabledCount - 1) + '个模块', { exact: true }).waitFor();
      assert.equal(await page.locator('.studio-bubble').count(), 0, 'Changing composition starts a fresh trial');
      await page.getByRole('button', { name: '人格编辑', exact: true }).click();
      assert.equal(await page.locator('.persona-item.active').count(), 1, 'Only editing has a selected module');
      assert.equal(await page.getByLabel('人格提示词').inputValue(), '尚未保存的完整人格草稿');
      assert.equal(await page.evaluate(() => window.personaEntryCalls.filter(call => call.method === 'studio.chat')[0].params.modules[0].text), '尚未保存的完整人格草稿');
      assert.equal(await page.evaluate(() => window.personaEntryCalls.filter(call => /persona.save|queue_outbound|sendSummary/.test(call.method)).length), 0);
      // A non-first editor selection is retained but never presented as a trial selection.
      await page.locator('.persona-item > button').nth(1).click();
      const selectedName = await page.locator('.persona-item.active b').innerText();
      await page.getByRole('button', { name: '人格试聊', exact: true }).click();
      assert.equal(await page.locator('.persona-composition .active, .persona-composition [aria-current]').count(), 0);
      await page.getByRole('button', { name: '人格编辑', exact: true }).click();
      assert.equal(await page.locator('.persona-item.active b').innerText(), selectedName);
      if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ passed: !layoutFailures.length, url, contrast, geometry, layoutFailures, longModules, errors }, null, 2));
      assert.deepEqual(layoutFailures, [], 'Enabled overview and controls/names must remain inside their actual panels');
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, contrast, contrastOnly, errors }));
  } finally { if (browser) await browser.close(); server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
