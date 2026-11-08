/* Synchronize saved contacts into an open editor without losing its draft. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { _electron } = require('playwright');
const { fixture } = require('./check-desktop.cjs');
const project = path.resolve(__dirname, '../..');
const output = path.resolve(process.argv[2] || path.join(project, 'build/ui-audit/session-sync'));
const python = process.env.SHIGUANG_PYTHON || path.join(project, '.venv/Scripts/python.exe');

async function main() {
  await fs.mkdir(output, { recursive: true });
  const test = fixture(output, true);
  let app;
  try {
    await test.ready;
    const profile = path.join(output, 'profile');
    const adapter = path.join(output, 'adapter');
    await fs.mkdir(adapter, { recursive: true });
    await fs.copyFile(path.join(project, 'scripts/qa_desktop_adapter.py'), path.join(adapter, 'sitecustomize.py'));
    const env = { ...process.env, SHIGUANG_PYTHON: python };
    env.PYTHONPATH = [adapter, project, env.PYTHONPATH].filter(Boolean).join(path.delimiter);
    env.SHIGUANG_UI_FIXTURE_PROFILE = profile;
    delete env.ELECTRON_RUN_AS_NODE;
    app = await _electron.launch({ args: [path.join(project, 'desktop'), '--profile=' + profile, '--writable'], env });
    const page = await app.firstWindow();
    const click = name => page.getByRole('button', { name, exact: true }).click();
    await click('会话');
    await page.locator('.session-entry').nth(4).waitFor();
    await page.locator('.session-entry').filter({ hasText: '圆桌讨论组' }).click();
    await click('仅 @ 或点名');
    await page.getByText('有未保存的修改', { exact: true }).waitFor();
    execFileSync(python, ['-X', 'utf8', '-c', `
from pathlib import Path
from dataclasses import replace
from xiaomao.config import Settings
import sys
root=Path(sys.argv[1]);cfg=Settings.load(root)
name='新加入的监听对象'
updated=replace(cfg, group_names=(*cfg.saved_groups,name), monitor_all_groups=True,
 conversation_targets={**cfg.conversation_targets,name:{'id':'sync-new-peer','account':'ui-qa','kind':'private'}})
updated.with_conversation_pause(name,True).save(expected=cfg)
`, profile], { cwd: project, windowsHide: true });
    await click('工作台');
    await page.locator('.watch-row').filter({ hasText: '新加入的监听对象' }).waitFor({ timeout: 8000 });
    await click('会话');
    await page.locator('.session-entry').filter({ hasText: '新加入的监听对象' }).waitFor({ timeout: 8000 });
    assert.equal(await page.locator('.session-entry').count(), 6);
    assert.equal(await page.getByRole('button', { name: '仅 @ 或点名', exact: true }).getAttribute('aria-pressed'), 'true');
    await page.getByText('有未保存的修改', { exact: true }).waitFor();
    await click('撤销');
    assert.equal(await page.locator('.session-entry').count(), 6);
    assert.equal(await page.getByRole('button', { name: '保存监听范围', exact: true }).count(), 0);
    await click('添加会话');
    const yun = page.getByRole('checkbox', { name: '阿云', exact: true });
    await yun.check();
    execFileSync(python, ['-X', 'utf8', '-c', `
from pathlib import Path
from dataclasses import replace
from xiaomao.config import Settings
import sys
cfg=Settings.load(Path(sys.argv[1]));name='阿云'
updated=replace(cfg, group_names=(*cfg.saved_groups,name), conversation_targets={
 **cfg.conversation_targets,name:{'id':'demo_yun','account':'ui-qa','kind':'private'}})
updated.with_conversation_pause(name,True).save(expected=cfg)
`, profile], { cwd: project, windowsHide: true });
    await page.locator('.directory-selected').getByText('勾选左侧群聊或联系人', { exact: true }).waitFor({ timeout: 8000 });
    assert.equal(await page.locator('.directory-selected .muted').first().textContent(), '7 / 8');
    assert.equal(await page.locator('.directory-picked').getByText('阿云', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: /^加入监听列表/ }).isDisabled(), true);
    await click('取消');
    assert.equal(await page.locator('.session-entry').count(), 7);
    assert.equal(await page.getByRole('button', { name: '保存监听范围', exact: true }).count(), 0);
    await page.getByRole('combobox', { name: '默认回复方式', exact: true }).click();
    await page.getByRole('option', { name: '逐条回复', exact: true }).click();
    execFileSync(python, ['-X', 'utf8', '-c', `
from pathlib import Path
from dataclasses import replace
from xiaomao.config import Settings
import sys
root=Path(sys.argv[1]);cfg=Settings.load(root)
(root/'prior-account.json').write_bytes((root/'runtime/config.json').read_bytes())
replace(cfg,group='另一个账号的会话',group_names=(),conversation_targets={
 '另一个账号的会话':{'id':'other-peer','account':'ui-qa-other','kind':'private'}},
 calibration=None,window_bindings={},calibration_account='',paused_conversations=(),conversation_rules={}).save(expected=cfg)
`, profile], { cwd: project, windowsHide: true });
    await page.getByRole('heading', { name: '另一个账号的会话', exact: true }).waitFor({ timeout: 8000 });
    assert.equal(await page.getByRole('combobox', { name: '默认回复方式', exact: true }).textContent(), '自然参与');
    assert.equal(await page.evaluate(() => window.shiguang.appInfo().then(value => value.dirty)), true);
    execFileSync(python, ['-X', 'utf8', '-c', `
from pathlib import Path
import sys
root=Path(sys.argv[1]);(root/'runtime/config.json').write_bytes((root/'prior-account.json').read_bytes())
`, profile], { cwd: project, windowsHide: true });
    await page.locator('.session-entry').filter({ hasText: '新加入的监听对象' }).waitFor({ timeout: 8000 });
    assert.equal(await page.getByRole('combobox', { name: '默认回复方式', exact: true }).textContent(), '逐条回复');
    await click('撤销');
    await page.screenshot({ path: path.join(output, 'synced.png'), animations: 'disabled' });
    console.log(JSON.stringify({ syncedContacts: 7, draftPreserved: true, undoKeepsImportedContacts: true,
      directoryDeduplicated: true, accountDraftRestored: true, hiddenDraftProtected: true }));
  } finally {
    await app?.close();
    test.child.kill();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
