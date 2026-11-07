from types import SimpleNamespace
import tkinter as tk
import pytest
from xiaomao.config import Settings
from test_reply_preferences import configured


@pytest.fixture
def workspace(native_root,tmp_path):
    from xiaomao.theme import GlassView
    window=tk.Toplevel(native_root);window.geometry('1200x800+3400+2000')
    cfg=configured(tmp_path);cfg.save();calls=[]
    panel=SimpleNamespace(root=window,directory=tmp_path,settings=cfg,connection_error=None,switch_busy=False)
    for key in ('mode','reason','notice','details','target_group','reply_scope','display_name','model_name','secret','secret_state'):
        setattr(panel,key,tk.StringVar(window))
    for key in ('at_unique','auto_on_launch','split_replies','owner_tone'):
        setattr(panel,key,tk.BooleanVar(window))
    for key in ('choose_contact','edit_groups','edit_persona','edit_companion','save_settings','test_model','calibrate','stop_and_exit','switch_selected_group'):
        setattr(panel,key,lambda:None)
    panel.control=lambda op:calls.append(('global',op))
    panel.control_conversation=lambda group,op:calls.append((group,op))
    panel.client=SimpleNamespace(pending=lambda:{'tasks':[]},reply_latest=lambda:None)
    panel.show_pending=panel.set_preview=panel.show_status=lambda *args:None
    panel.work=lambda fn,done,*args,**kw:done(fn())
    view=panel.view=GlassView(panel);view.pack(fill='both',expand=True)
    view.status={'running':True,'mode':'auto','group':'甲群','group_names':list(cfg.allowed_groups),
        'can_control_selected':True,'groups':[{'name':name,'mode':mode,'reply_mode':'natural'}
            for name,mode in zip(cfg.allowed_groups,('auto','paused','auto'))]}
    view.update_text();window.update()
    yield panel,view,calls,window
    window.destroy()


def test_selected_recipient_controls_follow_queue_selection_not_global_mode(workspace):
    _,view,calls,window=workspace
    view.watch_table.selection_set('乙群');window.update()
    assert view.target_name.get()=='乙群'
    assert view.actions['enable'].cget('state')=='normal'
    assert view.actions['pause'].cget('state')=='disabled'
    view.actions['enable'].invoke()
    assert calls==[('乙群','enable')]
    view.watch_table.selection_set('甲群');window.update()
    view.actions['pause'].invoke()
    assert calls[-1]==('甲群','pause')
    assert ('global','pause') not in calls


def test_workbench_calibrates_only_the_selected_conversation(workspace):
    panel,view,calls,window=workspace
    panel.calibrate=lambda group,*,auto_match:calls.append((group,auto_match))
    saved=Settings.load(panel.directory)
    view.watch_table.selection_set('乙群');window.update()
    view.actions['calibrate'].invoke()
    assert calls==[('乙群',True)] and Settings.load(panel.directory)==saved


def test_database_listener_in_background_displays_its_read_state(workspace):
    _,view,_,window=workspace
    view.status['groups'][0].update(receive_mode='database',last_observed=100,window_state='在后台')
    view.update_text();window.update()
    assert view.watch_table.set('甲群','state')=='监听中'
    assert '最近读取' in view.target_info.get()
    view.status['groups'][0]['receive_retrying']=True
    view.update_text()
    assert view.watch_table.set('甲群','state')=='接收重试'


def test_new_calibration_remains_selected_until_backend_can_enable_it(workspace):
    _,view,calls,window=workspace
    ready=view.status
    view.status={**ready,'group_names':['甲群'],'groups':ready['groups'][:1],'window_reload_pending':True}
    view.focus_conversation('乙群');window.update()
    assert view.target_name.get()=='乙群' and view.actions['enable'].instate(('disabled',))
    assert view.watch_table.selection()==('乙群',)
    view.control_selected('enable')
    assert not calls
    view.status=ready
    view.update_text();window.update()
    view.actions['enable'].invoke()
    assert calls==[('乙群','enable')]


def test_each_visible_row_has_real_independent_control_and_global_pause_is_separate(workspace):
    _,view,calls,window=workspace
    assert set(view.watch_controls)=={'甲群','乙群','朋友'}
    view.watch_controls['朋友'].invoke()
    assert calls==[('朋友','pause')]
    assert view.watch_controls['乙群'].cget('text')=='开启'
    view.watch_controls['乙群'].invoke()
    assert calls[-1]==('乙群','enable')
    view.actions['pause_all'].invoke()
    assert calls[-1]==('global','pause')
    for geometry in ('930x620','1200x800','1040x700'):
        window.geometry(geometry);window.update()
        for name,control in view.watch_controls.items():
            box=view.watch_table.bbox(name,'control')
            if box:
                assert control.winfo_manager()=='place'
                assert control.winfo_x()+control.winfo_width()<=view.watch_table.winfo_width()


def test_switch_busy_fences_row_dispatch_and_reenables_after_cached_status(workspace):
    _,view,calls,window=workspace
    view.set_switch_busy(True);window.update()
    assert all(control.cget('state')=='disabled' for control in view.watch_controls.values())
    view.control_selected('pause','朋友')
    view.watch_controls['朋友'].invoke()
    assert not calls
    view.status['groups'][1]['mode']='auto';view.update_text()
    view.set_switch_busy(False);window.update()
    assert all(control.cget('state')=='normal' for control in view.watch_controls.values())
    assert all(control.winfo_height()>=control.winfo_reqheight() for control in view.watch_controls.values())
    view.watch_controls['朋友'].invoke()
    assert calls==[('朋友','pause')]
