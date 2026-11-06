from dataclasses import replace
from types import MethodType, SimpleNamespace
import tkinter as tk

import pytest

from xiaomao.config import Settings
from xiaomao.credentials import CredentialStore
from xiaomao.gui import Panel
from xiaomao.settings_ui import SettingsPage


@pytest.fixture
def panel(tmp_path, native_root):
    root = tk.Frame(native_root)
    cfg = Settings(tmp_path)
    cfg.save()
    p = SimpleNamespace(root=root, directory=tmp_path, settings=cfg, jobs=[],
                        view=SimpleNamespace(page='settings', render=lambda: None))
    p.view.select = lambda name: setattr(p.view, 'page', name)
    p.view.pages = {'sessions': SimpleNamespace(pacing=SimpleNamespace(focus_set=lambda: None))}
    p.work = lambda action, done, failed=None, **kw: p.jobs.append((action, done, failed))
    for name in ('notice', 'secret_state', 'model_name', 'display_name'):
        setattr(p, name, tk.StringVar(root))
    for name in ('at_unique', 'auto_on_launch', 'split_replies', 'owner_tone'):
        setattr(p, name, tk.BooleanVar(root))
    p.test_model = p.calibrate = p.stop_and_exit = p.save_settings = lambda: None
    p.client = SimpleNamespace(status=lambda: {'mode': 'paused', 'can_stop_if_idle': True},
                              stop_if_idle=lambda: None, wait_stopped=lambda: None,
                              start=lambda **kw: {'mode': kw.get('initial_mode')})
    p.show_status = lambda status: None
    p.apply_pacing_settings = MethodType(Panel.apply_pacing_settings, p) if hasattr(Panel, 'apply_pacing_settings') else None
    yield p
    root.destroy()


def finish(panel):
    action, done, failed = panel.jobs.pop(0)
    try:
        result = action()
    except ValueError as error:
        failed(str(error))
    else:
        done(result)


def texts(widget):
    result = []
    if 'text' in widget.keys():
        result.append(str(widget.cget('text')))
    for child in widget.winfo_children():
        result.extend(texts(child))
    return result


def deletion_buttons(page):
    assert hasattr(page, 'key_delete_buttons'), 'Saved rows need individual delete actions'
    return page.key_delete_buttons


def test_saved_rows_show_only_number_and_last_four_and_cancel_preserves_keys(panel, monkeypatch):
    store = CredentialStore(panel.settings.runtime)
    keys = ('sk-test-ui-primary-AAAA', 'sk-test-ui-backup-BBBB')
    store.set_keys(keys)
    page = SettingsPage(panel.root, panel)
    buttons = deletion_buttons(page)
    rendered = '\n'.join(texts(page.key_list))
    assert '密钥 1' in rendered and '密钥 2' in rendered and 'AAAA' in rendered and 'BBBB' in rendered
    assert all(key not in rendered for key in keys)
    prompts = []
    def cancel(title, message, **kwargs):
        prompts.append(title + message)
        return False
    monkeypatch.setattr('xiaomao.settings_ui.messagebox.askokcancel', cancel)
    before = store.path.read_bytes()
    buttons[store.list_keys()[0].identifier].invoke()
    assert not panel.jobs and store.path.read_bytes() == before
    assert '已取消' in panel.notice.get()
    assert prompts and all(key not in prompts[0] for key in keys)


def test_delete_disables_actions_and_refreshes_to_protected_last_key(panel, monkeypatch):
    store = CredentialStore(panel.settings.runtime)
    store.set_keys(['sk-test-ui-primary-AAAA', 'sk-test-ui-backup-BBBB'])
    page = SettingsPage(panel.root, panel)
    monkeypatch.setattr('xiaomao.settings_ui.messagebox.askokcancel', lambda *a, **kw: True)
    deletion_buttons(page)[store.list_keys()[0].identifier].invoke()
    assert page.key_busy and all(button.instate(('disabled',)) for button in deletion_buttons(page).values())
    finish(panel)
    assert store.get_keys() == ('sk-test-ui-backup-BBBB',)
    assert page.key_count == 1 and '已删除' in panel.notice.get()
    assert all(button.instate(('disabled',)) for button in deletion_buttons(page).values())
    assert '至少保留' in '\n'.join(texts(page.model_card))
    assert not page.key_busy and not page.key_entries[0].get()


def test_appending_refreshes_saved_rows_and_reenables_deletion(panel):
    store = CredentialStore(panel.settings.runtime)
    store.set_key('sk-test-ui-primary-AAAA')
    page = SettingsPage(panel.root, panel)
    assert len(deletion_buttons(page)) == 1
    page.key_entries[0].insert(0, 'sk-test-ui-backup-BBBB')
    page.save_keys(); finish(panel)
    assert len(deletion_buttons(page)) == 2 and page.key_count == 2
    assert all(button.instate(('!disabled',)) for button in deletion_buttons(page).values())
    assert 'BBBB' in '\n'.join(texts(page.key_list))
    assert not page.key_entries[0].get()


def test_stale_delete_reports_change_and_refreshes_without_removing_another_key(panel, monkeypatch):
    store = CredentialStore(panel.settings.runtime)
    store.set_keys(['sk-test-ui-primary-AAAA', 'sk-test-ui-backup-BBBB'])
    page = SettingsPage(panel.root, panel)
    stale = store.list_keys()[1].identifier
    monkeypatch.setattr('xiaomao.settings_ui.messagebox.askokcancel', lambda *a, **kw: True)
    store.set_keys(['sk-test-ui-primary-AAAA', 'sk-test-ui-replaced-CCCC'])
    deletion_buttons(page)[stale].invoke(); finish(panel)
    assert store.get_keys() == ('sk-test-ui-primary-AAAA', 'sk-test-ui-replaced-CCCC')
    assert '列表已变化' in panel.notice.get() and not page.key_busy
    assert stale not in deletion_buttons(page)


def test_eight_saved_keys_have_bounded_scroll_area_and_keyboard_delete_actions(panel):
    store = CredentialStore(panel.settings.runtime)
    store.set_keys([f'sk-test-ui-number-{index:04}' for index in range(8)])
    page = SettingsPage(panel.root, panel)
    buttons = deletion_buttons(page)
    page.update_idletasks()
    assert len(buttons) == 8
    assert int(page.key_canvas.cget('height')) < page.key_list.winfo_reqheight()
    assert page.key_scroll.winfo_manager()
    assert all(str(button.cget('takefocus')) not in ('', '0') for button in buttons.values())


def open_editor(page):
    page.open_pacing()
    assert page.panel.view.page == 'settings', 'Pacing edits must stay in runtime settings'
    assert getattr(page, 'pacing_editor', None) is not None, 'Pacing numbers need an inline editor'
    return page.pacing_editor


def test_pacing_opens_direct_values_and_cancel_discards_changes(panel):
    panel.settings = replace(panel.settings, min_interval=17.5, pacing_jitter=2.5, minute_limit=4, hourly_limit=120)
    panel.settings.save()
    page = SettingsPage(panel.root, panel)
    editor = open_editor(page)
    assert [value.get() for value in editor.rate_values] == ['17.5', '2.5', '4', '120']
    editor.rate_values[0].set('23')
    editor.cancel_button.invoke()
    assert not panel.jobs and Settings.load(panel.directory).min_interval == 17.5
    assert page.pacing_editor is None and '已取消' in panel.notice.get()


def test_invalid_pacing_shows_local_error_and_retains_draft(panel):
    page = SettingsPage(panel.root, panel)
    editor = open_editor(page)
    editor.rate_values[0].set('2')
    editor.save_button.invoke()
    assert not panel.jobs and editor.rate_values[0].get() == '2'
    assert '3 至 3600' in editor.status.get()
    assert editor.save_button.instate(('!disabled',))


def test_pacing_save_disables_controls_and_updates_summary_after_persistence(panel):
    page = SettingsPage(panel.root, panel)
    editor = open_editor(page)
    for var, value in zip(editor.rate_values, ('17.5', '2.5', '4', '120')):
        var.set(value)
    editor.save_button.invoke()
    assert editor.save_button.instate(('disabled',)) and editor.cancel_button.instate(('disabled',))
    assert all(str(entry.cget('state')) == 'disabled' for entry in editor.entries)
    finish(panel)
    cfg = Settings.load(panel.directory)
    assert (cfg.min_interval, cfg.pacing_jitter, cfg.minute_limit, cfg.hourly_limit) == (17.5, 2.5, 4, 120)
    assert page.pacing_editor is None
    assert '4 条 / 分钟' in page.rate_text.get() and '120 条 / 小时' in page.rate_text.get()
    assert '已保存' in panel.notice.get()


def test_pacing_save_failure_keeps_editor_and_recovers_controls(panel):
    page = SettingsPage(panel.root, panel)
    editor = open_editor(page)
    panel.client.status = lambda: {'mode': 'auto', 'pending_count': 1}
    editor.rate_values[0].set('23')
    editor.save_button.invoke(); finish(panel)
    assert page.pacing_editor is editor and editor.rate_values[0].get() == '23'
    assert editor.save_button.instate(('!disabled',)) and editor.cancel_button.instate(('!disabled',))
    assert '待发回复' in editor.status.get()
    assert Settings.load(panel.directory).min_interval == 30
