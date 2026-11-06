"""Recipient selection must be explicit, account-scoped and keep pause state."""
from dataclasses import asdict, replace
from types import SimpleNamespace
import threading

from PIL import Image
import pytest

from xiaomao import contacts, selection, windows
from xiaomao.config import Settings
from xiaomao.contacts import Contact
from xiaomao.vision import TextLine, header_matches
from test_vision import binding


ROOM = Contact('fixture-room@chatroom', 'fixture-account', '打印交流', 'group')
PEER = Contact('fixture-peer', 'fixture-account', '新朋友', 'private', '原昵称', 'friend_alias')


def configured(root):
    return Settings(root, group=ROOM.name, group_names=(ROOM.name,),
                    conversation_targets={ROOM.name: ROOM.binding}, calibration=asdict(binding()))


def test_same_title_in_full_directory_reports_duplicate_instead_of_bad_crop():
    rows = [PEER, replace(PEER, id='another-peer', nickname='另一昵称')]
    with pytest.raises(ValueError, match='2.*同名.*唯一备注'):
        contacts.resolve_title_contact(rows, PEER.name, 'main')


def test_private_not_in_recent_list_resolves_from_full_directory():
    rows = [ROOM, *[replace(PEER, id=f'peer-{i}', name=f'目录好友{i}', nickname=f'昵称{i}', alias=f'alias{i}')
                   for i in range(60)], PEER]
    assert contacts.resolve_title_contact(rows, PEER.name, 'main').binding == PEER.binding
    assert contacts.search_directory(rows, PEER.nickname) == [PEER]
    assert contacts.search_directory(rows, PEER.alias) == [PEER]


def test_nickname_is_searchable_but_cannot_silently_override_different_remark():
    with pytest.raises(ValueError, match='备注.*刷新联系人'):
        contacts.resolve_title_contact([PEER], PEER.nickname, 'main')
    with pytest.raises(ValueError, match='刷新联系人'):
        contacts.resolve_title_contact([PEER], '新朋', 'main')


def test_group_member_count_cannot_impersonate_a_private_contact():
    private = replace(PEER, name=ROOM.name+'(8)')
    with pytest.raises(ValueError, match='同名|个人.*群'):
        contacts.resolve_title_contact([ROOM, private], private.name, 'main')


def test_visible_conflicting_emoji_is_not_treated_as_omitted_emoji():
    b = replace(binding(), target_group='拍照📸', target_kind='private', ocr_title='拍照')
    assert header_matches('拍照', b)
    assert header_matches('拍照📸', b)
    assert not header_matches('拍照🎨', b)


def test_explicit_process_directory_is_still_pinned_to_saved_account(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    from xiaomao import database
    class Hub:
        def __init__(self, *_args, **_kwargs): pass
        def directory(self): return [replace(PEER, account='foreign-account')]
        def close(self): pass
    monkeypatch.setattr(database, 'DatabaseHub', Hub)
    with pytest.raises(ValueError, match='账号.*改变'):
        selection.directory_for_settings(cfg, process_id=77)


def test_directory_refresh_recovers_restarted_process_only_for_original_account(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    from xiaomao import database
    calls = []
    class Hub:
        def __init__(self, _cfg, _cancelled, *, process_id=None):
            self.pid = process_id or cfg.binding.pid
            calls.append(self.pid)
        def directory(self):
            if self.pid == cfg.binding.pid:
                raise database.DatabaseUnavailable('原窗口进程已退出')
            return [PEER]
        def close(self): pass
    monkeypatch.setattr(database, 'DatabaseHub', Hub)
    monkeypatch.setattr(windows, 'list_calibration_windows', lambda: [
        dict(pid=77, hwnd=22, kind='main'), dict(pid=77, hwnd=23, kind='detached')])
    assert selection.directory_for_settings(cfg) == [PEER]
    assert calls == [cfg.binding.pid, 77]
    assert cfg.binding.pid != 77


def test_directory_restart_cannot_return_another_logged_in_account(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    from xiaomao import database
    class Hub:
        def __init__(self, _cfg, _event, *, process_id): self.pid = process_id
        def directory(self):
            if self.pid == cfg.binding.pid: raise database.DatabaseUnavailable('原窗口进程已退出')
            return [replace(PEER, account='foreign-account')]
        def close(self): pass
    monkeypatch.setattr(database, 'DatabaseHub', Hub)
    monkeypatch.setattr(windows, 'list_calibration_windows', lambda: [dict(pid=77, kind='main')])
    with pytest.raises(ValueError, match='账号.*改变'):
        selection.directory_for_settings(cfg)


def test_directory_can_be_browsed_before_calibration_when_one_account_is_open(tmp_path, monkeypatch):
    cfg = Settings(tmp_path)
    from xiaomao import database
    class Hub:
        def __init__(self, _cfg, _event, *, process_id): assert process_id == 77
        def directory(self): return [ROOM, PEER]
        def close(self): pass
    monkeypatch.setattr(database, 'DatabaseHub', Hub)
    monkeypatch.setattr(windows, 'list_calibration_windows', lambda: [dict(pid=77, kind='main')])
    assert selection.directory_for_settings(cfg) == [ROOM, PEER]
    assert cfg.binding is None and not cfg.conversation_targets


def fake_review(monkeypatch, cfg, draft, rows, titles):
    iterator = iter(titles)
    pixels = Image.new('RGB', (draft.width, draft.height), '#ededed')
    info = dict(kind=draft.window_kind, width=draft.width, height=draft.height,
                title=PEER.name, hwnd=draft.hwnd, pid=draft.pid)
    monkeypatch.setattr(selection, 'directory_for_settings', lambda *a, **kw: rows)
    monkeypatch.setattr(windows, 'capture_calibration_window', lambda *a: (pixels, info))
    monkeypatch.setattr('xiaomao.vision.Vision', lambda: SimpleNamespace(
        ocr=lambda _: [TextLine(next(iterator), (10, 10, 130, 30), .99)]))
    return pixels


def test_observed_private_identity_requires_explicit_choice_and_preserves_other_binding(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    draft = replace(cfg.binding, target_group=ROOM.name, target_kind='group')
    fake_review(monkeypatch, cfg, draft, [ROOM, PEER], [PEER.name]*4)
    with pytest.raises(ValueError, match='当前要校准'):
        selection.identify_reviewed_window(cfg, draft, threading.Event())
    image, observed = selection.identify_reviewed_window(
        cfg, draft, threading.Event(), choose_observed=True)
    assert image.size == (draft.width, draft.height)
    assert observed.contact.binding == PEER.binding
    assert observed.calibration.target_group == PEER.name
    assert not observed.calibration.bottom_confirmed
    saved = selection.bind_reviewed_window(
        cfg, replace(observed.calibration, bottom_confirmed=True), observed.contact)
    assert saved.group == ROOM.name
    assert saved.conversation_targets[ROOM.name] == ROOM.binding
    assert saved.conversation_targets[PEER.name] == PEER.binding
    assert cfg.saved_groups == (ROOM.name,)


def test_captured_stable_id_cannot_be_relabelled_to_another_same_named_contact(tmp_path):
    cfg = configured(tmp_path)
    draft = replace(cfg.binding, target_group=PEER.name, target_kind='private', ocr_title=PEER.name,
                    header_label=PEER.name, bottom_confirmed=True)
    draft._capture_contact = PEER
    with pytest.raises(ValueError, match='身份.*改变|身份.*不一致'):
        selection.bind_reviewed_window(cfg, draft, replace(PEER, id='different-person'))


def test_native_detached_caption_must_corroborate_the_observed_private_contact(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    draft = replace(cfg.binding, target_group=PEER.name, target_kind='private', ocr_title=PEER.name,
                    header_label=PEER.name, window_kind='detached')
    pixels = fake_review(monkeypatch, cfg, draft, [ROOM, PEER], [PEER.name]*2)
    info = dict(kind='detached', width=draft.width, height=draft.height, title='另一个人')
    monkeypatch.setattr(windows, 'capture_calibration_window', lambda *a: (pixels, info))
    with pytest.raises(ValueError, match='独立窗口标题'):
        selection.identify_reviewed_window(cfg, draft, threading.Event())


def test_two_reads_cannot_change_stable_id_even_when_visible_title_stays_the_same(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    draft = replace(cfg.binding, target_group=PEER.name, target_kind='private', ocr_title=PEER.name,
                    header_label=PEER.name)
    fake_review(monkeypatch, cfg, draft, [ROOM, PEER], [PEER.name]*2)
    directories = iter([[ROOM, PEER], [ROOM, replace(PEER, id='different-person')]])
    monkeypatch.setattr(selection, 'directory_for_settings', lambda *a, **kw: next(directories))
    with pytest.raises(ValueError, match='身份不同'):
        selection.identify_reviewed_window(cfg, draft, threading.Event())


@pytest.mark.parametrize('mode', ['paused', 'observe', 'auto', 'stopped'])
def test_selection_keeps_original_runtime_mode_and_launch_preference(tmp_path, monkeypatch, mode):
    cfg = configured(tmp_path)
    cfg.save()
    monkeypatch.setattr(selection, 'load_directory', lambda *a, **kw: [ROOM, PEER])
    starts = []
    class Client:
        def status(self): return {'running': mode != 'stopped', 'mode': mode}
        def stop(self): pass
        def wait_stopped(self): pass
        def start(self, initial_mode=None):
            starts.append(initial_mode)
            selected = Settings.load(tmp_path)
            return dict(group=selected.group, group_names=list(selected.allowed_groups),
                        reply_mode=selected.reply_mode, mode=initial_mode)
    result = selection.switch_group(tmp_path, PEER.name, Client(), binding=cfg.binding, contact=PEER)
    assert starts == ([] if mode == 'stopped' else [mode])
    assert result['mode'] == mode
    assert Settings.load(tmp_path).auto_on_launch == cfg.auto_on_launch


def test_selecting_paused_contact_from_auto_resident_keeps_its_saved_pause(tmp_path, monkeypatch):
    from xiaomao.__main__ import apply_initial_mode
    from xiaomao.store import StateStore
    from test_window_routing import screen_resident
    cfg = replace(configured(tmp_path), group_names=(ROOM.name,PEER.name),
                  conversation_targets={ROOM.name:ROOM.binding, PEER.name:PEER.binding},
                  monitor_all_groups=True, auto_on_launch=True, model_base_url='http://localhost:8000')
    cfg = cfg.with_conversation_pause(PEER.name,True)
    cfg.save()
    monkeypatch.setattr(selection,'load_directory',lambda *a,**kw:[ROOM,PEER])
    store = StateStore(cfg.runtime)
    residents = [screen_resident(cfg,store)]
    starts = []
    class Client:
        def status(self): return {'running':True,**residents[-1].status()}
        def stop(self): return residents[-1].stop()
        def wait_stopped(self): pass
        def start(self,initial_mode=None):
            starts.append(initial_mode)
            restored = screen_resident(Settings.load(tmp_path),store)
            residents.append(restored)
            apply_initial_mode(restored,store,initial_mode)
            return {'running':True,**restored.status()}
        def pause(self): pytest.fail('a saved recipient pause must not trigger a global pause failure')
    try:
        assert residents[0].status()['mode']=='auto'
        assert [engine.mode for engine in residents[0].engines]==['auto','paused']
        result = selection.switch_group(tmp_path,PEER.name,Client(),binding=cfg.binding,contact=PEER)
        assert starts==['auto'] and result['mode']=='paused'
        assert [engine.mode for engine in residents[-1].engines]==['paused']
        saved = Settings.load(tmp_path)
        assert saved.is_conversation_paused(PEER.name)
        assert saved.paused_conversations==cfg.paused_conversations
        assert all(not engine.desktop.events for resident in residents for engine in resident.engines)
    finally:
        for resident in residents:
            for engine in resident.engines:
                if engine.companion: engine.companion.close()
        store.close()


def test_editor_uses_observed_target_only_after_deliberate_button_click(tmp_path, native_root, monkeypatch):
    from xiaomao.calibration_ui import CalibrationDialog
    cfg = configured(tmp_path)
    original = cfg.binding
    callbacks, saved = [], []
    panel = SimpleNamespace(root=native_root, settings=cfg,
        work=lambda action, done, failed, **kw: callbacks.append((action, done, failed)),
        save_calibration=lambda *args: saved.append(args))
    observed_binding = replace(original, target_group=PEER.name, target_kind='private',
                               header_label=PEER.name, ocr_title=PEER.name, bottom_confirmed=False)
    observed_binding._capture_contact = PEER
    def identify(_cfg, draft, cancelled, *, choose_observed=False):
        assert choose_observed and draft.target_group == ROOM.name
        return Image.new('RGB', (800,600)), selection.CurrentGroup(PEER.name, observed_binding, PEER)
    monkeypatch.setattr(selection, 'identify_reviewed_window', identify)
    dialog = CalibrationDialog(panel, Image.new('RGB', (800,600)), original)
    try:
        assert dialog.calibration.target_group == ROOM.name and not callbacks
        dialog.use_observed_button.invoke()
        assert dialog.calibration.target_group == ROOM.name
        action, done, failed = callbacks.pop()
        done(action())
        assert dialog.calibration.target_group == PEER.name
        assert PEER.name in dialog.target_label.cget('text') and not dialog.confirmed.get()
        assert panel.settings == cfg and original.target_group == ROOM.name
        dialog.confirmed.set(True)
        dialog.save()
        assert saved[0][0].target_group == PEER.name
        assert saved[0][0]._capture_contact.binding == PEER.binding
    finally:
        dialog.close()


def test_editor_shows_observed_and_selected_targets_without_silently_switching(tmp_path, native_root, monkeypatch):
    from xiaomao.calibration_ui import CalibrationDialog
    cfg = configured(tmp_path)
    callbacks = []
    panel = SimpleNamespace(root=native_root, settings=cfg,
        work=lambda action, done, failed, **kw: callbacks.append((action, done, failed)))
    def identify(*_args, **_kwargs):
        raise selection.CalibrationIdentityError('当前窗口与所选对象不同，请明确选择', PEER.name, 'private')
    monkeypatch.setattr(selection, 'identify_reviewed_window', identify)
    dialog = CalibrationDialog(panel, Image.new('RGB', (800,600)), cfg.binding)
    try:
        dialog.recognize_target()
        action, done, _failed = callbacks.pop()
        done(action())
        assert PEER.name in dialog.observed_target.get()
        assert ROOM.name in dialog.chosen_target.get()
        assert dialog.calibration.target_group == ROOM.name
        assert '明确选择' in dialog.message.get() and not dialog.confirmed.get()
    finally:
        dialog.close()


def test_calibration_target_choice_and_save_buttons_fit_narrow_dialog(tmp_path, native_root):
    from xiaomao.calibration_ui import CalibrationDialog
    cfg = configured(tmp_path)
    panel = SimpleNamespace(root=native_root, settings=cfg)
    dialog = CalibrationDialog(panel, Image.new('RGB', (800,600)), cfg.binding)
    try:
        dialog.window.transient('')
        dialog.window.geometry('510x650')
        dialog.window.deiconify()
        dialog.window.update_idletasks()
        dialog.window.update()
        for control in (dialog.use_observed_button, dialog.save_button):
            assert control.winfo_width() >= control.winfo_reqwidth(), (dialog.window.winfo_width(),
                dialog.window.winfo_height(),control.master.winfo_width(),control.winfo_ismapped())
            assert control.winfo_rootx()+control.winfo_width() <= dialog.window.winfo_rootx()+dialog.window.winfo_width()
    finally:
        dialog.close()
