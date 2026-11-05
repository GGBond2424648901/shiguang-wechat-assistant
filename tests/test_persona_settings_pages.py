from types import SimpleNamespace
import tkinter as tk

import pytest

from xiaomao.config import Settings
from xiaomao.persona import load_persona,save_persona


@pytest.fixture
def panel(tmp_path,native_root):
    root=tk.Frame(native_root)
    p=SimpleNamespace(root=root,directory=tmp_path,settings=Settings(tmp_path),notice=tk.StringVar(root))
    p.work=lambda call,done,failed=None,**kw:done(call())
    for key in ('secret','secret_state','model_name','display_name'):
        setattr(p,key,tk.StringVar(root))
    for key in ('at_unique','auto_on_launch','split_replies','owner_tone'):
        setattr(p,key,tk.BooleanVar(root))
    p.test_model=p.calibrate=p.stop_and_exit=p.save_settings=lambda:None
    yield p
    root.destroy()


def test_persona_page_keeps_unsaved_sections_preview_and_default_reversible(panel):
    from xiaomao.persona_ui import PersonaPage
    raw='【表达风格】\n轻声说。\n【杂记】\n  保留缩进\n'
    save_persona(panel.directory,raw)
    page=PersonaPage(panel.root,panel)
    page.select_category('style')
    page.editor.delete('1.0','end');page.editor.insert('1.0','换一种写法。\n')
    page.select_category('relationship')
    page.editor.insert('1.0','只对已绑定的私聊使用昵称。')
    page.toggle_preview()
    assert '换一种写法。' in page.editor.get('1.0','end-1c')
    page.on_show();page.toggle_preview();page.save()
    saved=load_persona(panel.directory)
    assert '换一种写法。' in saved and '只对已绑定的私聊' in saved and '【杂记】\n  保留缩进\n' in saved
    page.restore_default()
    assert load_persona(panel.directory)==saved
    page.restore_default();page.toggle_preview()
    assert page.editor.get('1.0','end-1c')==saved


def test_masked_backup_editor_never_reads_back_saved_keys(panel):
    from xiaomao.credentials import CredentialStore
    from xiaomao.settings_ui import SettingsPage
    store=CredentialStore(panel.settings.runtime)
    store.set_key('sk-test-only-original')
    page=SettingsPage(panel.root,panel);page.on_show()
    assert page.key_entries[0].cget('show') and not page.key_entries[0].get()
    page.key_entries[0].insert(0,'sk-test-only-additional')
    page.refresh_scope();page.save_keys()
    assert store.count==2 and not page.key_entries[0].get()
    page.key_mode.set('replace')
    page.key_entries[0].insert(0,'sk-test-only-replacement')
    page.save_keys()
    assert store.get_keys()==('sk-test-only-replacement',)
    assert 'sk-' not in panel.notice.get()+panel.secret_state.get()


def test_invalid_persona_can_recover_only_after_restore_and_save(panel):
    from xiaomao.persona import DEFAULT
    from xiaomao.persona_ui import PersonaPage
    path=panel.directory/'config'/'persona.md';path.parent.mkdir()
    path.write_bytes(b'')
    page=PersonaPage(panel.root,panel)
    page.restore_default()
    assert path.read_bytes()==b''
    page.save()
    assert load_persona(panel.directory)==DEFAULT


def test_persona_marks_dirty_across_preview_and_clears_after_save(panel):
    from xiaomao.persona_ui import PersonaPage
    page=PersonaPage(panel.root,panel)
    assert page.draft_status.get()=='已保存'
    page.editor.insert('end','新草稿')
    panel.root.update()
    assert page.draft_status.get()=='有未保存修改'
    page.toggle_preview()
    assert '新草稿' in page.editor.get('1.0','end-1c')
    page.save()
    assert page.draft_status.get()=='已保存' and page.save_button.instate(('disabled',))


def test_clear_masked_input_does_not_clear_store(panel):
    from xiaomao.credentials import CredentialStore
    from xiaomao.settings_ui import SettingsPage
    store=CredentialStore(panel.settings.runtime);store.set_key('sk-test-saved')
    page=SettingsPage(panel.root,panel)
    page.key_entries[0].insert(0,'sk-test-draft')
    assert page.entry_status.get()=='1 条密钥输入待保存'
    page.clear_input.invoke()
    assert not page.key_entries[0].get() and page.clear_input.instate(('disabled',))
    assert store.get_keys()==('sk-test-saved',)


def test_calibration_undo_restores_geometry_and_assignment_without_persisting(panel):
    from copy import deepcopy
    from PIL import Image
    from xiaomao.calibration_ui import CalibrationDialog
    from xiaomao.config import Calibration
    from xiaomao.gui import default_regions
    calibration=Calibration(1,1,800,600,'示例会话',default_regions(),True,'示例会话','group','示例会话')
    original=deepcopy(calibration.regions)
    dialog=CalibrationDialog(panel,Image.new('RGB',(800,600),'white'),calibration)
    try:
        dialog.pick_regions['input'].current(0);dialog.assign('input')
        assert dialog.calibration.regions!=original
        dialog.undo_button.invoke()
        assert dialog.calibration.regions==original and calibration.regions==original
        assert dialog.pick_regions['input'].current()==2 and dialog.undo_button.instate(('disabled',))
        assert not dialog.confirmed.get()
    finally:dialog.close()
