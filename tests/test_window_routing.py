from dataclasses import asdict, replace
import json

import pytest

from xiaomao.config import Settings
from xiaomao.contacts import Contact
from test_vision import binding


ROOM = Contact('room@chatroom', 'account-a', '读书群', 'group')
PEER = Contact('peer', 'account-a', '小夏', 'private')
OTHER = Contact('other@chatroom', 'account-a', '摄影群', 'group')


def selected(root, *contacts):
    return Settings(root, group=contacts[0].name, group_names=tuple(c.name for c in contacts),
                    conversation_targets={c.name: c.binding for c in contacts},
                    model_base_url='http://localhost:8000', reply_mode='all', natural_pacing=False)


def window(contact, hwnd, kind='detached', pid=71):
    return replace(binding(), hwnd=hwnd, pid=pid, header_label=contact.name, target_group=contact.name,
                   target_kind=contact.kind, window_kind=kind, manual_regions=True)


def test_old_config_shares_main_window_until_individual_window_is_saved(tmp_path):
    cfg = selected(tmp_path, ROOM, PEER, OTHER)
    cfg.save()
    path = tmp_path/'runtime/config.json'
    old = json.loads(path.read_text('utf-8'))
    old.pop('window_bindings', None)
    raw = asdict(binding())
    raw.pop('window_kind', None)
    raw.pop('manual_regions', None)
    old['calibration'] = raw
    path.write_text(json.dumps(old), encoding='utf-8')

    cfg = Settings.load(tmp_path)
    assert cfg.for_group(PEER.name).binding.hwnd == 1
    assert cfg.for_group(PEER.name).binding.target_kind == 'private'
    cfg.with_window_binding(PEER.name, window(PEER, 22)).save()
    loaded = Settings.load(tmp_path).with_saved_conversations((PEER.name, OTHER.name, ROOM.name))
    assert loaded.allowed_groups == (PEER.name, OTHER.name, ROOM.name)
    assert [loaded.for_group(name).binding.hwnd for name in loaded.allowed_groups] == [22, 1, 1]
    assert loaded.binding.manual_regions and loaded.binding.window_kind == 'detached'


def test_detached_windows_stay_scoped_to_stable_accounts_and_recipients(tmp_path):
    cfg = selected(tmp_path, ROOM, PEER, OTHER)
    cfg = cfg.with_window_binding(ROOM.name, window(ROOM, 11))
    cfg = cfg.with_window_binding(PEER.name, window(PEER, 22))
    assert cfg.for_group(ROOM.name).binding.hwnd == 11
    assert cfg.for_group(PEER.name).binding.hwnd == 22
    assert cfg.for_group(OTHER.name).binding is None
    for change in ({'id': 'replacement@chatroom'}, {'account': 'account-b'}):
        replaced = replace(cfg, conversation_targets={
            **cfg.conversation_targets, ROOM.name: {**ROOM.binding, **change}})
        assert replaced.for_group(ROOM.name).binding is None
    removed = cfg.with_saved_conversations((PEER.name, OTHER.name))
    assert removed.select_conversation(ROOM).binding is None


def test_detached_window_needs_confirmed_identity_and_cannot_be_relabelled(tmp_path):
    cfg = selected(tmp_path, ROOM, PEER)
    unbound = Settings(tmp_path, group=ROOM.name)
    with pytest.raises(ValueError):
        unbound.with_window_binding(ROOM.name, window(ROOM, 11))
    with pytest.raises(ValueError):
        cfg.with_window_binding(PEER.name, window(ROOM, 11))
    cfg = cfg.with_window_binding(ROOM.name, window(ROOM, 11))
    with pytest.raises(ValueError):
        cfg.with_window_binding(PEER.name, window(PEER, 11))
    cfg = cfg.with_window_binding(PEER.name, window(PEER, 22, 'main'))
    other_account = replace(cfg, conversation_targets={
        **cfg.conversation_targets, ROOM.name: {**ROOM.binding, 'account': 'account-b'}})
    assert other_account.for_group(ROOM.name).binding is None


def test_compact_detached_window_accepts_only_its_own_minimum_size():
    candidate = replace(window(PEER, 22), width=360, height=360)
    candidate.validate()
    for invalid in (replace(candidate, width=359), replace(candidate, height=359),
                    replace(candidate, window_kind='main'), replace(candidate, window_kind='dialog')):
        with pytest.raises(ValueError):
            invalid.validate()


def test_saved_aliases_cannot_create_two_engines_for_one_recipient(tmp_path):
    with pytest.raises(ValueError, match='身份'):
        selected(tmp_path, ROOM, replace(ROOM, name='旧读书群备注'))


def test_binding_save_retries_a_transient_windows_reader_without_losing_old_file(tmp_path, monkeypatch):
    import os
    cfg=selected(tmp_path,ROOM)
    cfg.save()
    original=os.replace
    calls=0
    def contended(current,dest):
        nonlocal calls
        calls+=1
        if calls==1:
            assert Settings.load(tmp_path).binding is None
            raise PermissionError('temporary reader')
        original(current,dest)
    monkeypatch.setattr(os,'replace',contended)
    cfg.with_window_binding(ROOM.name,window(ROOM,11)).save()
    assert Settings.load(tmp_path).binding.hwnd==11
    assert not list(cfg.runtime.glob('config.json*.tmp'))


def screen_resident(cfg, store):
    from test_engine import Clock, IdentityVision, ReplyModel, Screen
    from xiaomao.domain import Frame, Message
    from xiaomao.resident import Resident

    class Window(Screen):
        def __init__(self, calibration):
            super().__init__()
            self.calibration = calibration
            if calibration:
                self.frame = Frame(calibration.target_group, (Message('甲锚点'), Message('乙锚点')))

    clock = Clock()
    return Resident(cfg, store, desktop_factory=Window, vision=IdentityVision(), model_factory=lambda _: ReplyModel(),
                    clock=clock, settle_seconds=0)


def test_missing_first_window_does_not_disable_ready_rooms_or_peer(tmp_path):
    from xiaomao.store import StateStore
    from xiaomao.domain import Message
    cfg = replace(selected(tmp_path, ROOM, OTHER, PEER), receive_mode='screen')
    cfg = cfg.with_window_binding(OTHER.name, window(OTHER, 33)).with_window_binding(PEER.name, window(PEER, 22))
    store = StateStore(cfg.runtime)
    try:
        resident = screen_resident(cfg, store)
        resident.enable()
        assert [e.mode for e in resident.engines] == ['observe', 'auto', 'auto']
        resident.observe_once()
        assert all(e.tracker.baseline is not None for e in resident.engines[1:])
        # A closed group cannot consume an available private window's message.
        room, peer = resident.engines[1:]
        room.desktop.hidden = True
        peer.desktop.background = True
        peer.desktop.frame = replace(peer.desktop.frame, messages=peer.desktop.frame.messages+(Message('小夏的新提问'),))
        for _ in range(2):
            resident.clock.advance(1)
            resident.observe_once()
        assert peer.pending()[0]['messages'] == ['小夏的新提问']
        assert not room.pending() and not room.desktop.events
    finally:
        store.close()


def test_hot_reload_retains_pending_work_and_respects_service_mode(tmp_path):
    from xiaomao.domain import Message
    from xiaomao.store import StateStore
    cfg = replace(selected(tmp_path, ROOM, PEER), receive_mode='screen')
    cfg = cfg.with_window_binding(ROOM.name, window(ROOM, 11))
    store = StateStore(cfg.runtime)
    try:
        resident = screen_resident(cfg, store)
        resident.enable()
        room, peer = resident.engines
        room._record([Message('原会话的新提问', message_id='room/new')])
        task = room.tasks[0]
        original_desktop = room.desktop
        cfg = cfg.with_window_binding(PEER.name, window(PEER, 22))
        cfg.save()
        result = resident.reload_windows()
        assert not result['window_reload_pending']
        assert resident.engines == [room, peer] and room.desktop is original_desktop
        assert room.tasks == [task] and store.get(task.id)['state'] == 'candidate'
        assert peer.desktop.calibration.hwnd == 22 and peer.mode == 'auto'
        resident.pause()
        cfg.with_window_binding(PEER.name, window(PEER, 44)).save()
        resident.reload_windows()
        assert peer.desktop.calibration.hwnd == 44
        assert all(e.mode == 'paused' for e in resident.engines)
    finally:
        store.close()


def test_reordering_window_targets_does_not_transfer_the_primary_groups_mention_name(tmp_path):
    from xiaomao.domain import Message
    from xiaomao.store import StateStore
    cfg=replace(selected(tmp_path,ROOM,PEER), receive_mode='screen', reply_mode='mentions',
                self_display_name='群内昵称', self_name_unique_confirmed=True)
    cfg=cfg.with_window_binding(ROOM.name,window(ROOM,11)).with_window_binding(PEER.name,window(PEER,22))
    store=StateStore(cfg.runtime)
    try:
        resident=screen_resident(cfg,store)
        mention=Message('@群内昵称 你好')
        assert resident.engines[0].policy.matches(mention)
        cfg.with_saved_conversations((PEER.name,ROOM.name)).save()
        resident.reload_windows()
        assert all(not e.settings.at_display_name and not e.policy.matches(mention) for e in resident.engines)
    finally:store.close()


def test_hot_reload_waits_for_old_recipient_receipt_and_new_engine_does_not_recover_live_journal(tmp_path):
    from xiaomao.domain import Message
    from xiaomao.store import StateStore
    cfg = replace(selected(tmp_path, ROOM), receive_mode='screen').with_window_binding(ROOM.name, window(ROOM, 11))
    store = StateStore(cfg.runtime)
    try:
        resident = screen_resident(cfg, store)
        resident.enable()
        room = resident.engines[0]
        room._record([Message('还在等待确认', message_id='room/wait')])
        task = room.tasks[0]
        store.mark_sending(task.id, resident.clock(), group=room.settings.memory_scope)
        task.state = 'awaiting_receipt'
        fresh = replace(cfg, group_names=(ROOM.name, OTHER.name),
                        conversation_targets={ROOM.name: ROOM.binding, OTHER.name: OTHER.binding})
        fresh = fresh.with_window_binding(ROOM.name, window(ROOM, 111)).with_window_binding(OTHER.name, window(OTHER, 33))
        fresh.save()
        assert resident.reload_windows()['window_reload_pending']
        assert room.desktop.calibration.hwnd == 11 and store.get(task.id)['state'] == 'sending'
        room._retire(task, 'verified')
        room._record([Message('后续待处理', message_id='room/later')])
        later = room.tasks[0]
        resident.observe_once()
        assert not resident.status()['window_reload_pending']
        assert room.desktop.calibration.hwnd == 111 and resident.engines[1].settings.group == OTHER.name
        assert later in room.tasks and store.get(later.id)['state'] == 'candidate'
    finally:
        store.close()


def test_database_routes_two_groups_and_private_chat_to_their_own_windows(tmp_path, monkeypatch):
    import hashlib
    import sqlite3
    from functools import partial
    from test_engine import Clock, IdentityVision, Screen
    from xiaomao.database import DatabaseFeed
    from xiaomao.dbcodec import discovery
    from xiaomao.domain import Frame
    from xiaomao.resident import Resident
    from xiaomao.store import StateStore

    peers = {11: ROOM, 22: PEER, 33: OTHER}
    cfg = replace(selected(tmp_path, *peers.values()), min_interval=3)
    for hwnd, contact in peers.items():
        cfg = cfg.with_window_binding(contact.name, window(contact, hwnd, pid=81 if hwnd==11 else 71))
    contacts = tmp_path/'db_storage/contact/contact.db'
    messages = tmp_path/'db_storage/message/message_0.db'
    contacts.parent.mkdir(parents=True)
    messages.parent.mkdir(parents=True)
    with sqlite3.connect(contacts) as conn:
        conn.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT)')
        conn.executemany('INSERT INTO contact VALUES(?,?,?)', [(p.id,p.name,'') for p in peers.values()])
    tables = {hwnd:'Msg_'+hashlib.md5(peer.id.encode()).hexdigest() for hwnd,peer in peers.items()}
    with sqlite3.connect(messages) as conn:
        conn.execute('CREATE TABLE Name2Id(user_name TEXT)')
        conn.executemany('INSERT INTO Name2Id VALUES(?)',[('self',),('peer',)])
        for table in tables.values():
            conn.execute(f'CREATE TABLE {table}(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,'
                         'create_time INTEGER,sort_seq INTEGER,real_sender_id INTEGER,message_content TEXT)')
    clock = Clock()
    monkeypatch.setattr('xiaomao.database.DatabaseFeed',partial(DatabaseFeed,clock=clock))

    def append(hwnd, sender, text):
        table = tables[hwnd]
        with sqlite3.connect(messages) as conn:
            conn.execute(f'INSERT INTO {table} SELECT NULL,coalesce(max(server_id),100)+1,1,?,1,?,? FROM {table}',
                         (int(clock()), sender, text))

    # Use real contact resolution, message tables, queue, quota and receipt
    # matching. Only the process inventory, native desktop and model are fake.
    monkeypatch.setattr(discovery,'discover_accounts',lambda:[{'id':'account-a','wxid':'self','path':str(tmp_path)}])
    monkeypatch.setattr(discovery,'active_account_ids',lambda accounts, processes:
                        {'account-a'} if processes[0]['pid'] in (71,81) else set())
    sent = []
    class Window(Screen):
        def __init__(self, calibration, **kwargs):
            super().__init__()
            self.calibration=calibration
            self.frame=Frame(peers[calibration.hwnd].name)
            self.background=calibration.hwnd==11  # Closed/unavailable for foreground input.
        def click_send(self):
            hwnd=self.calibration.hwnd
            sent.append((hwnd,self.frame.draft))
            append(hwnd,1,self.frame.draft)
            super().click_send()

    class Reply:
        def reply(self, history):return '回复'+history[-1].text

    monkeypatch.setattr('xiaomao.resident.WindowsDesktop',Window)
    store=StateStore(cfg.runtime)
    resident=Resident(cfg,store,vision=IdentityVision(),model_factory=lambda _:Reply(),clock=clock,settle_seconds=0)
    try:
        resident.enable()
        resident.observe_once()
        assert all(e.last_observed==clock() for e in resident.engines)
        for hwnd,peer in peers.items():
            append(hwnd,2,peer.name+'提问')
        resident.observe_once()
        assert len(sent)==1 and sent[0][0] in (22,33)
        clock.advance(3)
        resident.observe_once()
        assert sorted(sent)==[(22,'回复小夏提问'),(33,'回复摄影群提问')]
        assert store.counts()['verified']==2
        assert resident.engines[0].pending()[0]['messages']==['读书群提问']
        assert not resident.engines[0].desktop.events
        assert [(e.message_source.hub.pid,e.message_source.group) for e in resident.engines] == [
            (81,'读书群'), (71,'小夏'), (71,'摄影群')]
        for e in resident.engines:
            assert {m.text for m in e.context if m.direction=='in'} == {e.settings.group+'提问'}
    finally:
        for hub in resident.hubs.values():hub.close()
        store.close()


def test_header_mismatch_in_detached_chat_never_navigates_to_another_window(tmp_path):
    from xiaomao.store import StateStore
    from xiaomao.windows import DesktopTemporarilyHidden
    cfg = selected(tmp_path, ROOM).with_window_binding(ROOM.name, window(ROOM, 11))
    store=StateStore(cfg.runtime)
    try:
        engine=screen_resident(cfg,store).engines[0]
        engine.desktop.frame=replace(engine.desktop.frame, safe=False, reason='群标题与已校准目标不一致')
        def navigate(*args):raise AssertionError('独立窗口中不能使用主窗口导航')
        engine.desktop.select_target=navigate
        with pytest.raises(DesktopTemporarilyHidden):
            engine._frame(observation=True)
    finally:store.close()


def test_unavailable_window_update_is_retried_without_interrupting_other_observers(tmp_path):
    from xiaomao.store import StateStore
    cfg=replace(selected(tmp_path,ROOM,PEER),receive_mode='screen')
    cfg=cfg.with_window_binding(ROOM.name,window(ROOM,11)).with_window_binding(PEER.name,window(PEER,22))
    store=StateStore(cfg.runtime)
    try:
        resident=screen_resident(cfg,store)
        room,peer=resident.engines
        factory=resident.desktop_factory
        blocked=True
        def temporarily_unavailable(binding):
            if binding.hwnd==44 and blocked:raise OSError('synthetic constructor failure')
            return factory(binding)
        resident.desktop_factory=temporarily_unavailable
        cfg.with_window_binding(ROOM.name,window(ROOM,33)).with_window_binding(PEER.name,window(PEER,44)).save()
        assert resident.reload_windows()['window_reload_pending']
        resident.observe_once()
        assert (room.desktop.calibration.hwnd,peer.desktop.calibration.hwnd)==(11,22)
        assert room.last_observed is not None and peer.last_observed is not None
        blocked=False
        resident.observe_once()
        assert not resident.status()['window_reload_pending']
        assert (room.desktop.calibration.hwnd,peer.desktop.calibration.hwnd)==(33,44)
    finally:store.close()
