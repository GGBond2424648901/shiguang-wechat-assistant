import queue
import threading
from types import SimpleNamespace
import tkinter as tk
from xiaomao.gui import Panel


def test_action_completion_keeps_the_new_button_caption_and_disabled_state(native_root):
    from xiaomao.workspace_ui import button
    root=tk.Frame(native_root);control=button(root,'暂停此会话',lambda:None)
    panel=Panel.__new__(Panel)
    panel.root=native_root;panel.notice=tk.StringVar(native_root)
    panel.results=queue.Queue();panel.mutation_lock=threading.RLock()
    panel.pending_mutations=0;panel.exiting=False
    panel.show_error=lambda value:None
    try:
        panel.work(lambda:'paused',lambda _:control.configure(text='已暂停',state='disabled'),
                   label='暂停此会话',controls=(control,))
        callback,value=panel.results.get(timeout=2);callback(value)
        assert control.cget('text')=='已暂停'
        assert control.cget('state')=='disabled'
        assert not panel.active_actions
    finally:root.destroy()
