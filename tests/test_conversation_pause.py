from dataclasses import asdict,replace
import pytest
from xiaomao.config import Settings
from xiaomao.domain import Message,ReplyTask
from xiaomao.resident import Resident
from xiaomao.store import StateStore
from test_engine import Screen,IdentityVision,ReplyModel,Clock
from test_reply_preferences import configured
from test_vision import binding


def resident(cfg,store):
    return Resident(cfg,store,desktop_factory=lambda _:Screen(),vision=IdentityVision(),
                    model_factory=lambda _:ReplyModel(),clock=Clock(),settle_seconds=0)


@pytest.fixture
def rooms(tmp_path):
    cfg=replace(configured(tmp_path),calibration=asdict(binding()),
                model_base_url='http://localhost:8000',auto_on_launch=True)
    cfg.save();store=StateStore(cfg.runtime);live=resident(cfg,store)
    yield live,store
    for e in live.engines:
        if e.companion:e.companion.close()
    store.close()


def queue(live,engine):
    key=live.store.create('pending-'+engine.settings.group,live.clock())
    task=ReplyTask(key,live.clock(),live.clock(),'朋友',messages=[Message('聊聊')])
    engine.tasks.append(task)
    return task


def test_pause_one_preserves_other_queues_and_survives_restart(rooms):
    live,store=rooms;a,b,c=live.engines
    first=queue(live,a);second=queue(live,b)
    result=live.pause_selected(a.settings.group,a.settings.memory_scope)
    assert a.mode=='paused' and b.mode==c.mode=='auto'
    assert not a.tasks and b.tasks==[second]
    assert store.get(first.id)['state']=='skipped'
    assert result['mode']=='auto' and result['can_control_selected']
    assert Settings.load(live.settings.root).is_conversation_paused('甲群')
    restored=resident(Settings.load(live.settings.root),store)
    from xiaomao.__main__ import apply_initial_mode
    apply_initial_mode(restored,store,'auto')
    assert [e.mode for e in restored.engines]==['paused','auto','auto']
    live.reload_windows();live._apply_window_reload()
    assert [e.mode for e in live.engines]==['paused','auto','auto']


def test_resume_one_after_global_pause_does_not_resume_other_rooms_on_restart(rooms):
    live,store=rooms
    live.pause()
    peer=live.engines[2]
    live.enable_selected(peer.settings.group,peer.settings.memory_scope)
    assert [e.mode for e in live.engines]==['paused','paused','auto']
    restored=resident(Settings.load(live.settings.root),store)
    assert [e.mode for e in restored.engines]==['paused','paused','auto']
    live.enable()
    assert all(e.mode=='auto' for e in live.engines)
    assert not Settings.load(live.settings.root).paused_conversations


def test_paused_room_does_not_make_running_workbench_report_everything_paused(rooms):
    live,_=rooms;a,b,c=live.engines
    live.pause_selected(a.settings.group,a.settings.memory_scope)
    b.reason='数据库后台监听中';live.index=0
    status=live.status()
    assert status['mode']=='auto' and status['reason']==b.reason
    assert status['reason_group']==b.settings.group
    assert status['groups'][0]['mode']=='paused' and '暂停' in status['groups'][0]['reason']


def test_stale_identity_cannot_pause_another_recipient(rooms):
    live,_=rooms
    with pytest.raises(ValueError):live.pause_selected('甲群','other/room')
    assert all(e.mode=='auto' for e in live.engines)
    assert not Settings.load(live.settings.root).paused_conversations


def test_failed_pause_persistence_still_stops_only_the_selected_sender(rooms,monkeypatch):
    live,_=rooms;a,b,_=live.engines
    def fail(self,**kw):raise OSError('disk unavailable')
    monkeypatch.setattr(Settings,'save',fail)
    with pytest.raises(ValueError,match='暂停已生效'):live.pause_selected(a.settings.group,a.settings.memory_scope)
    assert a.mode=='paused' and a.cancelled.is_set() and b.mode=='auto'


def test_pause_is_bound_to_identity_and_removed_with_recipient(tmp_path):
    cfg=configured(tmp_path).with_conversation_pause('甲群',True)
    cfg.save();cfg=Settings.load(tmp_path)
    assert cfg.is_conversation_paused('甲群') and not cfg.is_conversation_paused('乙群')
    targets={**cfg.conversation_targets,'甲群':dict(account='another',id='different@chatroom',kind='group')}
    assert not replace(cfg,conversation_targets=targets).is_conversation_paused('甲群')
    assert not cfg.with_saved_conversations(('乙群','朋友')).paused_conversations
    with pytest.raises(ValueError):replace(cfg,paused_conversations=('not-a-valid-identity',))


def test_control_client_can_pause_selected_without_calling_global_pause(rooms):
    from xiaomao.client import ControlClient
    from xiaomao.control import ControlServer
    live,_=rooms;a,b,_=live.engines
    with ControlServer(live.settings.root,live):
        result=ControlClient(live.settings.root).pause_selected(a.settings.group,a.settings.memory_scope)
        assert result['mode']=='auto' and a.mode=='paused' and b.mode=='auto'


def test_calibrating_paused_legacy_title_migrates_pause_to_verified_identity(tmp_path):
    from xiaomao.contacts import Contact
    from xiaomao.selection import bind_reviewed_window
    original=configured(tmp_path)
    cfg=replace(original,conversation_targets={n:t for n,t in original.conversation_targets.items() if n!='甲群'},
        calibration=asdict(binding()),model_base_url='http://localhost:8000',auto_on_launch=True)
    cfg.save();store=StateStore(cfg.runtime);live=resident(cfg,store)
    try:
        live.pause();second=live.engines[1]
        live.enable_selected(second.settings.group,second.settings.memory_scope)
        saved=Settings.load(tmp_path)
        assert saved.is_conversation_paused('甲群')
        target=Contact('room-a@chatroom','account-a','甲群','group')
        draft=replace(cfg.binding,header_label='甲群',target_group='甲群',target_kind='group',ocr_title='甲群')
        updated=bind_reviewed_window(saved,draft,target)
        assert updated.is_conversation_paused('甲群')
        updated.save(expected=saved);live.reload_windows();live._apply_window_reload()
        assert [e.mode for e in live.engines]==['paused','auto','paused']
        assert all(not e.desktop.events for e in live.engines)
    finally:live.stop();store.close()


def test_new_calibration_while_all_rooms_paused_stays_paused_after_cold_start(rooms):
    from xiaomao.contacts import Contact
    from xiaomao.selection import bind_reviewed_window
    live,store=rooms;live.pause();saved=Settings.load(live.settings.root)
    peer=Contact('new-person','account-a','新同事','private')
    draft=replace(saved.binding,header_label=peer.name,target_group=peer.name,target_kind='private',ocr_title=peer.name)
    updated=bind_reviewed_window(saved,draft,peer)
    assert updated.is_conversation_paused(peer.name)
    updated.save(expected=saved)
    restarted=resident(updated,store)
    assert all(e.mode=='paused' for e in restarted.engines)
