"""Compact inline numeric pacing editor shared with the runtime settings card."""
import tkinter as tk
import unicodedata

from .config import CONTINUATION_MIN, CONTINUATION_MAX, MAX_SENDS_PER_MINUTE, MAX_SENDS_PER_HOUR, validate_send_pacing
from .persona_ui import BG, FONT, INK, LINE, MUTED, PAPER, PEACH, _action
from .sessions_ui import RATE_FIELDS, parse_rate, rate_fields


class PacingEditor(tk.Frame):
    def __init__(self,parent,panel,cfg,close):
        super().__init__(parent,bg=PAPER,highlightbackground=LINE,highlightthickness=1)
        self.panel=panel
        self.close=close
        self.busy=False
        self.status=tk.StringVar(self,value='保存后用于后续发送；已有待发回复时请等本轮结束。')
        heading=tk.Frame(self,bg=BG);heading.pack(fill='x')
        tk.Label(heading,text='编辑发送节奏',bg=BG,fg=INK,font=(FONT,11,'bold'),anchor='w'
                 ).pack(fill='x',padx=10,pady=8)
        fields=tk.Frame(self,bg=PAPER);fields.pack(fill='x',padx=10,pady=(10,4))
        fields.columnconfigure((0,1),weight=1,uniform='pacing')
        initial=rate_fields((cfg.min_interval,cfg.hourly_limit,cfg.minute_limit,cfg.pacing_jitter))
        self.rate_values=[tk.StringVar(self,value=value) for value in initial]
        self.continuation_value = tk.StringVar(self, value=f'{cfg.continuation_interval:g}')
        self.entries=[]
        limits = ((3, 3600, .5, '3–3600 秒'), (0, 30, .5, '0–30 秒'),
                  (1, MAX_SENDS_PER_MINUTE, 1, f'1–{MAX_SENDS_PER_MINUTE} 条'),
                  (1, MAX_SENDS_PER_HOUR, 1, f'1–{MAX_SENDS_PER_HOUR} 条'),
                  (CONTINUATION_MIN, CONTINUATION_MAX, .5, f'{CONTINUATION_MIN:g}–{CONTINUATION_MAX:g} 秒'))
        names = (*RATE_FIELDS, '同轮续发间隔')
        variables = (*self.rate_values, self.continuation_value)
        for index,(title,var,(minimum,maximum,step,hint)) in enumerate(zip(names, variables, limits)):
            cell=tk.Frame(fields,bg=PAPER)
            cell.grid(row=index//2,column=index%2,sticky='ew',padx=(0,6) if index%2==0 else (6,0),pady=(0,8))
            tk.Label(cell,text=title+('（秒）' if index in (0, 1, 4) else '（条）'),bg=PAPER,fg=INK,
                     font=(FONT,9),anchor='w').pack(fill='x')
            entry=tk.Spinbox(cell,textvariable=var,from_=minimum,to=maximum,increment=step,
                             width=8,bg=PAPER,fg=INK,insertbackground=INK,buttonbackground=BG,
                             relief='flat',highlightthickness=1,highlightbackground=LINE,
                             highlightcolor=PEACH,font=(FONT,10))
            entry.pack(fill='x',ipady=3,pady=(4,2));self.entries.append(entry)
            entry.bind('<Return>',lambda event:self.save())
            tk.Label(cell,text='范围 '+hint,bg=PAPER,fg=MUTED,font=(FONT,8),anchor='w').pack(fill='x')
        self.feedback=tk.Label(self,textvariable=self.status,bg=PAPER,fg=MUTED,font=(FONT,9),anchor='w',justify='left')
        self.feedback.pack(fill='x',padx=10,pady=(0,8))
        self.bind('<Configure>',lambda event:self.feedback.configure(wraplength=max(160,event.width-24)))
        actions=tk.Frame(self,bg=PAPER);actions.pack(fill='x',padx=10,pady=(0,10))
        self.save_button=_action(actions,'保存并应用',self.save,True)
        self.save_button.pack(side='right')
        self.cancel_button=_action(actions,'取消',self.cancel)
        self.cancel_button.pack(side='right',padx=(0,6))

    def _set_busy(self,busy):
        self.busy=busy
        for widget in (*self.entries,self.save_button,self.cancel_button):
            widget.configure(state='disabled' if busy else 'normal')

    def cancel(self):
        if self.busy:return
        self.panel.notice.set('已取消发送节奏修改，原设置保持不变')
        self.close()

    def values(self):
        rate = parse_rate([value.get() for value in self.rate_values])
        try:
            continuation = float(unicodedata.normalize('NFKC', self.continuation_value.get()).strip())
        except ValueError:
            raise ValueError('同轮续发间隔请填写秒数') from None
        validate_send_pacing(*rate, continuation)
        return (*rate, continuation)

    def save(self):
        if self.busy:return
        try:rate=self.values()
        except ValueError as error:
            self.status.set(str(error));self.feedback.configure(fg='#9B4436');return
        self._set_busy(True)
        self.status.set('正在保存发送节奏…');self.feedback.configure(fg=MUTED)
        def saved():
            self.panel.notice.set('发送节奏已保存，后续发送使用新数值。')
            self.close()
        def failed(error):
            self._set_busy(False);self.status.set(error);self.feedback.configure(fg='#9B4436')
            self.panel.notice.set(error)
        self.panel.apply_pacing_settings(rate,saved,failed)
