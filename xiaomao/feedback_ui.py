"""In-window action feedback. Never activates a window or interrupts typing."""
import re
import tkinter as tk
from time import monotonic
from tkinter import ttk
from .workspace_ui import PAPER, GREEN, INK, LINE, button, label


def feedback_kind(text):
    if re.search(r'失败|未完成|无法|错误|不支持|请先|请至少|不能为空|无效|未保存|未确认|不匹配|已取消|已变化|过期|检查|最多',text):
        return 'error'
    if re.search(r'^正在|识别中|保存中|应用中|当前图片返回后',text):return 'busy'
    if re.search(r'已|成功|完成|结束|正常|生效',text):return 'success'
    return 'info'


class FeedbackBar(tk.Frame):
    ENTER_MS=160
    EXIT_MS=100
    HOLD_MS=3000

    def __init__(self,parent):
        super().__init__(parent,bg=PAPER,highlightbackground=LINE,highlightthickness=1,padx=12,pady=10)
        self.kind='info';self.dismiss_job=None;self.traces=[]
        self._animation_job=self._layout_job=self._progress_job=None
        self._disposed=False;self._visible=False;self._reveal=0.
        self._motion_target=0.;self._motion_from=0.;self._motion_started=0.;self._motion_ms=0
        self.columnconfigure(1,weight=1)
        self.rowconfigure(1,weight=1)
        self.mark=label(self,'●',16,bold=True);self.mark.grid(row=0,column=0,rowspan=2,sticky='n',padx=(0,9))
        self.heading=label(self,size=11,bold=True);self.heading.grid(row=0,column=1,sticky='w')
        self.message=label(self,size=10,wraplength=310);self.message.grid(row=1,column=1,sticky='new',pady=(3,0))
        button(self,'×',self.dismiss,compact=True).grid(row=0,column=2,sticky='ne',padx=(7,0))
        self.progress=ttk.Progressbar(self,mode='indeterminate')
        self._parent_configure=parent.bind('<Configure>',self._resized,add='+')
        self.bind('<Destroy>',self._destroyed,add='+')

    def watch(self,var):
        if self._disposed:return
        self.traces.append((var,var.trace_add('write',lambda *_:self.show(var.get()))))

    def show(self,text,kind=None):
        if self._disposed:return
        if not text:
            self.dismiss()
            return
        self._cancel_job('dismiss_job')
        self.kind=kind or feedback_kind(text)
        color={'error':'#A1472C','success':GREEN,'busy':'#865536','info':INK}[self.kind]
        self.mark.configure(text={'success':'✓','error':'!','busy':'◌','info':'●'}[self.kind],fg=color)
        self.heading.configure(text={'success':'操作已完成','error':'请查看操作结果','busy':'正在处理','info':'操作提示'}[self.kind],fg=color)
        # Do not show raw secrets even if a remote service echoes one in an error.
        text=re.sub(r'\bsk-[A-Za-z0-9_-]+','[密钥已隐藏]',str(text))
        self.message.configure(text=text)
        if self.kind=='busy':
            self.progress.grid(row=2,column=1,columnspan=2,sticky='ew',pady=(8,0))
            if self._progress_job is None:self._pulse()
        else:
            self._cancel_job('_progress_job');self.progress.grid_remove()
            if self.kind!='error':self.dismiss_job=self.after(self.HOLD_MS,self.dismiss)
        if not self._visible:
            self._visible=True;self._reveal=0.
            self._position();self.lift()
        self._request_layout()
        self._transition(1.)

    def dismiss(self):
        if self._disposed:return
        self._cancel_job('dismiss_job');self._cancel_job('_progress_job')
        if self._visible:self._transition(0.)

    def _cancel_job(self,name):
        job=getattr(self,name)
        if job is not None:
            try:self.after_cancel(job)
            except tk.TclError:pass
            setattr(self,name,None)

    def _pulse(self):
        self._progress_job=None
        if self._disposed or self.kind!='busy':return
        # Own this timer too, so destroying a page never leaves a ttk timer behind.
        self.progress.step()
        self._progress_job=self.after(25,self._pulse)

    def _request_layout(self):
        if not self._disposed and self._visible and self._layout_job is None:
            self._layout_job=self.after_idle(self._layout)

    def _layout(self):
        self._layout_job=None
        if not self._disposed and self._visible:self._position()

    def _resized(self,event):
        if event.widget is self.master and self._visible and not self._disposed:
            self._position();self._request_layout()

    def _position(self):
        width=max(1,self.master.winfo_width());height=max(1,self.master.winfo_height())
        margin=min(20,(width-1)//2);vertical_margin=min(12,(height-1)//2)
        card_width=min(420,max(1,width-2*margin))
        wrap=max(1,card_width-100)
        if int(self.message.cget('wraplength'))!=wrap:
            self.message.configure(wraplength=wrap);self._request_layout()
        card_height=min(self.winfo_reqheight(),max(1,height-2*vertical_margin))
        top=min(65,max(vertical_margin,height-card_height-vertical_margin))
        # The parent's clipping supplies the reveal; a frame needs no extra window.
        right=width-margin+round((card_width+2*margin)*(1-self._reveal))
        self.place(x=right,y=top,width=card_width,height=card_height,anchor='ne')

    def _transition(self,target):
        if target==self._motion_target and self._animation_job is not None:return
        self._cancel_job('_animation_job')
        self._motion_target=target;self._motion_from=self._reveal
        self._motion_started=monotonic()
        duration=self.ENTER_MS if target else self.EXIT_MS
        self._motion_ms=max(1,round(duration*abs(target-self._reveal)))
        if self._reveal==target:
            if not target:self._hide()
            return
        self._animation_job=self.after(min(16,self._motion_ms),self._animate)

    def _animate(self):
        self._animation_job=None
        if self._disposed:return
        elapsed=max(0,(monotonic()-self._motion_started)*1000)
        fraction=min(1.,elapsed/self._motion_ms)
        eased=1-(1-fraction)**3 if self._motion_target else fraction*fraction
        self._reveal=self._motion_from+(self._motion_target-self._motion_from)*eased
        self._position()
        if fraction<1:
            remaining=max(1,round(self._motion_ms-elapsed))
            self._animation_job=self.after(min(16,remaining),self._animate)
        elif not self._motion_target:self._hide()

    def _hide(self):
        self._visible=False;self._reveal=0.
        self._cancel_job('_layout_job')
        self.place_forget();self.progress.grid_remove()

    def _destroyed(self,event):
        if event.widget is self:
            # Child widgets are already destroyed when the frame receives this event.
            self._disposed=True;self._visible=False
            for name in ('dismiss_job','_animation_job','_layout_job','_progress_job'):
                self._cancel_job(name)
            try:self.master.unbind('<Configure>',self._parent_configure)
            except tk.TclError:pass
            for var,trace in self.traces:
                try:var.trace_remove('write',trace)
                except tk.TclError:pass
            self.traces.clear()
