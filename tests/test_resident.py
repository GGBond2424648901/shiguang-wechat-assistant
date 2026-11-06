from dataclasses import asdict,replace
from test_engine import IdentityVision, system


def resident_for(system):
    from xiaomao.resident import Resident
    engine,screen,model,clock,store=system
    return Resident(engine.settings,store,desktop_factory=lambda _:screen,vision=IdentityVision(),
                    model_factory=lambda _:model,clock=clock,settle_seconds=0)


def test_observer_storage_error_during_pause_still_leaves_memory_state_safe(system,monkeypatch):
    from xiaomao.domain import Message
    _,screen,_,_,store=system
    resident=resident_for(system)
    engine=resident.engines[0]
    engine.enable();engine._record([Message('小猫你好',message_id='q',speaker_id='p_'+'a'*16)])
    def failed(*args,**kwargs):raise OSError('PRIVATE_DISK_ERROR')
    monkeypatch.setattr(store,'update',failed)
    monkeypatch.setattr(engine,'observe_once',lambda:store.update(engine.tasks[0].id,'generated'))
    resident.observe_once()
    assert engine.mode=='paused' and engine.cancelled.is_set() and not screen.events


def test_unexpected_observer_failure_cannot_leave_live_control_with_dead_worker(system,caplog,monkeypatch):
    resident=resident_for(system)
    engine=resident.engines[0]
    calls=[]
    def observe():
        calls.append(1)
        if len(calls)==1:raise RuntimeError('PRIVATE_MESSAGE_MUST_NOT_BE_LOGGED')
    monkeypatch.setattr(engine,'observe_once',observe)
    resident.observe_once()
    resident.observe_once()
    assert len(calls)==2 and engine.mode=='paused'
    assert '监听' in engine.reason and 'RuntimeError' in caplog.text
    assert 'PRIVATE_MESSAGE' not in caplog.text


def test_selected_groups_keep_independent_baselines_and_pending_messages(tmp_path):
    from xiaomao.resident import Resident
    from xiaomao.config import Settings
    from xiaomao.store import StateStore
    from xiaomao.domain import Frame,Message
    from test_engine import Clock,Screen,IdentityVision,ReplyModel
    from test_vision import binding
    clock=Clock()
    world={'current':'甲群','frames':{name:Frame(name,(Message(name+'锚点甲','朋友'),Message(name+'锚点乙','朋友'))) for name in ('甲群','乙群')}}
    switches=[]
    class Desktop(Screen):
        def __init__(self,calibration):super().__init__();self.target=calibration.header_label
        def capture(self):return world['frames'][world['current']]
        def select_target(self,idle_seconds=10):world['current']=self.target;switches.append(self.target);return True
    class GroupVision(IdentityVision):
        def read(self,image,calibration):
            return image if image.group==calibration.header_label else replace(image,safe=False,reason='群标题与已校准目标不一致')
    cfg=Settings(tmp_path,group='甲群',group_names=('甲群','乙群'),reply_mode='all',calibration=asdict(binding()),model_base_url='http://127.0.0.1:11434/v1')
    store=StateStore(tmp_path/'runtime')
    try:
        resident=Resident(cfg,store,desktop_factory=Desktop,vision=GroupVision(),model_factory=lambda cfg:ReplyModel(),clock=clock,settle_seconds=0)
        for _ in range(12):clock.advance(3);resident.observe_once()
        assert all(e.tracker.baseline is not None for e in resident.engines)
        for name in ('甲群','乙群'):
            world['frames'][name]=replace(world['frames'][name],messages=world['frames'][name].messages+(Message('普通新消息','朋友'),))
        for _ in range(18):clock.advance(3);resident.observe_once()
        pending=resident.pending()
        assert {(t['group'],tuple(t['messages'])) for t in pending}=={('甲群',('普通新消息',)),('乙群',('普通新消息',))}
        assert all(all(not m.text.startswith('乙群' if e.settings.group=='甲群' else '甲群') for m in e.context) for e in resident.engines)
        assert set(switches)=={'甲群','乙群'}
        resident.pause()
        for _ in range(3):clock.advance(3);resident.observe_once()
        assert resident.status()['mode']=='paused' and resident.pending()==[]
    finally:store.close()
