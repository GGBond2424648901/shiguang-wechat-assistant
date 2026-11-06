import random
import pytest
from xiaomao.domain import Message
from xiaomao.store import StateStore
from test_engine import system


def paced_engine(system,monkeypatch,values):
    engine,screen,model,clock,store=system
    engine.settings.natural_pacing=True
    engine.settings.min_interval=5
    engine.settings.hourly_limit=60
    engine.mode='auto'
    draws=iter(values)
    monkeypatch.setattr(random,'uniform',lambda a,b:next(draws))
    return engine,screen,model,clock,store


def test_random_wait_generates_once_without_activating_or_typing(system,monkeypatch):
    engine,screen,model,clock,store=paced_engine(system,monkeypatch,[4,6])
    calls=[]
    model.reply=lambda messages:calls.append(messages[-1].text) or '小玲在呢。'
    engine._record([Message('小猫你好',message_id='one')]);task=engine.tasks[0]
    engine._reply_and_send(task)
    assert screen.events==[] and screen.prepared==0
    assert calls==['小猫你好']
    clock.advance(3);engine._reply_and_send(task)
    assert screen.events==[] and len(calls)==1
    clock.advance(1);engine._reply_and_send(task)
    assert screen.events.count('send')==1 and len(calls)==1
    assert store.counts()=={'verified':1}


def test_queued_messages_keep_spacing_from_actual_previous_send(system,monkeypatch):
    engine,screen,model,clock,store=paced_engine(system,monkeypatch,[4,0,2,7])
    engine._record([Message('小猫第一条',message_id='one'),Message('小猫第二条',message_id='two')])
    first,second=engine.tasks
    engine._reply_and_send(first);clock.advance(4);engine._reply_and_send(first)
    assert screen.events.count('send')==1
    engine._reply_and_send(second)
    clock.advance(7);engine._reply_and_send(second)
    assert screen.events.count('send')==1
    clock.advance(5);engine._reply_and_send(second)
    assert screen.events.count('send')==2


def test_waiting_for_random_delay_continues_database_receipt(system,monkeypatch):
    from xiaomao.database import MessageBatch
    engine,screen,model,clock,store=paced_engine(system,monkeypatch,[5,7])
    batches=iter([MessageBatch([],[],True),
                  MessageBatch([],[Message('小猫第一条',message_id='one',created_at=101)]),
                  MessageBatch([],[Message('小猫继续',message_id='two',created_at=102)])])
    class Feed:
        def poll(self):return next(batches)
    engine.message_source=Feed()
    engine.observe_once();clock.advance(1);engine.observe_once()
    clock.advance(1);engine.observe_once()
    assert len(engine.tasks)==2 and engine.mode=='auto'
    assert engine.context[-1].text=='小猫继续'
    assert screen.events==[] and screen.prepared==0


def test_pause_during_random_wait_never_types_later(system,monkeypatch):
    engine,screen,model,clock,store=paced_engine(system,monkeypatch,[5,0])
    engine._record([Message('小猫你好',message_id='one')]);task=engine.tasks[0]
    engine._reply_and_send(task);engine.pause();clock.advance(30)
    engine.observe_once()
    assert screen.events==[] and engine.mode=='paused' and not engine.tasks


def test_minute_limit_is_rolling_shared_and_survives_restart(tmp_path):
    store=StateStore(tmp_path)
    for index,at in enumerate((45,55,65,75,85,95)):
        task=store.create(store.fingerprint(str(index)),at);store.mark_sending(task,at)
    store.close();store=StateStore(tmp_path)
    try:
        assert store.allow_send(100,3,60,minute_limit=6)==(False,'minute_limit')
        assert store.allow_send(105,3,60,minute_limit=6)==(True,'')
        assert store.allow_send(105,3,6,minute_limit=6)==(False,'hourly_limit')
        assert store.allow_send(90,3,60,minute_limit=6)==(False,'interval')
    finally:store.close()


def test_minute_cap_prevents_engine_activating_input_until_window_advances(system,monkeypatch):
    engine,screen,model,clock,store=paced_engine(system,monkeypatch,[2,0])
    for index,at in enumerate((56,66,76,86,96,104)):
        previous=store.create(store.fingerprint('previous'+str(index)),at)
        store.mark_sending(previous,at);store.update(previous,'verified')
    clock.value=110
    engine._record([Message('小猫新问题',message_id='new')]);task=engine.tasks[0]
    engine._reply_and_send(task);clock.advance(2);engine._reply_and_send(task)
    assert screen.events==[] and screen.prepared==0 and engine.mode=='auto'
    clock.advance(4);engine._reply_and_send(task)
    assert screen.events.count('send')==1


def test_configurable_fast_pacing_roundtrips_and_honors_tenth_message(tmp_path,system,monkeypatch):
    from xiaomao.config import Settings
    cfg=Settings(tmp_path,min_interval=4,hourly_limit=180,minute_limit=10,pacing_jitter=4)
    cfg.save();again=Settings.load(tmp_path)
    assert (again.min_interval,again.hourly_limit,again.minute_limit,again.pacing_jitter)==(4,180,10,4)
    engine,screen,model,clock,store=paced_engine(system,monkeypatch,[2,0])
    engine.settings.minute_limit=10;engine.settings.pacing_jitter=4
    assert engine.status()['minute_limit']==10
    assert engine.status()['send_interval_range']==[5,9]
    for i in range(10):
        task=store.create(store.fingerprint(str(i)),50+i*5)
        store.mark_sending(task,50+i*5);store.update(task,'verified')
    clock.value=100
    engine._record([Message('小猫你好',message_id='new')]);task=engine.tasks[0]
    engine._reply_and_send(task);clock.advance(2);engine._reply_and_send(task)
    assert screen.prepared==0
    clock.advance(8);engine._reply_and_send(task)
    assert screen.events.count('send')==1


@pytest.mark.parametrize('kwargs',[{'minute_limit':0},{'minute_limit':11},{'minute_limit':True},
    {'hourly_limit':501},{'hourly_limit':True},{'hourly_limit':1.5},
    {'min_interval':float('nan')},{'pacing_jitter':-1},{'pacing_jitter':True}])
def test_pacing_limits_validate(tmp_path,kwargs):
    from xiaomao.config import Settings
    with pytest.raises(ValueError):Settings(tmp_path,**kwargs)


def test_applying_advertised_preset_enables_its_minute_cap_and_randomness(system,monkeypatch):
    from types import SimpleNamespace
    from xiaomao.gui import Panel
    from xiaomao.config import Settings
    import xiaomao.selection as selection
    e,*_=system
    e.settings.natural_pacing=False;e.settings.save()
    monkeypatch.setattr(selection,'refresh_binding',lambda cfg:cfg.binding)
    monkeypatch.setattr(selection,'stop_confirmed',lambda *a:None)
    started=[]
    panel=SimpleNamespace(directory=e.settings.root,
                          client=SimpleNamespace(status=lambda:{'mode':'auto'},start=lambda **kw:started.append(kw) or {}),
                          work=lambda action,*a,**kw:action())
    Panel.apply_group_settings(panel,[e.settings.group],'all',(4,180,10,4),lambda:None)
    result=Settings.load(e.settings.root)
    assert result.natural_pacing
    assert started==[{'initial_mode':'auto'}]
    assert (result.min_interval,result.hourly_limit,result.minute_limit,result.pacing_jitter)==(4,180,10,4)


def test_500_hourly_quota_is_persisted_enforced_and_released_by_the_rolling_window(tmp_path):
    from xiaomao.config import Settings
    Settings(tmp_path,min_interval=4.5,pacing_jitter=1.25,minute_limit=10,hourly_limit=500).save()
    cfg=Settings.load(tmp_path)
    assert (cfg.min_interval,cfg.pacing_jitter,cfg.minute_limit,cfg.hourly_limit)==(4.5,1.25,10,500)
    store=StateStore(cfg.runtime)
    with store.db:
        store.db.executemany('INSERT INTO attempts(task_id,at) VALUES(?,?)',
            ((f'synthetic-{index}',100+index*7) for index in range(500)))
    store.close()
    store=StateStore(cfg.runtime)
    try:
        assert store.allow_send(3600,cfg.min_interval,cfg.hourly_limit,minute_limit=cfg.minute_limit)==(False,'hourly_limit')
        assert store.allow_send(3700,cfg.min_interval,cfg.hourly_limit,minute_limit=cfg.minute_limit)==(True,'')
    finally:
        store.close()


def test_loading_old_numeric_hourly_limit_preserves_effective_attempt_count(tmp_path):
    from xiaomao.config import Settings
    cfg=Settings(tmp_path)
    cfg.runtime.mkdir()
    (cfg.runtime/'config.json').write_text('{"min_interval": 97.5, "hourly_limit": 2.5}',encoding='utf8')
    cfg=Settings.load(tmp_path)
    assert (cfg.min_interval,cfg.hourly_limit)==(97.5,3)


def test_saving_other_rules_preserves_legacy_runtime_pacing(system,monkeypatch):
    from types import SimpleNamespace
    from xiaomao.gui import Panel
    from xiaomao.config import Settings
    import xiaomao.selection as selection
    engine,*_=system
    cfg=engine.settings
    cfg.natural_pacing=False
    cfg.min_interval,cfg.hourly_limit,cfg.minute_limit,cfg.pacing_jitter=(310.125,2,1,0)
    cfg.context_limit=8
    cfg.save()
    monkeypatch.setattr(selection,'refresh_binding',lambda settings:settings.binding)
    monkeypatch.setattr(selection,'stop_confirmed',lambda *args:None)
    panel=SimpleNamespace(directory=cfg.root,work=lambda action,*args,**kw:action(),
                          client=SimpleNamespace(status=lambda:{'mode':'paused'},start=lambda **kw:{}))
    Panel.apply_group_settings(panel,[cfg.group],'all',(4,180,10,4),lambda:None,keep_pacing=True)
    saved=Settings.load(cfg.root)
    assert (saved.min_interval,saved.hourly_limit,saved.minute_limit,saved.pacing_jitter)==(310.125,2,1,0)
    assert not saved.natural_pacing and saved.context_limit==8 and saved.reply_mode=='all'
