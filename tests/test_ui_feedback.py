"""User actions must report actual completion and survive callback errors."""
import queue
import threading
import tkinter as tk
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


def test_feedback_is_in_window_and_does_not_take_keyboard_focus(native_root):
    from xiaomao.feedback_ui import FeedbackBar
    host=tk.Frame(native_root);host.pack()
    value=tk.StringVar(host)
    bar=FeedbackBar(host);bar.watch(value)
    before=native_root.focus_get()
    value.set('正在保存设置…');native_root.update_idletasks()
    assert bar.kind=='busy'
    value.set('设置已保存');native_root.update_idletasks()
    assert bar.kind=='success' and bar.winfo_manager()=='place'
    value.set('保存失败，请检查连接');native_root.update_idletasks()
    assert bar.kind=='error' and bar.dismiss_job is None
    assert native_root.focus_get() is before
    value.set('');assert not bar.winfo_manager()
    host.destroy()
