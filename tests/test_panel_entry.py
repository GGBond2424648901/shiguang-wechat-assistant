from pathlib import Path
from types import SimpleNamespace
import threading
import time
import pytest


def test_second_panel_signals_existing_owner_instead_of_opening_duplicate(tmp_path):
    from xiaomao.panel_entry import PanelLease,signal_panel
    first=PanelLease(tmp_path)
    second=None
    try:
        assert first.owner
        first.open_signal()
        second=PanelLease(tmp_path)
        assert not second.owner
        assert signal_panel(tmp_path)
        assert first.consume_signal()
        assert not first.consume_signal()
    finally:
        if second:second.close()
        first.close()


def test_panel_signal_is_scoped_to_exact_profile_and_stops_after_close(tmp_path):
    from xiaomao.panel_entry import PanelLease,signal_panel
    first=PanelLease(tmp_path/'a')
    try:
        first.open_signal()
        assert not signal_panel(tmp_path/'b')
        assert not first.consume_signal()
    finally:first.close()
    assert not signal_panel(tmp_path/'a')


def test_explicit_panel_reopen_restores_tk_without_enabling_worker():
    from xiaomao.panel_entry import watch_panel_signal
    calls=[];ready=[True]
    class Root:
        def deiconify(self):calls.append('show')
        def lift(self):calls.append('raise')
        def focus_force(self):calls.append('focus')
        def after(self,n,fn):calls.append(('after',n))
        def winfo_exists(self):return True
    lease=SimpleNamespace(consume_signal=lambda:ready.pop() if ready else False)
    watch_panel_signal(Root(),lease)
    assert calls==['show','raise','focus',('after',200)]


def test_explicit_reopen_reconnects_worker_but_idle_watch_does_not_start_it():
    from xiaomao.panel_entry import watch_panel_signal
    calls=[];root=FakeRoot(calls);ready=[True,False,True]
    lease=SimpleNamespace(consume_signal=lambda:ready.pop(0))
    watch_panel_signal(root,lease,on_show=lambda:calls.append('connect'))
    assert calls==['show','raise','focus','connect']
    root.scheduled.pop(0)()
    assert calls==['show','raise','focus','connect']
    root.scheduled.pop(0)()
    assert calls==['show','raise','focus','connect']*2


def test_tray_commands_are_separate_from_stopping_icon(tmp_path):
    from xiaomao.panel_entry import resident_tray
    calls=[]
    resident=SimpleNamespace(pause=lambda:calls.append('pause'),stop=lambda:calls.append('stop'),
        status=lambda:{'mode':'auto','group_names':['示例群'],'pending_count':0})
    class Tray:
        def __init__(self,**kw):self.actions=kw
        def start(self):calls.append('start');return self
    tray=resident_tray(tmp_path,resident,tray_factory=Tray,opener=lambda root:calls.append('open'))
    assert calls==['start']
    tray.actions['on_open']();assert calls[-1]=='open'
    tray.actions['on_pause']();assert calls[-1]=='pause'
    tray.actions['on_exit']();assert calls[-1]=='stop'


class FakeRoot:
    def __init__(self, calls):
        self.calls, self.alive, self.scheduled = calls, True, []
    def winfo_exists(self):return self.alive
    def deiconify(self):self.calls.append('show')
    def lift(self):self.calls.append('raise')
    def focus_force(self):self.calls.append('focus')
    def after(self, delay, callback):self.scheduled.append(callback)
    def destroy(self):self.calls.append('closed');self.alive=False


def tray_for(root, calls):
    from xiaomao.panel_entry import resident_tray
    class Tray:
        def __init__(self, **actions):self.actions=actions
        def start(self):return self
    resident=SimpleNamespace(pause=lambda:None, stop=lambda:calls.append('resident_stop'),
        status=lambda:{'mode':'auto'})
    return resident_tray(root,resident,tray_factory=Tray)


def pump_exit(root, lease, handler, *, timeout=1):
    from xiaomao.panel_entry import watch_panel_signal
    called=[]
    def on_exit():
        called.append(threading.get_ident())
        return handler()
    deadline=time.monotonic()+timeout
    while not called and time.monotonic()<deadline:
        watch_panel_signal(root,lease,on_exit=on_exit)
        time.sleep(.005)
    assert called
    return called[0]


def test_tray_exit_asks_existing_gui_to_stop_and_wait_before_destroying_panel(tmp_path):
    from xiaomao.panel_entry import PanelLease
    from test_panel import panel
    p,calls=panel(tmp_path);p.root=FakeRoot(calls)
    lease=PanelLease(tmp_path)
    request=None
    try:
        lease.open_signal()
        tray=tray_for(tmp_path,calls)
        request=threading.Thread(target=tray.actions['on_exit'],daemon=True)
        request.start()
        gui_thread=pump_exit(p.root,lease,p.stop_and_exit)
        request.join(1)
        assert not request.is_alive()
        assert gui_thread==threading.get_ident()!=request.ident
        assert calls==['show','raise','focus','stop','wait','closed']
        assert p.closing
    finally:
        lease.close()
        if request:request.join(1)


def test_cancelled_exit_keeps_gui_and_worker_even_when_gui_is_closed_later(tmp_path,monkeypatch):
    from xiaomao.panel_entry import PanelLease
    from test_panel import panel
    p,calls=panel(tmp_path);p.root=FakeRoot(calls)
    p.view.pages={'settings':SimpleNamespace(key_entries=[SimpleNamespace(get=lambda:'UNSAVED_PRIVATE_KEY')])}
    prompts=[]
    monkeypatch.setattr('tkinter.messagebox.askokcancel',lambda *args,**kw:prompts.append(args) or False)
    lease=PanelLease(tmp_path);request=None
    try:
        lease.open_signal();tray=tray_for(tmp_path,calls)
        request=threading.Thread(target=tray.actions['on_exit'],daemon=True);request.start()
        pump_exit(p.root,lease,p.stop_and_exit)
        request.join(1)
        assert not request.is_alive() and prompts
        assert calls==['show','raise','focus'] and not p.closing
        assert 'UNSAVED_PRIVATE_KEY' not in str(prompts)
        lease.close()  # A later ordinary panel close must not revive the cancelled request.
        assert 'resident_stop' not in calls
    finally:
        lease.close()
        if request:request.join(1)


def test_exit_falls_back_to_resident_only_if_gui_disappears_before_handling_request(tmp_path):
    from xiaomao.panel_entry import PanelLease
    import win32event
    calls=[];lease=PanelLease(tmp_path);request=None
    try:
        lease.open_signal();tray=tray_for(tmp_path,calls)
        request=threading.Thread(target=tray.actions['on_exit'],daemon=True);request.start()
        assert win32event.WaitForSingleObject(lease.exit_event,1000)==win32event.WAIT_OBJECT_0
        lease.close()
        request.join(1)
        assert not request.is_alive() and calls==['resident_stop']
    finally:
        lease.close()
        if request:request.join(1)


def test_exit_channel_is_profile_scoped_and_worker_loss_never_closes_gui(tmp_path):
    from xiaomao.panel_entry import PanelLease,watch_panel_signal
    calls=[];root=FakeRoot(calls);lease=PanelLease(tmp_path/'other')
    try:
        lease.open_signal()
        tray_for(tmp_path/'requested',calls).actions['on_exit']()
        assert calls==['resident_stop']
        calls.clear()
        watch_panel_signal(root,lease,on_exit=lambda:calls.append('unexpected_exit'))
        assert calls==[] and root.alive and len(root.scheduled)==1
    finally:lease.close()


@pytest.mark.parametrize('draft', ['persona_editor','persona_other_document','persona_paste','settings_model',
                                    'settings_keys','settings_pacing','companion_routine'])
def test_explicit_exit_protects_unsaved_persona_and_settings_drafts(tmp_path,monkeypatch,draft):
    from test_panel import panel
    from xiaomao.config import Settings
    from xiaomao.persona import split_persona
    from xiaomao.companion import CompanionPreferences
    p,calls=panel(tmp_path);p.settings=Settings(tmp_path)
    if draft.startswith('persona'):
        page=SimpleNamespace(loaded=True,source='原稿',sections=split_persona('原稿'),previewing=False,
            editor=SimpleNamespace(get=lambda *args:'未保存的人格' if draft=='persona_editor' else '原稿'),
            shown='原稿',document_drafts={'other':('修改稿','原稿')} if draft=='persona_other_document' else {})
        if draft=='persona_paste':
            page.import_dialog=SimpleNamespace(winfo_exists=lambda:True,editor=SimpleNamespace(get=lambda *args:'尚未保存的粘贴原文'))
        p.view.pages={'persona':page}
    elif draft=='settings_model':
        p.model_name=SimpleNamespace(get=lambda:'edited-model')
    elif draft=='settings_keys':
        p.view.pages={'settings':SimpleNamespace(key_entries=[SimpleNamespace(get=lambda:'UNSAVED_KEY')])}
    elif draft=='settings_pacing':
        p.view.pages={'settings':SimpleNamespace(pacing_editor=SimpleNamespace(
            values=lambda: (30, 50, 3, 1, 1)))}
    else:
        prefs=CompanionPreferences()
        p.view.pages={'companion':SimpleNamespace(ready=True,loaded_preferences=prefs,values=lambda:prefs,
            routines_editor=SimpleNamespace(values=lambda:[{'id':'draft'}]))}
    prompts=[]
    monkeypatch.setattr('tkinter.messagebox.askokcancel',lambda *args,**kw:prompts.append(args) or False)
    assert p.stop_and_exit() is False
    assert prompts and calls==[] and not getattr(p,'exiting',False)


def test_repeated_gui_exit_request_does_not_queue_duplicate_shutdown(tmp_path):
    from test_panel import panel
    p,calls=panel(tmp_path)
    p.work=lambda *args,**kwargs:calls.append('queued_shutdown')
    assert p.stop_and_exit() is True
    assert p.stop_and_exit() is True
    assert calls==['queued_shutdown']


def test_gui_launch_passes_its_panel_exit_handler_to_signal_watch(tmp_path,monkeypatch):
    from xiaomao import gui,panel_entry
    calls=[];root=FakeRoot(calls)
    lease=SimpleNamespace(owner=True,open_signal=lambda:None,close=lambda:None)
    panel=SimpleNamespace(stop_and_exit=lambda:True,connect_worker=lambda:None)
    received=[]
    root.mainloop=lambda:root.scheduled.pop(0)()
    monkeypatch.setattr(panel_entry,'PanelLease',lambda directory:lease)
    monkeypatch.setattr(panel_entry,'watch_panel_signal',lambda root,lease,on_exit=None,on_show=None:received.append((on_exit,on_show)))
    monkeypatch.setattr(gui.tk,'Tk',lambda:root)
    monkeypatch.setattr(gui,'Panel',lambda root,directory:panel)
    gui.launch(tmp_path)
    assert received==[(panel.stop_and_exit,panel.connect_worker)]


def test_clean_async_form_snapshots_do_not_prompt_or_block_exit(tmp_path,monkeypatch):
    from test_panel import panel
    from xiaomao.config import Settings
    p,calls=panel(tmp_path);p.settings=Settings(tmp_path,model_name='configured-model')
    p.model_name=SimpleNamespace(get=lambda:'loaded-model')
    p._form_baseline={'model_name':'loaded-model'}
    p.view.pages={
        'persona':SimpleNamespace(loaded=False,document_drafts={},editor=SimpleNamespace(get=lambda *args:'读取中…')),
        'settings':SimpleNamespace(key_entries=[]),
        'companion':SimpleNamespace(ready=False,values=lambda:pytest.fail('unloaded form must not be read')),
    }
    monkeypatch.setattr('tkinter.messagebox.askokcancel',lambda *args,**kw:pytest.fail('clean forms must not prompt'))
    assert p.stop_and_exit() is True
    assert calls==['stop','wait','closed']


def test_reentrant_exit_during_dirty_prompt_does_not_open_another_prompt(tmp_path,monkeypatch):
    from test_panel import panel
    p,calls=panel(tmp_path)
    p.view.pages={'sessions':SimpleNamespace(can_leave=lambda:False)}
    prompts=[];nested=[]
    def ask(*args,**kwargs):
        prompts.append(1)
        if len(prompts)==1:nested.append(p.stop_and_exit())
        return False
    monkeypatch.setattr('tkinter.messagebox.askokcancel',ask)
    assert p.stop_and_exit() is False
    assert prompts==[1] and nested==[False] and calls==[]


def test_exit_event_access_error_never_bypasses_existing_gui_guard(tmp_path,monkeypatch):
    from xiaomao.panel_entry import PanelLease
    import win32api,win32event
    calls=[];lease=PanelLease(tmp_path)
    try:
        lease.open_signal()
        def denied(*args):raise win32api.error(5,'OpenEvent','PRIVATE_ACCESS_DETAIL')
        monkeypatch.setattr(win32event,'OpenEvent',denied)
        monkeypatch.setattr(win32event,'OpenMutex',denied)
        with pytest.raises(ValueError) as failure:
            tray_for(tmp_path,calls).actions['on_exit']()
        assert calls==[] and 'PRIVATE_ACCESS_DETAIL' not in str(failure.value)
    finally:lease.close()
