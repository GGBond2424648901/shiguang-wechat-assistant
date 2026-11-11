// Native preload + main-process close guard + real DesktopService/SQLite.
// Uses an isolated synthetic profile; never starts a resident or sends messages.
const { _electron } = require('playwright');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const project = path.resolve(__dirname, '../..');
const python = process.env.SHIGUANG_PYTHON || 'python';

(async () => {
  const output = process.env.SG_OPERATIONS_OUTPUT || await fs.mkdtemp(path.join(os.tmpdir(), 'shiguang-operations-'));
  await fs.mkdir(output, { recursive: true });
  const profile = await fs.mkdtemp(path.join(output, 'profile-'));
  const fixture = spawnSync(python, ['-c', 'from pathlib import Path; import sys; from scripts.qa_ui_baseline import prepare_profile; prepare_profile(Path(sys.argv[1]),count=2)', profile],
    { cwd: project, windowsHide: true, encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } });
  assert.equal(fixture.status, 0, fixture.stderr);
  const env = { ...process.env, SHIGUANG_PYTHON: python }; delete env.ELECTRON_RUN_AS_NODE;
  let electron;
  const errors = [], checks = [];
  async function open(readOnly) {
    electron = await _electron.launch({ args: [path.join(project, 'desktop'), '--profile=' + profile, ...(readOnly ? [] : ['--writable'])], env, timeout: 45000 });
    const page = await electron.firstWindow();
    page.on('pageerror', error => errors.push(error.message));
    await page.getByRole('button', { name: '任务中心', exact: true }).click();
    await page.getByRole('heading', { name: '未来计划', exact: true }).waitFor();
    await page.getByText('后台尚未连接；当前仅显示已保存计划。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '用量统计', exact: true }).click();
    await page.getByRole('spinbutton', { name: '每百万输入tokens单价', exact: true }).waitFor();
    return page;
  }
  const dirty = (page, expected) => page.waitForFunction(async value => (await window.shiguang.appInfo()).dirty === value, expected);
  try {
    let page = await open(false);
    const input = page.getByRole('spinbutton', { name: '每百万输入tokens单价', exact: true });
    await dirty(page, false);
    await input.fill('2'); await dirty(page, true);
    await page.getByRole('button', { name: '计划总览', exact: true }).click(); await dirty(page, true);
    await page.getByRole('button', { name: '执行记录', exact: true }).click(); await dirty(page, true);
    await page.getByRole('button', { name: '工作台', exact: true }).click(); await dirty(page, true);
    await page.getByRole('button', { name: '任务中心', exact: true }).click();
    await page.getByRole('button', { name: '用量统计', exact: true }).click();
    assert.equal(await input.inputValue(), '2'); await dirty(page, true);
    const opened = electron.waitForEvent('window');
    await page.getByRole('button', { name: '关闭助手', exact: true }).click();
    const prompt = await opened;
    await prompt.getByText('请先保存修改，或选择完全退出', { exact: true }).waitFor();
    assert.equal(await prompt.evaluate(async () => (await window.shiguang.appInfo()).dirty), true);
    assert.match(await prompt.evaluate(async () => {
      try { await window.shiguang.windowAction('exit'); return 'unexpected exit'; }
      catch (error) { return String(error); }
    }), /未保存/);
    await prompt.screenshot({ path: path.join(output, 'native-price-close-guard.png') });
    await prompt.getByRole('button', { name: '取消关闭', exact: true }).click();
    checks.push('native dirty survives task tabs, leaving page, and close dialog; exit denied');
    await input.fill('-1');
    await page.getByRole('button', { name: '保存估算单价', exact: true }).click();
    await page.getByText('请输入 0–100000 之间的有效单价', { exact: true }).waitFor(); await dirty(page, true);
    const before = await page.evaluate(() => window.shiguang.request('tasks.usage', { days: 7 }));
    assert.equal(before.pricing.inputPerMillion, 0);
    await input.fill('2');
    await page.getByRole('button', { name: '保存估算单价', exact: true }).click();
    await page.getByText('估算单价已保存', { exact: true }).waitFor(); await dirty(page, false);
    const saved = await page.evaluate(() => window.shiguang.request('tasks.usage', { days: 7 }));
    assert.equal(saved.pricing.inputPerMillion, 2);
    const savedClose = electron.waitForEvent('window');
    await page.getByRole('button', { name: '关闭助手', exact: true }).click();
    const savedPrompt = await savedClose;
    await savedPrompt.getByText('请选择关闭方式', { exact: true }).waitFor();
    assert.equal(await savedPrompt.evaluate(async () => (await window.shiguang.appInfo()).dirty), false);
    await savedPrompt.getByRole('button', { name: '取消关闭', exact: true }).click();
    checks.push('invalid value retains dirty and stored price; valid save clears native dirty and persists');
    await electron.close(); electron = undefined;
    page = await open(true);
    assert.equal(await page.getByRole('spinbutton', { name: '每百万输入tokens单价', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: '保存估算单价', exact: true }).isDisabled(), true);
    await dirty(page, false);
    assert.match(await page.evaluate(async () => {
      try { await window.shiguang.request('tasks.savePrices', { prices: { inputPerMillion: 8, outputPerMillion: 8 } }); return 'unexpected save'; }
      catch (error) { return String(error); }
    }), /只读/);
    assert.equal((await page.evaluate(() => window.shiguang.request('tasks.usage', { days: 7 }))).pricing.inputPerMillion, 2);
    checks.push('readonly UI and actual RPC reject price edits, native dirty false');
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ passed: true, checks, errors, profile, mode: 'actual Electron/IPC/DesktopService, offline isolated synthetic profile' }, null, 2));
    console.log(JSON.stringify({ passed: true, output, checks, errors }));
  } finally { if (electron) await electron.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
