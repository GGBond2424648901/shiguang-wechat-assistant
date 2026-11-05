from types import SimpleNamespace
import threading
import pytest


@pytest.mark.parametrize('reply_mode,label', [('natural','自然参与'),('all','逐条回复'),('mentions','仅点名')])
@pytest.mark.parametrize('at_enabled', [True,False])
def test_status_distinguishes_reply_mode_and_mention_recognition(tmp_path,reply_mode,label,at_enabled):
    from xiaomao.config import Settings
    from xiaomao.gui import Panel
    values={}
    p=Panel.__new__(Panel)
    p.settings=Settings(tmp_path,reply_mode=reply_mode)
    p.connection_error=None
    for name in ('mode','reason','reply_scope','details'):
        setattr(p,name,SimpleNamespace(set=lambda value,key=name:values.__setitem__(key,value)))
    p.view=SimpleNamespace(update_text=lambda:None)
    p.show_status({'mode':'auto','at_enabled':at_enabled})
    assert label in values['reply_scope']
    assert '点名：@小猫 / @小玲' in values['details']
    assert ('额外群昵称已配置' in values['details']) is at_enabled
    if reply_mode=='natural':
        assert '@点名必回' in values['reply_scope']
        assert '模型判断' in values['reply_scope']


def test_status_uses_configured_wake_aliases_for_mention_guidance(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.gui import Panel
    values={}
    p=Panel.__new__(Panel)
    p.settings=Settings(tmp_path,wake_aliases=('小玲','玲玲'))
    p.connection_error=None
    for name in ('mode','reason','reply_scope','details'):
        setattr(p,name,SimpleNamespace(set=lambda value,key=name:values.__setitem__(key,value)))
    p.view=SimpleNamespace(update_text=lambda:None)
    p.show_status({'mode':'auto','at_enabled':False})
    assert '点名：@小玲 / @玲玲 / @小猫' in values['details']
    assert '未配置' not in values['details']


def panel(tmp_path):
    from xiaomao.gui import Panel
    events=[];p=Panel.__new__(Panel)
    p.closing=False;p.switch_busy=False;p.switch_cancel=threading.Event();p.directory=tmp_path
    p.root=SimpleNamespace(destroy=lambda:events.append('closed'))
    p.reason=SimpleNamespace(set=lambda value:None)
    p.client=SimpleNamespace(stop=lambda:events.append('stop'),wait_stopped=lambda:events.append('wait'))
    p.work=lambda action,success=None,failure=None,**options:success(action()) if success else action()
    p.show_error=lambda value:events.append('error')
    return p,events


def test_closing_panel_keeps_standalone_worker_running(tmp_path):
    p,events=panel(tmp_path);p.close()
    assert events==['closed'] and p.closing


def test_explicit_stop_and_exit_waits_for_worker_exit(tmp_path):
    p,events=panel(tmp_path);p.stop_and_exit()
    assert events==['stop','wait','closed'] and p.closing


def test_quick_chat_profile_supports_shorter_idle_and_larger_context(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.sessions_ui import RATES
    cfg=Settings(tmp_path,poll_seconds=1,idle_seconds=2,min_interval=5,hourly_limit=60,context_limit=24)
    cfg.save();assert Settings.load(tmp_path).idle_seconds==2
    assert (5,60,6,7) in RATES.values()
    assert (4,180,10,4) in RATES.values()


def test_saving_sticker_switch_does_not_resume_a_manual_pause(tmp_path,monkeypatch):
    from xiaomao.config import Settings,Calibration
    from xiaomao.gui import Panel
    cfg=Settings(tmp_path);cfg.save();p=Panel.__new__(Panel);p.directory=tmp_path;starts=[]
    p.client=SimpleNamespace(status=lambda:{'mode':'paused'},start=lambda **kw:starts.append(kw))
    p.work=lambda action,*args,**kwargs:action()
    binding=Calibration(1,1,800,600,cfg.group,{'header':[0,0,1,.1],'messages':[0,.1,1,.7],'input':[0,.8,.8,.95],'send':[.8,.95,1,1]},True)
    monkeypatch.setattr('xiaomao.selection.refresh_binding',lambda cfg:binding)
    monkeypatch.setattr('xiaomao.selection.stop_confirmed',lambda *a:None)
    p.apply_group_settings([cfg.group],'natural',(4,180,10,4),lambda:None,stickers_enabled=False)
    assert starts==[{'initial_mode':'paused'}] and not Settings.load(tmp_path).stickers_enabled


def test_exit_is_final_after_an_inflight_restart_and_blocks_later_starts(tmp_path):
    from xiaomao.gui import Panel
    import queue
    p,events=panel(tmp_path);p.work=Panel.work.__get__(p)
    p.results=queue.Queue();p.mutation_lock=threading.RLock();p.pending_mutations=0;p.exiting=False
    entered=threading.Event();release=threading.Event()
    def saving():
        entered.set();assert release.wait(2);events.append('start')
    p.work(saving)
    assert entered.wait(1)
    p.stop_and_exit()
    p.work(lambda:events.append('late_start'))
    release.set()
    for _ in range(3):
        callback,value=p.results.get(timeout=3)
        if callback:callback(value)
    assert [x for x in events if x!='error']==['start','stop','wait','closed']
