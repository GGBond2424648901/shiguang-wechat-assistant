from dataclasses import replace
from test_persona_settings_pages import panel
from test_persona_modules import kit
from xiaomao.persona_modules import load_modules
from xiaomao.persona import load_persona


def test_module_editor_keeps_cross_module_drafts_preview_and_applies_one_set(panel):
    from xiaomao.persona_modules_ui import PersonaModulesPage
    kit(panel.directory)
    page=PersonaModulesPage(panel.root,panel)
    page.select_module('v3')
    page.editor.insert('end','新增语言细节')
    page.select_module('v5')
    page.editor.insert('end','新增观点细节')
    page.toggle_preview()
    assert '新增语言细节' in page.editor.get('1.0','end-1c')
    assert '新增观点细节' in page.editor.get('1.0','end-1c')
    page.on_show()
    assert page.dirty() and not page.can_leave()
    page.save()
    assert all(t in load_persona(panel.directory) for t in ('新增语言细节','新增观点细节'))
    assert not page.dirty() and page.can_leave()
    assert '已应用' in page.state_text.get() and '下一次' in panel.notice.get()


def test_module_editor_disables_only_selected_module_and_imports_without_discarding_draft(panel,monkeypatch):
    from xiaomao.persona_modules_ui import PersonaModulesPage
    kit(panel.directory);page=PersonaModulesPage(panel.root,panel)
    page.select_module('v5');page.enabled.set(False);page.name.set('我的主动风格')
    path=panel.directory/'新模块.md';path.write_bytes('# 我的兴趣\r\n完整内容不改\r\n'.encode())
    monkeypatch.setattr('xiaomao.persona_modules_ui.filedialog.askopenfilenames',lambda **k:(str(path),))
    page.import_files();page.save()
    state=load_modules(panel.directory)
    assert state.modules[0].enabled
    assert not state.modules[1].enabled and state.modules[1].title=='我的主动风格'
    assert state.modules[2].text==path.read_bytes().decode('utf-8')
    assert len(state.modules)==3


def test_editor_refresh_does_not_discard_unsaved_content_or_overwrite_external_save(panel):
    from xiaomao.persona_modules_ui import PersonaModulesPage
    from xiaomao.persona_modules import save_modules
    first=kit(panel.directory);page=PersonaModulesPage(panel.root,panel)
    page.editor.insert('end','本地草稿')
    save_modules(panel.directory,[replace(first.modules[0],text='外部更新'),first.modules[1]],
                 title=first.title,expected_revision=first.revision)
    page.on_show();page.save()
    assert '本地草稿' in page.editor.get('1.0','end-1c')
    assert '外部更新' in load_persona(panel.directory)
    assert '更新' in panel.notice.get()


def test_save_freezes_editor_until_atomic_apply_completes_and_cancel_restores_draft(panel):
    from xiaomao.persona_modules_ui import PersonaModulesPage
    original=kit(panel.directory);page=PersonaModulesPage(panel.root,panel)
    page.editor.insert('end','尚未保存')
    page.discard_changes()
    assert not page.dirty() and '尚未保存' not in page.editor.get('1.0','end-1c')
    page.editor.insert('end','需要保存')
    jobs=[];panel.work=lambda call,done,failed=None,**kw:jobs.append((call,done))
    page.save()
    assert str(page.editor.cget('state'))=='disabled'
    page.select_module('v5')
    assert page.selected=='v3'
    call,done=jobs.pop();done(call())
    assert str(page.editor.cget('state'))=='normal' and not page.dirty()


def test_export_import_bundle_preserves_disabled_modules_and_original_text(panel,monkeypatch):
    from xiaomao.persona_modules_ui import PersonaModulesPage
    kit(panel.directory);page=PersonaModulesPage(panel.root,panel)
    page.select_module('v5');page.enabled.set(False)
    path=panel.directory/'整套.json'
    monkeypatch.setattr('xiaomao.persona_modules_ui.filedialog.asksaveasfilename',lambda **k:str(path))
    page.export_file()
    monkeypatch.setattr('xiaomao.persona_modules_ui.filedialog.askopenfilenames',lambda **k:(str(path),))
    page.import_files()
    assert len(page.modules)==4
    assert [m.enabled for m in page.modules]==[True,False,True,False]
    assert page.modules[2].text==page.modules[0].text
    page.save()
    assert len(load_modules(panel.directory).modules)==4
