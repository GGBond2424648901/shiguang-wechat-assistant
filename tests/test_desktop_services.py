"""Regression tests for the desktop boundary shared by the renderer and local worker."""

import base64
from dataclasses import replace
import io
import json
import sys

import pytest
from PIL import Image

from xiaomao.config import Settings
from test_stickers import library


class FixtureClient:
    def __init__(self):
        self.calls = []

    def status(self):
        return {
            'mode': 'auto', 'group': '晨光群', 'group_names': ['晨光群', '小禾'],
            'account_scope': 'account-a', 'account_display_name': '示例微信', 'counts': {'verified': 4},
            'groups': [{'name': '晨光群', 'mode': 'auto'}, {'name': '小禾', 'mode': 'paused'}],
        }

    def pending(self):
        return {'tasks': [
            {'id': 'a', 'group': '晨光群', 'messages': ['群里的消息'], 'reply': '回复 A', 'state': 'generated'},
            {'id': 'b', 'group': '小禾', 'messages': ['私聊消息'], 'reply': '回复 B', 'state': 'unknown'},
        ]}

    def pause_selected(self, group, scope):
        self.calls.append((group, scope))
        return self.status()


def configured(tmp_path):
    cfg = Settings(tmp_path, group='晨光群', group_names=('晨光群', '小禾'),
                   conversation_targets={
                       '晨光群': {'account': 'account-a', 'id': 'example@chatroom', 'kind': 'group'},
                       '小禾': {'account': 'account-a', 'id': 'example_friend', 'kind': 'private'},
                   })
    cfg.save()
    return cfg


def test_dashboard_filters_by_verified_identity_and_rejects_old_account(tmp_path):
    from xiaomao.desktop_service import DesktopService

    cfg = configured(tmp_path)
    worker = FixtureClient()
    ui = DesktopService(tmp_path, client=worker)
    state = ui.call('view.status', {})
    assert state['stats']['verified'] == 4
    assert 'account-a' not in json.dumps(state) and 'example_friend' not in json.dumps(state)
    friend = next(row['id'] for row in state['sessions'] if row['name'] == '小禾')
    queued = ui.call('view.queue', {'session': friend})
    assert [row['reply'] for row in queued['tasks']] == ['回复 B']
    assert queued['tasks'][0]['state'] == 'unknown'
    ui.call('run.selected', {'session': friend, 'action': 'pause'})
    assert worker.calls == [('小禾', 'account-a/example_friend')]

    targets = {name: {**row, 'account': 'account-b'} for name, row in cfg.conversation_targets.items()}
    replace(cfg, conversation_targets=targets).save()
    with pytest.raises(ValueError, match='会话'):
        ui.call('run.selected', {'session': friend, 'action': 'pause'})
    assert len(worker.calls) == 1


def test_recovery_waits_for_the_requested_primary_to_be_live(tmp_path):
    from threading import RLock
    from types import SimpleNamespace

    from xiaomao.client import ControlClient, ControlUnavailable
    from xiaomao.control import ControlServer
    from xiaomao.desktop_service import DesktopService
    from xiaomao.resident import Resident

    before = configured(tmp_path)
    after = before.with_saved_conversations(('小禾', '晨光群'))
    after.save(expected=before)
    recovered = []
    worker = Resident.__new__(Resident)
    worker.lock = RLock()
    worker.engines = [
        SimpleNamespace(settings=before.for_group(name), reply_latest=lambda name=name: recovered.append(name))
        for name in before.saved_groups
    ]
    worker.status = lambda: {'group': worker.engines[0].settings.group, 'account_scope': 'account-a'}
    panel = DesktopService(tmp_path)
    params = {'session': after.conversation_pause_key(after.group)}

    with ControlServer(tmp_path, worker):
        with pytest.raises(ControlUnavailable, match='主会话'):
            panel.call('run.recover', params)
        assert recovered == []
        worker.engines.reverse()
        panel.call('run.recover', params)
        assert recovered == ['小禾']
        ControlClient(tmp_path).reply_latest()
        assert recovered == ['小禾', '小禾']


def test_settings_draft_scope_changes_when_primary_changes_on_the_same_account(tmp_path):
    from xiaomao.desktop_settings import DesktopSettings

    first = configured(tmp_path)
    editor = DesktopSettings(tmp_path, client=FixtureClient())
    original = editor.read()
    replace(first, min_interval=12).save(expected=first)
    paced = editor.read()
    assert original['revision'] != paced['revision']
    assert original['scopeRevision'] == paced['scopeRevision']
    current = Settings.load(tmp_path)
    current.with_saved_conversations(('小禾', '晨光群')).save(expected=current)
    assert editor.read()['scopeRevision'] != original['scopeRevision']


def test_read_only_bridge_keeps_writes_and_private_methods_closed(tmp_path):
    from xiaomao.desktop_service import DesktopService

    configured(tmp_path)
    worker = FixtureClient()
    ui = DesktopService(tmp_path, client=worker, read_only=True)
    friend = ui.call('view.status', {})['sessions'][-1]['id']
    with pytest.raises(ValueError, match='只读'):
        ui.call('run.selected', {'session': friend, 'action': 'pause'})
    with pytest.raises(ValueError, match='不支持'):
        ui.call('client.request', {'operation': 'stop'})
    assert worker.calls == []
    assert 'tkinter' not in sys.modules or not any(
        name.startswith('xiaomao.desktop') and hasattr(module, 'tk')
        for name, module in sys.modules.copy().items())


def test_sticker_query_projects_safe_data_and_gif_keeps_original_bytes(tmp_path):
    from xiaomao.desktop_service import DesktopService

    rows = library(tmp_path, 7)
    ui = DesktopService(tmp_path, read_only=True)
    page = ui.call('stickers.query', {'category': '菲比', 'limit': 2, 'offset': 1})
    assert page['total'] == 4
    assert len(page['items']) == 2 and 'path' not in page['items'][0]
    full = ui.call('stickers.image', {'id': rows[0]['id'], 'variant': 'original'})
    assert full['mime'] == 'image/gif'
    assert base64.b64decode(full['data']) == (tmp_path / rows[0]['path']).read_bytes()
    thumb = ui.call('stickers.image', {'id': rows[0]['id'], 'variant': 'thumbnail'})
    with Image.open(io.BytesIO(base64.b64decode(thumb['data']))) as frame:
        assert frame.format == 'PNG' and frame.n_frames == 1
    with pytest.raises(ValueError, match='素材'):
        ui.call('stickers.image', {'id': '../runtime/config.json', 'variant': 'original'})
    (tmp_path / rows[0]['path']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='素材'):
        ui.call('stickers.image', {'id': rows[0]['id'], 'variant': 'original'})


def test_import_validation_and_commit_preserve_gif_animation(tmp_path):
    from xiaomao.desktop_service import DesktopService
    from xiaomao.sticker_import import StickerShelf

    rows = library(tmp_path, 1)
    path = tmp_path / rows[0]['path']
    ui = DesktopService(tmp_path)
    staged = ui.call('imports.pick', {'paths': [str(path)]})
    row = staged['rows'][0]
    assert row['id'] != str(path) and row['frames'] == 2
    original = ui.call('imports.preview', {'id': row['id'], 'variant': 'original'})
    assert original['mime'] == 'image/gif' and base64.b64decode(original['data']) == path.read_bytes()
    thumbnail = ui.call('imports.preview', {'id': row['id']})
    assert thumbnail['mime'] == 'image/png'
    assert not StickerShelf(tmp_path).snapshot()[0]
    result = ui.call('imports.commit', {'selected': {row['id']: {
        'category': '菲比', 'meaning': '验收修改过的用途', 'tags': ['开心'], 'source': '',
    }}})
    assert result['saved'] == 1
    record = StickerShelf(tmp_path).snapshot()[0][0]
    assert record['meaning'] == '验收修改过的用途' and record['frames'] == 2
    assert (StickerShelf(tmp_path).files / (record['digest'] + '.gif')).read_bytes() == path.read_bytes()


def test_appearance_is_profile_local_revision_checked_and_keeps_chat_settings(tmp_path):
    from xiaomao.desktop_service import DesktopService

    configured(tmp_path)
    config = tmp_path / 'runtime/config.json'
    original = config.read_bytes()
    ui = DesktopService(tmp_path)
    first = ui.call('appearance.read', {})
    assert first['transparency'] == 21 and first['reduceMotion'] is False
    saved = ui.call('appearance.save', {'transparency': 37, 'reduceMotion': True, 'expected': first['revision']})
    assert DesktopService(tmp_path).call('appearance.read', {}) == saved
    assert config.read_bytes() == original
    with pytest.raises(ValueError, match='更新'):
        ui.call('appearance.save', {'transparency': 20, 'reduceMotion': False, 'expected': first['revision']})
    with pytest.raises(ValueError, match='透明度'):
        ui.call('appearance.save', {'transparency': 99, 'reduceMotion': False, 'expected': saved['revision']})


def test_modular_persona_round_trip_keeps_all_text_and_disabled_modules(tmp_path):
    from xiaomao.desktop_service import DesktopService
    from xiaomao.persona_modules import PersonaModule, load_modules, save_modules

    original = ('# 模块原文\n' + '这是一段完整的长人格正文。\n' * 3000)
    save_modules(tmp_path, (PersonaModule('base', '正文', original), PersonaModule('notes', '备用', '备用规则', False)),
                 title='大肥鱼', expected_revision=None)
    service = DesktopService(tmp_path)
    snapshot = service.call('persona.read', {})
    assert snapshot['modules'][0]['text'] == original
    assert not snapshot['modules'][1]['enabled']
    rows = [snapshot['modules'][1], {**snapshot['modules'][0], 'text': original + '新增结尾。'}]
    result = service.call('persona.save', {'expected': snapshot['revision'], 'title': snapshot['title'], 'modules': rows})
    assert [row.id for row in load_modules(tmp_path).modules] == ['notes', 'base']
    assert result['modules'][1]['text'] == original + '新增结尾。'
    with pytest.raises(ValueError, match='更新'):
        service.call('persona.save', {'expected': snapshot['revision'], 'title': snapshot['title'], 'modules': rows})

def test_desktop_sessions_save_does_not_unpause_existing_conversations(tmp_path, monkeypatch):
    from xiaomao.config import Settings
    from xiaomao.contacts import Contact
    from xiaomao.desktop_service import DesktopService
    from xiaomao import selection, windows

    group = Contact('group@chatroom', 'account', '摄影群', 'group')
    contact = Contact('example', 'account', '小禾', 'private')
    settings = Settings(tmp_path, group=group.name, conversation_targets={group.name: group.binding})
    settings.with_conversation_pause(group.name, True).save()
    monkeypatch.setattr(selection, 'load_directory', lambda *a: [group, contact])
    monkeypatch.setattr(windows, 'worker_scope', lambda *a: None)
    service = DesktopService(tmp_path)
    original = service.call('sessions.read', {})
    directory = service.call('sessions.directory', {'query': '小禾'})
    assert directory['items'][0]['kind'] == 'private'
    with pytest.raises(ValueError, match='更新'):
        service.call('sessions.save', {'expected': 'stale', 'order': [original['items'][0]['id']],
                                      'monitorAll': True})
    changed = service.call('sessions.save', {'expected': original['revision'], 'monitorAll': True,
                           'order': [original['items'][0]['id'], directory['items'][0]['id']]})
    assert [row['name'] for row in changed['items']] == ['摄影群', '小禾']
    assert Settings.load(tmp_path).is_conversation_paused(group.name)
    assert Settings.load(tmp_path).is_conversation_paused(contact.name)
    private = changed['items'][1]
    service.call('sessions.rules', {'expected': changed['revision'], 'session': private['id'],
                                   'replyMode': 'all', 'stickerFrequency': 'expressive'})
    assert Settings.load(tmp_path).for_group(contact.name).reply_mode == 'all'
    assert Settings.load(tmp_path).for_group(group.name).reply_mode == settings.reply_mode


def test_adding_a_paused_conversation_does_not_interrupt_a_pending_reply(tmp_path, monkeypatch):
    from xiaomao import selection
    from xiaomao.contacts import Contact
    from xiaomao.desktop_service import DesktopService

    cfg = configured(tmp_path)
    existing = [Contact(row['id'], row['account'], name, row['kind'])
                for name, row in cfg.conversation_targets.items()]
    added = Contact('new-contact', 'account-a', '小岚', 'private')
    monkeypatch.setattr(selection, 'load_directory', lambda *_: [*existing, added])
    worker = FixtureClient()
    status = worker.status()
    worker.status = lambda: {**status, 'pending_count': 1, 'window_reload_pending': False}
    worker.reload_windows = lambda: worker.calls.append('reload')
    service = DesktopService(tmp_path, client=worker)
    before = service.call('sessions.read', {})
    directory = service.call('sessions.directory', {'query': added.name})
    order = [row['id'] for row in before['items']] + [directory['items'][0]['id']]

    updated = service.call('sessions.save', {'expected': before['revision'], 'order': order, 'monitorAll': True})

    assert worker.calls == ['reload']
    assert updated['items'][-1]['name'] == added.name and updated['items'][-1]['paused']
    assert Settings.load(tmp_path).group == cfg.group


def test_first_real_contact_replaces_the_factory_placeholder(tmp_path, monkeypatch):
    from xiaomao import selection, windows
    from xiaomao.contacts import Contact
    from xiaomao.desktop_service import DesktopService

    contact = Contact('real-shaped-peer', 'account-a', '小岚', 'private')
    monkeypatch.setattr(selection, 'load_directory', lambda *_: [contact])
    monkeypatch.setattr(windows, 'worker_scope', lambda *_: None)
    service = DesktopService(tmp_path)
    before = service.call('sessions.read', {})
    assert before['items'][0]['placeholder']
    directory = service.call('sessions.directory', {})
    after = service.call('sessions.save', {'expected': before['revision'], 'monitorAll': False,
                        'order': [before['items'][0]['id'], directory['items'][0]['id']]})
    assert len(after['items']) == 1
    assert after['items'][0]['name'] == contact.name and after['items'][0]['paused']
    assert Settings.load(tmp_path).conversation_targets[contact.name] == contact.binding


def test_companion_reads_do_not_set_permissions_on_an_existing_profile(tmp_path, monkeypatch):
    from xiaomao import companion_store, memory
    from xiaomao.desktop_service import DesktopService

    cfg = configured(tmp_path)
    memory.ConversationMemory(cfg.runtime, cfg.memory_scope)
    def forbid_permissions_change(_):
        pytest.fail('read-only access must leave the original directory permissions untouched')
    monkeypatch.setattr(companion_store, 'private_dir', forbid_permissions_change)
    monkeypatch.setattr(memory, 'private_dir', forbid_permissions_change)
    service = DesktopService(tmp_path, read_only=True)
    params = {'session': cfg.conversation_pause_key(cfg.group)}
    assert service.call('companion.read', params)['preferences']['daily_max'] == 2
    assert service.call('companion.memory', params)['messages'] == 0


def test_desktop_calibration_cancels_lease_and_rejects_stale_save(tmp_path, monkeypatch):
    from dataclasses import replace
    from PIL import Image
    from xiaomao import selection, windows
    from xiaomao.config import Calibration, Settings
    from xiaomao.contacts import Contact
    from xiaomao.desktop_service import DesktopService

    group = Contact('example@chatroom', 'account', '摄影群', 'group')
    cfg = Settings(tmp_path, group=group.name, conversation_targets={group.name: group.binding})
    cfg.save()
    candidate = {'hwnd': 1, 'pid': 2, 'title': group.name, 'minimized': False, 'kind': 'detached'}
    regions = {'header': [.1, .1, .9, .2], 'messages': [.1, .21, .9, .69],
               'input': [.1, .7, .9, .89], 'send': [.7, .9, .9, .98]}
    draft = Calibration(1, 2, 600, 510, group.name, regions, target_group=group.name, window_kind='detached')
    image = Image.new('RGB', (600, 510), 'white')
    closed = []

    class Lease:
        def __init__(self, handle):
            assert handle == candidate['hwnd']

        def close(self):
            closed.append(True)

    monkeypatch.setattr(windows, 'CalibrationWindowLease', Lease)
    monkeypatch.setattr(windows, 'list_calibration_windows', lambda: [candidate])
    monkeypatch.setattr(windows, 'activate_calibration_window', lambda *_: None)
    monkeypatch.setattr(selection, 'preview_window', lambda *a: (image, draft))
    monkeypatch.setattr(selection, 'identify_reviewed_window',
                        lambda *a, **kw: (image, selection.CurrentGroup(group.name, draft, group)))
    service = DesktopService(tmp_path)
    session = cfg.conversation_pause_key(group.name)
    rows = service.call('calibration.discover', {'session': session})
    assert rows['suggested'] == rows['windows'][0]['id']
    assert 'hwnd' not in rows['windows'][0]
    capture = service.call('calibration.capture', {'window': rows['suggested'], 'session': session})
    assert capture['image'].startswith('data:image/jpeg;base64,')
    with pytest.raises(ValueError, match='先确认'):
        service.call('calibration.save', {'token': capture['token'], 'regions': regions, 'confirmed': False})
    replace(cfg, reply_mode='mentions').save(expected=cfg)
    with pytest.raises(ValueError, match='设置已变化'):
        service.call('calibration.save', {'token': capture['token'], 'regions': regions, 'confirmed': True})
    assert not Settings.load(tmp_path).binding_for(group.name)
    service.call('calibration.cancel', {'token': capture['token']})
    assert closed == [True]


def test_desktop_companion_checks_scope_and_only_requests_preview(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.desktop_service import DesktopService

    group, private = '摄影群', '小禾'
    cfg = Settings(tmp_path, group=group, group_names=(group, private), conversation_targets={
        group: {'account': 'example', 'id': 'demo@chatroom', 'kind': 'group'},
        private: {'account': 'example', 'id': 'demo', 'kind': 'private'},
    })
    cfg.save()
    calls = []

    class Resident:
        def status(self):
            return {'account_scope': 'example', 'group_names': [group, private], 'group': group,
                    'groups': [{'name': group, 'companion': {'preview': '群聊预览'}},
                               {'name': private, 'companion': {'preview': '私聊预览'}}]}

        def companion_preview(self, **target):
            calls.append(target)

    service = DesktopService(tmp_path, client=Resident())
    target = cfg.conversation_pause_key(private)
    assert service.call('companion.state', {'session': target})['preview'] == '私聊预览'
    assert not service.call('companion.read', {'session': target})['preferences']['enabled']
    assert not (tmp_path / 'runtime/fingerprint.key').exists()
    with pytest.raises(ValueError, match='监听范围'):
        service.call('companion.command', {'session': 'other', 'action': 'preview'})
    service.call('companion.command', {'session': target, 'action': 'preview'})
    assert calls == [{'group': private, 'scope': 'example/demo'}]
