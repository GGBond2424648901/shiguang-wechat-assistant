"""In-window action feedback. Never activates a window or interrupts typing."""
import re
import tkinter as tk
from tkinter import ttk
from .workspace_ui import PAPER, GREEN, INK, MUTED, LINE, button, label


def feedback_kind(text):
    if re.search(r'失败|未完成|无法|错误|不支持|请先|请至少|不能为空|无效|未保存|未确认|不匹配|已取消|已变化|过期|检查|最多',text):
        return 'error'
    if re.search(r'^正在|识别中|保存中|应用中|当前图片返回后',text):return 'busy'
    if re.search(r'已|成功|完成|结束|正常|生效',text):return 'success'
    return 'info'


class FeedbackBar(tk.Frame):
    def __init__(self,parent):
        super().__init__(parent,bg=PAPER,highlightbackground=LINE,highlightthickness=1,padx=12,pady=10)
        self.kind='info';self.dismiss_job=None;self.traces=[]
        self.columnconfigure(1,weight=1)
        self.mark=label(self,'●',16,bold=True);self.mark.grid(row=0,column=0,rowspan=2,sticky='n',padx=(0,9))
        self.heading=label(self,size=11,bold=True);self.heading.grid(row=0,column=1,sticky='w')
        self.message=label(self,size=10,wraplength=310);self.message.grid(row=1,column=1,sticky='ew',pady=(3,0))
        button(self,'×',self.dismiss,compact=True).grid(row=0,column=2,sticky='ne',padx=(7,0))
        self.progress=ttk.Progressbar(self,mode='indeterminate')
        self.bind('<Destroy>',self._destroyed,add='+')

    def watch(self,var):
        self.traces.append((var,var.trace_add('write',lambda *_:self.show(var.get()))))

    def show(self,text,kind=None):
        if not text:return
        self.dismiss()
        self.kind=kind or feedback_kind(text)
        color={'error':'#A1472C','success':GREEN,'busy':'#865536','info':INK}[self.kind]
        self.mark.configure(text={'success':'✓','error':'!','busy':'◌','info':'●'}[self.kind],fg=color)
        self.heading.configure(text={'success':'操作已完成','error':'请查看操作结果','busy':'正在处理','info':'操作提示'}[self.kind],fg=color)
        # Do not show raw secrets even if a remote service echoes one in an error.
        text=re.sub(r'\bsk-[A-Za-z0-9_-]+','[密钥已隐藏]',str(text))
        self.message.configure(text=text)
        self.place(relx=1,x=-20,y=65,anchor='ne')
        self.lift()
        if self.kind=='busy':
            self.progress.grid(row=2,column=1,columnspan=2,sticky='ew',pady=(8,0));self.progress.start(25)
        elif self.kind!='error':self.dismiss_job=self.after(8000,self.dismiss)

    def dismiss(self):
        if self.dismiss_job:self.after_cancel(self.dismiss_job);self.dismiss_job=None
        self.progress.stop();self.progress.grid_remove();self.place_forget()

    def _destroyed(self,event):
        if event.widget is self:
            # Child widgets are already destroyed when the frame receives this event.
            if self.dismiss_job:self.after_cancel(self.dismiss_job);self.dismiss_job=None
            for var,trace in self.traces:
                try:var.trace_remove('write',trace)
                except tk.TclError:pass
