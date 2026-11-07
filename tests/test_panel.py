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
    p.view=SimpleNamespace(pages={})
    p.reason=SimpleNamespace(set=lambda value:None)
    p.client=SimpleNamespace(stop=lambda:events.append('stop'),wait_stopped=lambda:events.append('wait'))
    p.work=lambda action,success=None,failure=None,**options:success(action()) if success else action()
    p.show_error=lambda value:events.append('error')
    return p,events


def test_closing_panel_keeps_standalone_worker_running(tmp_path):
    p,events=panel(tmp_path);p.hide_panel()
    assert events==['closed'] and p.closing


def test_panel_waits_for_worker_before_polling_status_or_queue(tmp_path):
    p,events=panel(tmp_path)
    p.poll_busy=False
    p.root.after=lambda *_:None
    p.view.status={}
    p.view.page='assistant'
    p.view.refresh_pending=lambda **_:events.append('queue')
    p.show_status=lambda data:setattr(p.view,'status',data)
    p.set_preview=lambda _:None
    requests=[]
    p.client=SimpleNamespace(start=lambda:{'mode':'auto'},status=lambda:pytest.fail('polled before startup'))
    p.work=lambda action,success,failure,**_:requests.append((action,success))
    p.connect_worker()
    assert p.view.status['mode']=='starting'
    p.poll()
    assert len(requests)==1 and not events
    action,success=requests.pop()
    success(action())
    assert p.view.status['mode']=='auto' and events==['queue']


def test_reopen_reconnect_keeps_existing_worker_paused(tmp_path,monkeypatch):
    from xiaomao.client import ControlClient
    from xiaomao import launch
    p,events=panel(tmp_path)
    p.view.status={'mode':'disconnected'};p.view.page='settings'
    p.client=ControlClient(tmp_path)
    p.client.status=lambda:{'running':True,'mode':'paused','reason':'用户已暂停'}
    p.show_status=lambda data:setattr(p.view,'status',data)
    monkeypatch.setattr(launch,'spawn_worker',lambda *args,**kwargs:pytest.fail('existing worker must not be restarted'))
    p.connect_worker()
    assert p.view.status=={'running':True,'mode':'paused','reason':'用户已暂停'}


def test_missing_tray_does_not_discard_last_visible_entry(tmp_path):
    p,events=panel(tmp_path)
    p.view.status={'tray':{'running':False,'error':'通知图标暂不可用'}}
    p.hide_panel()
    assert not p.closing and events==['error']


def test_explicit_stop_and_exit_waits_for_worker_exit(tmp_path):
    p,events=panel(tmp_path);p.stop_and_exit()
    assert events==['stop','wait','closed'] and p.closing


def test_failed_full_exit_keeps_panel_available_for_retry(tmp_path):
    from xiaomao.client import ControlUnavailable
    p,events=panel(tmp_path)
    p.view.set_exiting=lambda value:events.append(('exiting',value))
    def fail_wait():raise ControlUnavailable('后台仍在结束当前请求')
    p.client.wait_stopped=fail_wait
    def work(action,success,failure,**options):
        try:result=action()
        except ControlUnavailable as error:failure(str(error))
        else:success(result)
    p.work=work
    p.stop_and_exit()
    assert events==[('exiting',True),'stop',('exiting',False),'error']
    assert not p.closing and not p.exiting


@pytest.mark.parametrize('choice,expected',[('background',['closed']),('exit',['stop','wait','closed']),(None,[])])
def test_window_close_choice_routes_background_full_exit_or_cancel(tmp_path,monkeypatch,choice,expected):
    p,events=panel(tmp_path)
    monkeypatch.setattr('xiaomao.feedback_ui.ask_close_action',lambda *a,**kw:choice,raising=False)
    p.close()
    assert events==expected


def test_close_chooser_does_not_reenter_and_allows_full_exit_without_tray(tmp_path,monkeypatch):
    p,events=panel(tmp_path);p.view.status={'tray':{'running':False}}
    opened=[]
    def choose(*args,**kw):
        opened.append(kw);p.close();return 'exit'
    monkeypatch.setattr('xiaomao.feedback_ui.ask_close_action',choose,raising=False)
    p.close()
    assert len(opened)==1 and opened[0]['background_available'] is False
    assert events==['stop','wait','closed']


@pytest.mark.parametrize('choice', ['background','exit',None])
def test_close_dialog_buttons_return_only_the_chosen_action(native_root,choice):
    import tkinter as tk
    from xiaomao.feedback_ui import CloseChoice
    root=tk.Toplevel(native_root);root.geometry('930x620+3400+2000')
    try:
        dialog=CloseChoice(root);root.update()
        control={'background':dialog.background_button,'exit':dialog.exit_button,None:dialog.cancel_button}[choice]
        assert control.winfo_ismapped() and dialog.grab_current() is dialog
        control.invoke();root.update()
        assert dialog.result==choice and not dialog.winfo_exists() and root.grab_current() is None
    finally:root.destroy()


def test_close_dialog_escape_cancels_and_missing_tray_disables_background(native_root):
    import tkinter as tk
    from xiaomao.feedback_ui import CloseChoice
    root=tk.Toplevel(native_root);root.geometry('930x620+3400+2000')
    try:
        dialog=CloseChoice(root,background_available=False);root.update()
        dialog.background_button.invoke();assert dialog.winfo_exists()
        dialog.cancel_button.focus_force();root.update()
        dialog.cancel_button.event_generate('<Escape>');root.update()
        assert dialog.result is None and not dialog.winfo_exists()
    finally:root.destroy()


def test_close_choice_is_small_owned_window_and_leaves_page_visible(native_root):
    import tkinter as tk
    from xiaomao.feedback_ui import CloseChoice
    root=tk.Toplevel(native_root);root.geometry('1000x740+3200+1800')
    page=tk.Frame(root);page.pack(fill='both',expand=True)
    original=tk.Entry(page);original.pack();original.insert(0,'未保存的页面内容')
    root.update();before=root.geometry()
    try:
        dialog=CloseChoice(root);root.update()
        assert dialog.winfo_toplevel() is dialog
        assert str(dialog.transient())==str(root)
        assert root.geometry()==before and page.winfo_ismapped()
        assert original.get()=='未保存的页面内容'
        assert 400<=dialog.winfo_width()<=520
        assert 250<=dialog.winfo_height()<=450
        assert abs(dialog.winfo_rootx()+dialog.winfo_width()/2-root.winfo_rootx()-root.winfo_width()/2)<15
        root.tk.call(dialog.protocol('WM_DELETE_WINDOW'));root.update()
        assert dialog.result is None and not dialog.winfo_exists()
        assert root.winfo_exists() and page.winfo_ismapped()
        assert root.grab_current() is None
    finally:root.destroy()


@pytest.mark.parametrize('discard',[True,False])
def test_explicit_exit_confirms_unsaved_session_changes(tmp_path,monkeypatch,discard):
    p,events=panel(tmp_path)
    p.view.pages={'sessions':SimpleNamespace(can_leave=lambda:False)}
    asked=[]
    monkeypatch.setattr('tkinter.messagebox.askokcancel',lambda *a,**kw:asked.append(a) or discard)
    p.stop_and_exit()
    assert asked
    assert events==(['stop','wait','closed'] if discard else [])


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
