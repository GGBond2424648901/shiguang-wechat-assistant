"""User actions must report actual completion and survive callback errors."""
import queue
import threading
import tkinter as tk


def test_hover_hint_shows_current_caption_without_focus_and_cancels_on_leave(native_root):
    from xiaomao.workspace_ui import HoverTip
    window=tk.Toplevel(native_root);window.geometry('300x100+3400+2000')
    try:
        button=tk.Button(window,text='最大化');button.pack();window.update()
        button.bind('<Return>',lambda _:button.configure(text='最大化'))
        tip=HoverTip(button,lambda:button.cget('text'),delay=0)
        before=window.focus_get()
        button.event_generate('<Enter>');window.update()
        assert tip.label.winfo_ismapped() and tip.label.cget('text')=='最大化'
        assert window.focus_get() is before
        button.event_generate('<Leave>');window.update()
        assert not tip.label.winfo_ismapped()
        button.configure(text='还原')
        button.event_generate('<Enter>');window.update()
        assert tip.label.cget('text')=='还原'
        button.focus_force();window.update();button.event_generate('<Return>');window.update()
        assert not tip.label.winfo_ismapped()  # keyboard invocation must not leave a stale caption
        button.event_generate('<Leave>');button.event_generate('<Enter>');button.destroy();window.update()
        assert not tip.label.winfo_exists()
    finally:window.destroy()


def test_native_rounding_waits_until_resize_settles_instead_of_repainting_each_size(native_root,monkeypatch):
    import time
    import win32gui
    from types import SimpleNamespace
    from xiaomao.theme import _install_window_rounding
    window=tk.Toplevel(native_root);window.withdraw()
    size=[900,620];calls=[]
    monkeypatch.setattr(win32gui,'GetWindowRect',lambda hwnd:(0,0,*size))
    monkeypatch.setattr(win32gui,'CreateRoundRectRgn',lambda *args:123)
    monkeypatch.setattr(win32gui,'SetWindowRgn',lambda hwnd,region,paint:calls.append((region,paint)))
    def pump(seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:window.update();time.sleep(.005)
    try:
        resized=_install_window_rounding(window,111);pump(.17);calls.clear()
        for width in (950,1000,1200,1080,930):
            size[0]=width;resized(SimpleNamespace(widget=window,width=width,height=620))
            pump(.025)
        # Clear the old clipping once; do not rebuild or force redraw in the drag loop.
        assert calls==[(0,False)]
        pump(.18)
        assert calls==[(0,False),(123,False)]
        resized(SimpleNamespace(widget=window,width=930,height=620));pump(.18)
        assert len(calls)==2
    finally:window.destroy()
from types import SimpleNamespace

from xiaomao.gui import Panel


def control_panel(native_root):
    p=Panel.__new__(Panel)
    p.root=native_root
    p.closing=p.exiting=False
    p.results=queue.Queue()
    p.mutation_lock=threading.RLock()
    p.pending_mutations=0
    p.notice=tk.StringVar(native_root)
    return p


def test_action_reports_progress_blocks_duplicate_and_waits_for_result(native_root):
    p=control_panel(native_root)
    started=threading.Event();release=threading.Event();calls=[]
    def save():
        calls.append('save');started.set();assert release.wait(2)
        return 7
    p.work(save,lambda value:p.notice.set(f'已保存 {value} 项'),label='保存设置')
    assert started.wait(1)
    assert '正在保存设置' in p.notice.get()
    p.work(save,label='保存设置')
    release.set()
    callback,value=p.results.get(timeout=2);callback(value)
    assert calls==['save'] and p.notice.get()=='已保存 7 项'


def test_action_failure_is_not_replaced_by_success_and_can_retry(native_root):
    p=control_panel(native_root)
    def fail():raise ValueError('请先选定会话')
    p.work(fail,label='保存设置')
    callback,value=p.results.get(timeout=2);callback(value)
    assert '请先选定会话' in p.notice.get() and '完成' not in p.notice.get()
    p.work(lambda:1,label='保存设置')
    callback,value=p.results.get(timeout=2);callback(value)
    assert '已完成' in p.notice.get()


def test_callback_failure_does_not_kill_the_ui_result_pump(native_root):
    p=control_panel(native_root);seen=[];timers=[]
    p.root=SimpleNamespace(after=lambda delay,cb:timers.append(cb))
    def bad(_):raise ValueError('当前页面已失效，请重试')
    p.results.put((bad,None));p.results.put((seen.append,'next'))
    p.drain()
    assert seen==['next'] and timers and '请重试' in p.notice.get()


def test_feedback_is_in_window_and_does_not_take_keyboard_focus(native_root,monkeypatch):
    from xiaomao.feedback_ui import FeedbackBar
    host=tk.Frame(native_root);host.place(x=0,y=0,width=800,height=500)
    value=tk.StringVar(host)
    bar=FeedbackBar(host);bar.watch(value)
    clock=FeedbackClock(bar,monkeypatch)
    before=native_root.focus_get()
    value.set('正在保存设置…');clock.advance(160)
    assert bar.kind=='busy'
    value.set('设置已保存');native_root.update_idletasks()
    assert bar.kind=='success' and bar.winfo_manager()=='place'
    value.set('保存失败，请检查连接');native_root.update_idletasks()
    assert bar.kind=='error' and bar.dismiss_job is None
    assert native_root.focus_get() is before
    value.set('');clock.advance(100)
    assert not bar.winfo_manager()
    host.destroy()


class FeedbackClock:
    """Replace only the widget's wall clock/timers; keep real Tk layout and controls."""
    def __init__(self,bar,monkeypatch):
        import xiaomao.feedback_ui as feedback_ui
        self.bar=bar;self.now=0.;self.jobs={};self.serial=0
        monkeypatch.setattr(feedback_ui,'monotonic',lambda:self.now/1000,raising=False)
        monkeypatch.setattr(bar,'after',self.after)
        monkeypatch.setattr(bar,'after_cancel',lambda job:self.jobs.pop(job,None))

    def after(self,delay,callback,*args):
        self.serial+=1;job=f'feedback-test-{self.serial}'
        self.jobs[job]=(self.now+(0 if delay=='idle' else delay),callback,args)
        return job

    def advance(self,milliseconds):
        target=self.now+milliseconds
        self.bar._root().update_idletasks()
        for _ in range(10000):
            ready=[(due,job) for job,(due,_,_) in self.jobs.items() if due<=target]
            if not ready:break
            due,job=min(ready);self.now=due
            _,callback,args=self.jobs.pop(job);callback(*args)
            self.bar._root().update_idletasks()
        else:raise AssertionError('feedback scheduled an endless timer loop')
        self.now=target
        self.bar._root().update_idletasks()


def feedback_bar(native_root,monkeypatch):
    from xiaomao.feedback_ui import FeedbackBar
    host=tk.Frame(native_root);host.place(x=0,y=0,width=800,height=500)
    native_root.update_idletasks()
    bar=FeedbackBar(host)
    return host,bar,FeedbackClock(bar,monkeypatch)


def test_feedback_slides_in_then_retracts_faster_without_unmapping_midway(native_root,monkeypatch):
    host,bar,clock=feedback_bar(native_root,monkeypatch)
    try:
        bar.show('设置已保存');clock.advance(0)
        outside=bar.winfo_x()
        clock.advance(80);middle=bar.winfo_x()
        clock.advance(80);inside=bar.winfo_x()
        assert outside>middle>inside>=0
        assert bar.winfo_x()+bar.winfo_width()<=800
        bar.dismiss();clock.advance(0)
        assert bar.winfo_manager()=='place' and bar.winfo_x()==inside
        clock.advance(48)
        assert bar.winfo_manager()=='place' and bar.winfo_x()>inside
        clock.advance(52)
        assert not bar.winfo_manager()
    finally:host.destroy()


def test_feedback_updates_in_place_and_reverses_an_unfinished_retract(native_root,monkeypatch):
    host,bar,clock=feedback_bar(native_root,monkeypatch)
    try:
        bar.show('正在保存设置…');clock.advance(160)
        inside=bar.winfo_x()
        bar.show('正在应用设置…');clock.advance(0)
        assert bar.winfo_x()==inside and bar.progress.winfo_manager()=='grid'
        bar.show('设置已保存');clock.advance(0)
        assert bar.winfo_x()==inside and not bar.progress.winfo_manager()
        bar.dismiss();clock.advance(40)
        retracting=bar.winfo_x()
        assert retracting>inside and bar.winfo_manager()=='place'
        bar.show('连接失败，请重试');clock.advance(0)
        assert bar.winfo_x()==retracting
        clock.advance(160)
        assert bar.winfo_x()==inside and bar.kind=='error' and bar.dismiss_job is None
        clock.advance(10000)
        assert bar.winfo_manager()=='place'
    finally:host.destroy()


def test_feedback_repeated_updates_do_not_restart_the_entrance(native_root,monkeypatch):
    host,bar,clock=feedback_bar(native_root,monkeypatch)
    try:
        bar.show('正在保存设置…');clock.advance(64)
        bar.show('正在应用设置…');clock.advance(64)
        bar.show('正在刷新设置…');clock.advance(32)
        assert 0<=bar.winfo_x()
        assert bar.winfo_x()+bar.winfo_width()<=800
        assert bar.progress.winfo_manager()=='grid'
    finally:host.destroy()


def test_feedback_success_and_info_auto_retract_after_three_seconds(native_root,monkeypatch):
    host,bar,clock=feedback_bar(native_root,monkeypatch)
    try:
        for text in ('设置已保存','请查看帮助说明'):
            bar.show(text);clock.advance(2990)
            assert bar.winfo_manager()=='place'
            clock.advance(300)
            assert not bar.winfo_manager()
    finally:host.destroy()


def test_feedback_new_busy_result_cancels_old_auto_dismiss_and_busy_persists(native_root,monkeypatch):
    host,bar,clock=feedback_bar(native_root,monkeypatch)
    try:
        bar.show('设置已保存');clock.advance(2800)
        bar.show('正在校准…');clock.advance(20000)
        assert bar.winfo_manager()=='place' and bar.kind=='busy'
        assert bar.dismiss_job is None and bar.progress.winfo_manager()=='grid'
    finally:host.destroy()


def test_feedback_resizes_inside_host_and_hides_echoed_keys(native_root,monkeypatch):
    host,bar,clock=feedback_bar(native_root,monkeypatch)
    try:
        bar.show('保存失败 sk-project_example-secret，请检查连接。'*10);clock.advance(160)
        assert 'sk-project_example-secret' not in bar.message.cget('text')
        assert '[密钥已隐藏]' in bar.message.cget('text')
        host.place_configure(width=260,height=220);native_root.update_idletasks();clock.advance(0)
        assert 0<=bar.winfo_x()<260
        assert bar.winfo_x()+bar.winfo_width()<=260
        assert 0<=bar.winfo_y()<220
        assert bar.winfo_y()+bar.winfo_height()<=220
        assert int(bar.message.cget('wraplength'))<260
    finally:host.destroy()


def test_feedback_destruction_cancels_motion_progress_dismiss_and_variable_traces(native_root,monkeypatch):
    host,bar,clock=feedback_bar(native_root,monkeypatch)
    value=tk.StringVar(host);bar.watch(value)
    value.set('正在保存设置…');clock.advance(32)
    host.destroy()
    assert not clock.jobs
    assert not value.trace_info()
    value.set('设置已保存');clock.advance(10000)


def test_feedback_destroyed_during_auto_retract_leaves_no_callbacks(native_root,monkeypatch):
    host,bar,clock=feedback_bar(native_root,monkeypatch)
    bar.show('设置已保存');clock.advance(3050)
    host.destroy()
    assert not clock.jobs
    clock.advance(10000)


def test_feedback_native_tcl_timers_are_cancelled_when_host_is_destroyed(native_root):
    from xiaomao.feedback_ui import FeedbackBar
    before=set(native_root.tk.splitlist(native_root.tk.call('after','info')))
    host=tk.Frame(native_root);host.place(x=0,y=0,width=800,height=500)
    bar=FeedbackBar(host)
    bar.show('正在保存设置…');native_root.update_idletasks()
    pending=set(native_root.tk.splitlist(native_root.tk.call('after','info')))-before
    assert pending
    host.destroy()
    assert not pending.intersection(native_root.tk.splitlist(native_root.tk.call('after','info')))
