"""Saved conversations and the read-only local contact picker."""
from collections import Counter
from decimal import Decimal
import re
import threading
import tkinter as tk
from tkinter import ttk
import unicodedata

from .config import Settings,validate_send_pacing
from .contacts import search_directory, visual_name_key
from .selection import load_directory
from .workspace_ui import BG, FONT, INK, LINE, MUTED, NAVY, PAPER, PEACH, button, card, glyph, label


RATES = {
    '活跃聊天 · 随机4–8秒 / 每分钟10条 / 每小时180条': (4, 180, 10, 4),
    '快速接话 · 随机5–12秒 / 每分钟6条 / 每小时60条': (5, 60, 6, 7),
    '日常聊天 · 随机10–17秒 / 每分钟6条 / 每小时60条': (10, 60, 6, 7),
    '保守模式 · 随机30–37秒 / 每小时6条': (30, 6, 6, 7),
}
MODES = [('natural', '自然参与'), ('all', '逐条回复'), ('mentions', '仅点名')]
KINDS = {'group': '群聊', 'private': '个人', 'service': '系统服务'}
CUSTOM_RATE = '自定义节奏'
RATE_FIELDS = ('最短间隔', '随机加时', '每分钟上限', '每小时上限')


def rate_fields(rate):
    minimum,hour,minute,jitter=rate
    return tuple(str(int(value)) if isinstance(value,float) and value.is_integer() else str(value)
                 for value in (minimum,jitter,minute,hour))


def parse_rate(values):
    parsed=[]
    for index, (title, raw) in enumerate(zip(RATE_FIELDS, values, strict=True)):
        text=unicodedata.normalize('NFKC',raw.strip())
        seconds=index<2
        if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)' if seconds else r'\d+',text) or len(text)>64:
            raise ValueError(f'{title}请填写'+('秒数' if seconds else '整数'))
        parsed.append(float(text) if seconds else int(text))
    minimum,jitter,minute,hour=parsed
    rate=(minimum,hour,minute,jitter)
    validate_send_pacing(*rate)
    return rate


def directory_scope(cfg):
    binding = cfg.binding
    return (cfg.memory_scope, cfg.group, (binding.pid, binding.hwnd) if binding else None)


def short_rate(description):
    return description.split(' / ')[0]


def separator(parent):
    tk.Frame(parent, bg=LINE, height=1).pack(fill='x', pady=8)


class SessionsPage(tk.Frame):
    def __init__(self, parent, panel):
        super().__init__(parent, bg=BG)
        self.panel = panel
        self.names, self.picked = [], {}
        self.baseline = None
        self.busy = False
        self.monitor_all = tk.BooleanVar(self)
        self.mode = tk.StringVar(self)
        self.rate = tk.StringVar(self)
        self.rate_values = [tk.StringVar(self) for _ in RATE_FIELDS]
        self.custom_draft = None
        self.rate_edited = False
        self.rate_error = ''
        self.sync_rate = False
        self.stickers = tk.BooleanVar(self)
        self.note = tk.StringVar(self)
        self.rates = {short_rate(desc): rate for desc, rate in RATES.items()}
        self.selected_name = None
        self.row_widgets = {}
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._scroll_changed)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.item = self.canvas.create_window(0, 0, anchor='nw', window=self.body)
        self.body.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._resize)
        self.saved = card(self.body, pad=14)
        self.rules = card(self.body, pad=14)
        label(self.saved, '已保存的会话', 16, bold=True).pack(anchor='w', pady=(0, 10))
        self.rows = tk.Frame(self.saved, bg=PAPER)
        self.rows.pack(fill='x', anchor='n')
        self.add = button(self.saved, '批量添加会话', panel.choose_contact, primary=True,icon_name='add')
        self.add.pack(side='bottom', anchor='w', pady=(12, 0))
        label(self.saved, '↑↓ 选行　Enter 设为主要　Delete 移除', 9, color=MUTED).pack(side='bottom', anchor='w', pady=(8, 0))
        label(self.rules, '回复规则', 16, bold=True).pack(anchor='w')
        separator(self.rules)
        self.widgets = []
        style = ttk.Style(self)
        style.configure('Session.TCheckbutton', font=(FONT, 12), padding=(2, 4))
        style.configure('Session.TRadiobutton', font=(FONT, 12), padding=(2, 2))
        style.configure('Rate.TEntry', padding=6)
        style.map('Rate.TEntry', bordercolor=[('invalid','#AE5947'),('focus','#488397')],
                  fieldbackground=[('invalid','#FFF5F1'),('focus','#F4FAFA')])
        monitor = ttk.Checkbutton(self.rules, text='同时监听已选会话', variable=self.monitor_all,
                                  style='Session.TCheckbutton')
        monitor.pack(anchor='w', pady=4)
        self.widgets.append(monitor)
        separator(self.rules)
        for key, name in MODES:
            radio = ttk.Radiobutton(self.rules, value=key, variable=self.mode, text=name,
                                    style='Session.TRadiobutton')
            radio.pack(anchor='w', pady=4)
            self.widgets.append(radio)
        separator(self.rules)
        label(self.rules, '发送节奏', 11, bold=True).pack(anchor='w')
        self.pacing = ttk.Combobox(self.rules, state='readonly', textvariable=self.rate, width=12)
        self.pacing.configure(values=[*self.rates,CUSTOM_RATE])
        self.pacing.pack(fill='x', pady=(4, 5))
        editor=tk.Frame(self.rules,bg=PAPER)
        editor.pack(fill='x')
        self.rate_inputs=[]
        for index,(name,var) in enumerate(zip(RATE_FIELDS,self.rate_values)):
            cell=tk.Frame(editor,bg=PAPER)
            col=index%2
            editor.columnconfigure(col,weight=1,uniform='rate')
            cell.grid(row=index//2,column=col,sticky='ew',padx=(0 if col==0 else 7,0),pady=1)
            label(cell,name+(' · 秒' if index<2 else ' · 条'),9,color=MUTED).pack(anchor='w')
            field=ttk.Entry(cell,textvariable=var,width=6,style='Rate.TEntry',font=(FONT,11))
            field.pack(fill='x',pady=(1,2))
            field.bind('<FocusIn>',lambda e:self._reveal(e.widget))
            self.rate_inputs.append(field)
            var.trace_add('write', lambda *_: self._field_changed())
        self.rate_note = label(self.rules, '', 9, color=MUTED)
        self.rate_note.pack(fill='x')
        self.rate.trace_add('write', lambda *_: self._profile_changed())
        separator(self.rules)
        sticker = ttk.Checkbutton(self.rules, text='启用情境表情', variable=self.stickers,
                                  style='Session.TCheckbutton')
        sticker.pack(anchor='w', pady=3)
        self.widgets.append(sticker)
        self.apply = button(self.rules, '保存并应用', self.save, primary=True,icon_name='save')
        self.apply.pack(side='bottom', fill='x', pady=(8, 0))
        self.message = label(self.rules, textvariable=self.note, size=9, color=MUTED)
        self.message.pack(side='bottom', fill='x', pady=(6, 0))
        self.wheel_tag = f'SessionScroll{self.winfo_id()}'
        self.bind_class(self.wheel_tag, '<MouseWheel>', self._wheel)
        self.bind_class(self.wheel_tag, '<Control-s>', self._keyboard_save)
        self._tag_scroll(self)
        self.bind('<Destroy>', self._destroyed)
        self.refresh_scope()

    def _scroll_changed(self, first, last):
        self.bar.set(first, last)
        if float(first) > .001 or float(last) < .999:
            if not self.bar.winfo_manager():
                self.bar.pack(side='right', fill='y')
        else:
            self.bar.pack_forget()

    def _tag_scroll(self, widget):
        if self.wheel_tag not in widget.bindtags():
            widget.bindtags((self.wheel_tag, *widget.bindtags()))
        for child in widget.winfo_children():
            self._tag_scroll(child)

    def _destroyed(self, event):
        if event.widget is self:
            self.unbind_class(self.wheel_tag, '<MouseWheel>')
            self.unbind_class(self.wheel_tag, '<Control-s>')

    def _keyboard_save(self,event):
        self.save()
        return 'break'

    def _reveal(self,widget):
        box=self.canvas.bbox('all')
        if not box:
            return
        top=widget.winfo_rooty()-self.canvas.winfo_rooty()
        bottom=top+widget.winfo_height()-self.canvas.winfo_height()
        if top < 0:
            delta=top-5
        elif bottom > 0:
            delta=bottom+5
        else:
            return
        if delta:
            self.canvas.yview_moveto(self.canvas.yview()[0]+delta/max(1,box[3]-box[1]))

    def _wheel(self, event):
        if self.bar.winfo_manager() and event.delta:
            self.canvas.yview_scroll((-1 if event.delta > 0 else 1) * max(1, abs(event.delta)//120)*3, 'units')
            return 'break'

    def _resize(self, event):
        width = max(1, event.width)
        self.canvas.itemconfigure(self.item, width=width)
        wide = width >= 645
        self.body.columnconfigure(0, weight=7 if wide else 1, uniform='cards' if wide else '')
        self.body.columnconfigure(1, weight=6 if wide else 0, uniform='cards' if wide else '')
        self.body.rowconfigure(0, minsize=max(0, event.height-3) if wide else 0)
        self.saved.grid(row=0, column=0, sticky='nsew', padx=(0, 10 if wide else 0))
        self.rules.grid(row=0 if wide else 1, column=1 if wide else 0, sticky='nsew', pady=(0 if wide else 8, 0))
        text_width = width*6//13-50 if wide else width-50
        self.message.configure(wraplength=max(150, text_width))
        self.rate_note.configure(wraplength=max(150, text_width))

    def _raw_rate(self):
        return tuple(v.get() for v in self.rate_values)

    def _set_rate(self, profile, values):
        self.sync_rate=True
        try:
            self.rate.set(profile)
            for var,value in zip(self.rate_values,values):
                var.set(value)
        finally:
            self.sync_rate=False
        self._rate_help()

    def _profile_changed(self):
        if self.sync_rate:
            return
        profile=self.rate.get()
        if profile==CUSTOM_RATE:
            values=self.custom_draft or self._raw_rate()
        elif profile in self.rates:
            values=rate_fields(self.rates[profile])
        else:
            self.rate_note.configure(text='请选择一个节奏或填写自定义参数',fg='#A14A35')
            return
        self.rate_edited=True
        self._set_rate(profile,values)

    def _field_changed(self):
        if self.sync_rate:
            return
        self.rate_edited=True
        self.custom_draft=self._raw_rate()
        self.sync_rate=True
        try:
            self.rate.set(CUSTOM_RATE)
        finally:
            self.sync_rate=False
        self._rate_help()

    def _rate_help(self):
        for field in self.rate_inputs:
            field.state(['!invalid'])
        try:
            rate=parse_rate(self._raw_rate())
        except ValueError as error:
            self.rate_error=str(error)
            self.rate_note.configure(text=self.rate_error,fg='#A14A35')
            for name,field in zip(RATE_FIELDS,self.rate_inputs):
                if str(error).startswith(name):
                    field.state(['invalid'])
            return
        if self.rate.get()==CUSTOM_RATE:
            self.rates[CUSTOM_RATE]=rate
        if self.rate_error and self.note.get()==self.rate_error:
            self.note.set('节奏已调整，保存后生效')
        self.rate_error=''
        minimum,jitter,minute,hour=rate_fields(rate)
        if not self.panel.settings.natural_pacing and not self.rate_edited:
            summary=f'当前固定间隔 {minimum} 秒，最近 60 分钟 ≤ {hour} 条。编辑节奏后启用分钟上限和随机加时。'
        else:
            maximum=format(Decimal(minimum)+Decimal(jitter),'f')
            summary=f'间隔 {minimum}–{maximum} 秒 · 最近 60 秒 ≤ {minute} 条 · 最近 60 分钟 ≤ {hour} 条。全部会话共用。'
        if self.rate_edited:
            summary='待保存 · '+summary
        self.rate_note.configure(text=summary,fg=MUTED)

    def on_show(self):
        self.refresh_scope()

    def refresh_scope(self):
        """Rebase local edits onto persisted settings when the active recipient changes."""
        cfg = self.panel.settings
        current_rate = (cfg.min_interval, cfg.hourly_limit, cfg.minute_limit, cfg.pacing_jitter)
        current = (tuple(cfg.saved_groups), cfg.group, cfg.monitor_all_groups, cfg.reply_mode,
                   current_rate, cfg.stickers_enabled, dict(cfg.conversation_targets),cfg.natural_pacing)
        if self.baseline == current:
            return
        before = self.baseline
        if before is None:
            self.names = list(current[0])
        else:
            old, primary = before[:2]
            account = cfg.conversation_targets.get(cfg.group, {}).get('account')
            removed = set(old) - set(self.names)
            self.names = [n for n in self.names if n in current[0] or n not in old]
            self.names.extend(n for n in current[0] if n not in removed and n not in self.names)
            for name, row in tuple(self.picked.items()):
                binding = cfg.conversation_targets.get(name)
                if (account and row.account != account) or (binding and binding != row.binding):
                    del self.picked[name]
                    if name not in current[0] and name in self.names:
                        self.names.remove(name)
            if cfg.group != primary:
                self.names = [cfg.group, *(n for n in self.names if n != cfg.group)]
        if before is None or self.monitor_all.get() == before[2]:
            self.monitor_all.set(cfg.monitor_all_groups)
        if before is None or self.mode.get() == before[3]:
            self.mode.set(cfg.reply_mode)
        if before is None or self.stickers.get() == before[5]:
            self.stickers.set(cfg.stickers_enabled)
        if before is None or not self.rate_edited:
            self.rate_edited=False
            profile=next((short_rate(name) for name,rate in RATES.items() if rate==current_rate),CUSTOM_RATE)
            values=rate_fields(current_rate)
            if profile==CUSTOM_RATE:
                self.custom_draft=values
            self._set_rate(profile,values)
        self.baseline = current
        self._render_rows()

    def _render_rows(self):
        focused=str(self.tk.call('focus'))
        had_focus=any(str(row)==focused for row in self.row_widgets.values())
        for child in self.rows.winfo_children():
            child.destroy()
        self.row_widgets.clear()
        if self.selected_name not in self.names:
            self.selected_name=next(iter(self.names),None)
        cfg=self.panel.settings
        for index,name in enumerate(self.names):
            binding=self.picked[name].binding if name in self.picked else cfg.conversation_targets.get(name,{})
            group=binding.get('kind','group')=='group'
            face='#FFF2E9' if index==0 else PAPER
            row=tk.Frame(self.rows,bg=face,takefocus=True,highlightthickness=2,
                         highlightbackground=LINE,highlightcolor='#427D94',padx=8,pady=6)
            row.pack(fill='x',pady=(0,6))
            row.columnconfigure(1,weight=1)
            self.row_widgets[name]=row
            avatar=tk.Label(row,bg=face,image=glyph(row,'people' if group else 'persona',NAVY,44,
                               disc='#E1EBF1' if group else '#F3E3D9'),bd=0)
            avatar.grid(row=0,column=0,rowspan=2,sticky='w',padx=(0,8))
            title=label(row,name,12,bold=True)
            title.grid(row=0,column=1,sticky='ew')
            role='群聊' if group else '个人'
            tags=[role]
            if index==0:tags.append('主会话')
            if name not in cfg.saved_groups:tags.append('待保存')
            elif cfg.binding_for(name):
                tags.append('独立窗口' if cfg.binding_for(name).window_kind=='detached' else '主窗口')
            meta=label(row,' · '.join(tags),9,color=MUTED)
            meta.grid(row=1,column=1,sticky='w')
            actions=tk.Frame(row,bg=face)
            actions.grid(row=0,column=2,rowspan=2,sticky='e',padx=(5,0))
            calibrate=button(actions,'校准',lambda n=name:self.panel.calibrate(n),compact=True)
            calibrate.pack(side='left',padx=(0,4))
            if self.busy or name not in cfg.saved_groups:calibrate.configure(state='disabled')
            if index:
                promote=button(actions,'设为主要',lambda n=name:self.promote(n),compact=True)
                promote.pack(side='left',padx=(0,4))
                if self.busy:promote.configure(state='disabled')
            remove=button(actions,'移除',lambda n=name:self.remove(n),compact=True)
            remove.pack(side='left')
            if self.busy or len(self.names)==1:remove.configure(state='disabled')
            row.bind('<Configure>',lambda e,w=title,a=actions:w.configure(
                wraplength=max(48,e.width-a.winfo_reqwidth()-85)))
            for widget in (row,title,avatar,meta):
                widget.configure(cursor='hand2')
                widget.bind('<Button-1>',lambda _,n=name:self._pick_row(n))
            row.bind('<FocusIn>',lambda _,n=name:self._pick_row(n,focus=False))
            row.bind('<Enter>',lambda _,n=name,r=row:r.configure(
                highlightbackground='#86A1AE' if n!=self.selected_name else '#688F9F'))
            row.bind('<Leave>',lambda _,n=name,r=row:r.configure(
                highlightbackground=LINE if n!=self.selected_name else '#8BA7B4'))
            row.bind('<Up>',lambda _,n=name:self._move_row(n,-1))
            row.bind('<Down>',lambda _,n=name:self._move_row(n,1))
            row.bind('<Return>',lambda _,n=name:self._row_action(n,self.promote))
            row.bind('<Delete>',lambda _,n=name:self._row_action(n,self.remove))
            self._tag_scroll(row)
        self._pick_row(self.selected_name,focus=had_focus)

    def _pick_row(self,name,*,focus=True):
        if name not in self.row_widgets:
            return
        self.selected_name=name
        for key,row in self.row_widgets.items():
            row.configure(highlightbackground='#8BA7B4' if key==name else LINE)
        if focus:
            self.row_widgets[name].focus_set()
            self._reveal(self.row_widgets[name])

    def _move_row(self,name,step):
        index=max(0,min(len(self.names)-1,self.names.index(name)+step))
        self._pick_row(self.names[index])
        return 'break'

    def _row_action(self,name,action):
        action(name)
        return 'break'

    def promote(self, name):
        if not self.busy and name in self.names:
            self.names = [name, *(n for n in self.names if n != name)]
            self.selected_name=name
            self.note.set('主要会话已调整，保存后生效')
            self._render_rows()

    def remove(self, name):
        if self.busy or name not in self.names:
            return
        if len(self.names) == 1:
            self.note.set('至少保留一个回复对象')
            return
        self.names.remove(name)
        self.picked.pop(name, None)
        self.note.set('已从草稿移除，保存后生效')
        self._render_rows()

    def add_contact(self, contact):
        return self.add_contacts([contact])

    def add_contacts(self, contacts,*,together=False):
        if self.busy:
            return False
        self.refresh_scope()
        cfg = self.panel.settings
        account = cfg.conversation_targets.get(cfg.group, {}).get('account')
        names,picked=list(self.names),dict(self.picked)
        if not contacts:return False
        for contact in contacts:
            if not contact.selectable or contact.kind not in ('group','private'):
                self.note.set(contact.reason or '该会话不能开启自动回复')
                return False
            account=account or contact.account
            known=cfg.conversation_targets.get(contact.name)
            previous=picked.get(contact.name)
            if (account!=contact.account or known and known!=contact.binding or
                    previous and previous.binding!=contact.binding):
                self.note.set('会话身份或登录账号已改变，请刷新目录')
                return False
            if any(n!=contact.name and visual_name_key(n)==visual_name_key(contact.name) for n in names):
                self.note.set('会话标题容易混淆，请先设置唯一备注名')
                return False
            if contact.name not in names:names.append(contact.name)
            picked[contact.name]=contact
        if len(names)>8:
            self.note.set('最多同时保存 8 个会话，请减少所勾选的会话')
            return False
        self.names,self.picked=names,picked
        self.selected_name=contacts[-1].name
        if together and len(names)>1:self.monitor_all.set(True)
        self.note.set(f'已加入草稿，点「保存并应用」后生效 · 共 {len(names)} 个会话')
        self._render_rows()
        self._pick_row(self.selected_name)
        return True

    def _busy(self, busy):
        self.busy = busy
        for widget in (self.add, self.apply, *self.widgets):
            widget.configure(state='disabled' if busy else 'normal')
        self.pacing.configure(state='disabled' if busy else 'readonly')
        for field in self.rate_inputs:
            field.configure(state='disabled' if busy else 'normal')
        self._render_rows()

    def save(self):
        if self.busy:
            return
        if getattr(self.panel, 'switch_busy', False):
            self.note.set('正在切换会话，请稍后再保存')
            return
        self.refresh_scope()
        if not 1 <= len(self.names) <= 8:
            self.note.set('请选择 1 至 8 个会话')
            return
        try:
            if self.rate.get() not in (*self.rates,CUSTOM_RATE):
                raise ValueError('请选择一个节奏或填写自定义参数')
            rate=parse_rate(self._raw_rate())
        except ValueError as error:
            self.note.set(str(error))
            for name,field in zip(RATE_FIELDS,self.rate_inputs):
                if str(error).startswith(name):
                    field.focus_set()
                    break
            return
        settings = (list(self.names), self.mode.get(), rate)
        picked = [self.picked[n] for n in self.names if n in self.picked]
        self._busy(True)
        self.note.set('正在应用会话与回复规则…')

        def failed(error):
            if self.winfo_exists():
                self._busy(False)
                self.note.set(error)

        def done():
            if self.winfo_exists():
                self.baseline = None
                self.picked.clear()
                self.refresh_scope()
                self._busy(False)
                self.note.set('会话与回复规则已应用')

        self.panel.apply_group_settings(*settings, done, failed, monitor_all=self.monitor_all.get(),
                                       stickers_enabled=self.stickers.get(), contacts=picked,
                                       keep_pacing=not self.rate_edited)


class DirectoryPage(tk.Frame):
    FILTERS = {'全部': 'all', '群聊': 'group', '个人': 'private', '系统': 'service'}

    def __init__(self, parent, panel):
        super().__init__(parent, bg=BG)
        self.panel = panel
        self.rows, self.visible = [], []
        self.title_counts = Counter()
        self.cancel = None
        self.scope = None
        self.active = False
        self.loading = False
        self.error = ''
        self.selected_key = None
        self.checked={}
        self.hovered = None
        self.search_job = None
        self.query = tk.StringVar(self)
        self.kind = tk.StringVar(self, '全部')
        self.note = tk.StringVar(self)
        self.batch_info=tk.StringVar(self)
        button(self, '返回会话', lambda: panel.view.select('sessions'), compact=True,icon_name='back').pack(anchor='w', pady=(0, 8))
        area = card(self, pad=12)
        area.pack(fill='both', expand=True)
        search = tk.Frame(area, bg=PAPER)
        search.pack(fill='x', pady=(2, 10))
        self.searchbox = tk.Frame(search, bg=PAPER, highlightthickness=1, highlightbackground=LINE)
        label(self.searchbox, '搜索', 10, color=MUTED).pack(side='left', padx=6)
        self.entry = tk.Entry(self.searchbox, textvariable=self.query, bd=0, relief='flat',
                              font=(FONT, 11), bg=PAPER, fg=INK, insertbackground=INK, width=10)
        self.entry.pack(side='left', fill='x', expand=True, padx=5, pady=5)
        self.entry.bind('<FocusIn>',lambda _:self.searchbox.configure(highlightbackground='#427D94'))
        self.entry.bind('<FocusOut>',lambda _:self.searchbox.configure(highlightbackground=LINE))
        self.entry.bind('<Return>',self._first_result)
        self.entry.bind('<Down>',self._first_result)
        self.entry.bind('<Escape>',self._clear_search)
        self.placeholder = label(self.entry, '昵称、备注或微信号', 10, color=MUTED, cursor='xterm')
        self.placeholder.bind('<ButtonPress-1>', lambda _: self.entry.focus_set())
        filters = self.filterbar = tk.Frame(search, bg=PAPER)
        self.filter_buttons = {}
        for name in self.FILTERS:
            b = button(filters, name, lambda value=name: self.kind.set(value), compact=True)
            b.pack(side='left', padx=(3, 0))
            self.filter_buttons[name] = b
        self.searchbox.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        search.columnconfigure(0, weight=1)
        filters.grid(row=0, column=1)
        self.note_label = label(area, textvariable=self.note, size=9, color=MUTED)
        self.note_label.pack(side='bottom', fill='x', pady=(5, 0))
        self.batch_label=label(area,textvariable=self.batch_info,size=10,bold=True)
        self.batch_label.pack(fill='x',pady=(0,6))
        self.actions = tk.Frame(area, bg=PAPER)
        self.actions.pack(side='bottom', anchor='e', pady=(7, 0))
        self.refresh_button = button(self.actions, '刷新', self.refresh, compact=True,icon_name='refresh')
        self.current_button = button(self.actions, '使用微信当前会话', self.use_current, compact=True)
        self.add = button(self.actions, '加入监听列表', self.stage, primary=True,compact=True,icon_name='add')
        self.choose = button(self.actions, '仅开启此会话', self.select, compact=True)
        self.buttons = [self.refresh_button, self.current_button, self.add, self.choose]
        for item in self.buttons:
            item.configure(font=(FONT, 10, 'bold' if item is self.choose else 'normal'), pady=6)
        frame = tk.Frame(area, bg=PAPER)
        frame.pack(fill='both', expand=True)
        style = ttk.Style(self)
        style.configure('Directory.Treeview', rowheight=46, font=(FONT, 11),
                        foreground=INK, background=PAPER, fieldbackground=PAPER, relief='flat', borderwidth=0)
        style.configure('Directory.Treeview.Heading', font=(FONT, 11, 'bold'), padding=(6, 6),
                        relief='flat', borderwidth=0, background='#EFF2F2', foreground=INK)
        style.map('Directory.Treeview', background=[('selected', '#E2EDF9')], foreground=[('selected', INK)])
        self.list = ttk.Treeview(frame, columns=('pick', 'kind', 'name', 'nick', 'status'), show='headings',
                                 selectmode='browse', height=3, style='Directory.Treeview')
        for key, heading, width in (('pick', '多选', 48), ('kind', '类型', 65), ('name', '名称', 190),
                                    ('nick', '昵称', 160), ('status', '状态', 110)):
            self.list.heading(key, text=heading)
            self.list.column(key, anchor='center' if key == 'pick' else 'w', width=width, minwidth=24)
        self.list.tag_configure('disabled', foreground=MUTED)
        self.list.tag_configure('hover', background='#F0F6F8')
        scrollbar = ttk.Scrollbar(frame, command=self.list.yview)
        self.list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.list.pack(side='left', fill='both', expand=True)
        self.empty_note=label(frame,'',size=12,color=MUTED)
        self.query.trace_add('write', lambda *_: self._search_changed())
        self.kind.trace_add('write', lambda *_: self.render())
        self.list.bind('<<TreeviewSelect>>', lambda _: self._selection())
        self.list.bind('<Return>', self._stage_key)
        self.list.bind('<Button-1>',self._click_checkbox)
        self.list.bind('<space>',self._space_checkbox)
        self.list.bind('<Motion>', self._hover_row)
        self.list.bind('<Leave>',lambda _:self._hover_row(None))
        self.bind('<Configure>', self._resize)
        self.kb_tag=f'DirectoryKeys{self.winfo_id()}'
        self.bind_class(self.kb_tag,'<Control-f>',self._focus_search)
        self.bind_class(self.kb_tag,'<F5>',self._refresh_key)
        pending=[self]
        while pending:
            widget=pending.pop()
            widget.bindtags((self.kb_tag,*widget.bindtags()))
            pending.extend(widget.winfo_children())
        self.bind('<Destroy>',self._destroyed)
        self.render()

    def _destroyed(self,event):
        if event.widget is self:
            self.on_hide()
            self.unbind_class(self.kb_tag,'<Control-f>')
            self.unbind_class(self.kb_tag,'<F5>')

    def _stage_key(self,event):
        self.stage()
        return 'break'

    def _click_checkbox(self,event):
        if self.list.identify_region(event.x,event.y)!='cell':return
        iid=self.list.identify_row(event.y)
        if not iid:return
        if self.search_job:
            self.render()
            return 'break'
        self.list.focus_set()
        self.list.selection_set(iid);self.list.focus(iid)
        self.toggle_checked(self.visible[int(iid)])
        return 'break'

    def _space_checkbox(self,_):
        row=self._checked()
        if row:self.toggle_checked(row)
        return 'break'

    def toggle_checked(self,row):
        if not self.active or self.loading or not self._eligible(row):return
        key=(row.account,row.id)
        if key in self.checked:del self.checked[key]
        else:
            if len(self.checked)>=8:
                self.note.set('最多同时监听 8 个会话')
                return
            self.checked[key]=row
        self._selection()

    def _focus_search(self,event):
        self.entry.focus_set()
        self.entry.selection_range(0,'end')
        return 'break'

    def _refresh_key(self,event):
        self.refresh()
        return 'break'

    def _clear_search(self,event):
        self.query.set('')
        return 'break'

    def _search_changed(self):
        if self.search_job:
            self.after_cancel(self.search_job)
            self.search_job=None
        if len(self.rows)>500:
            self.search_job=self.after(110,self.render)
        else:
            self.render()

    def _first_result(self,event):
        if self.search_job:
            self.after_cancel(self.search_job)
            self.search_job=None
            self.render()
        for index,row in enumerate(self.visible):
            if self._eligible(row):
                iid=str(index)
                self.list.selection_set(iid)
                self.list.focus(iid)
                self.list.see(iid)
                self.list.focus_set()
                break
        return 'break'

    def _hover_row(self,event):
        iid=self.list.identify_row(event.y) if event else ''
        if iid==self.hovered:
            return
        if self.hovered and self.list.exists(self.hovered):
            self.list.item(self.hovered,tags=[t for t in self.list.item(self.hovered,'tags') if t!='hover'])
        self.hovered=iid
        allowed=bool(iid and self._eligible(self.visible[int(iid)]))
        if allowed:
            self.list.item(iid,tags=(*self.list.item(iid,'tags'),'hover'))
        self.list.configure(cursor='hand2' if allowed else '')

    def _resize(self, event):
        available = event.width - 30
        if available < 580:
            self.filterbar.grid(row=1, column=0, sticky='w', pady=(4, 0))
        else:
            self.filterbar.grid(row=0, column=1, sticky='e', pady=0)
        cols = len(self.buttons) if sum(b.winfo_reqwidth()+5 for b in self.buttons) < available else 2
        for idx, b in enumerate(self.buttons):
            b.grid(row=idx//cols, column=idx%cols, padx=(0, 4), pady=2, sticky='ew')
        self.note_label.configure(wraplength=max(140, available))
        self.batch_label.configure(wraplength=max(140, available))
        for key, fraction in [('pick', .055), ('kind', .14), ('name', .28), ('nick', .27), ('status', .25)]:
            self.list.column(key, width=max(24, int((available-18)*fraction)))

    def on_show(self):
        self.active = True
        self.refresh()

    def on_hide(self):
        self.active = False
        self.loading = False
        if self.cancel:
            self.cancel.set()
        self.cancel = None
        self.rows, self.visible = [], []
        self.title_counts.clear()
        self.scope = None
        self.selected_key=None
        self.checked.clear()
        if self.search_job:
            self.after_cancel(self.search_job)
            self.search_job=None
        if self.list.winfo_exists():
            self.list.delete(*self.list.get_children())

    def refresh_scope(self):
        if self.active and self.scope != directory_scope(self.panel.settings):
            self.refresh()

    def refresh(self):
        if not self.active:
            return
        if self.cancel:
            self.cancel.set()
        ticket = self.cancel = threading.Event()
        scope=directory_scope(self.panel.settings)
        if self.scope!=scope:
            self.selected_key=None
            self.checked.clear()
        self.scope=scope
        self.rows = []
        self.title_counts.clear()
        self.loading, self.error = True, ''
        self.render()

        def live():
            if not self.active or self.cancel is not ticket or ticket.is_set():
                return False
            if scope != directory_scope(self.panel.settings):
                self.refresh()
                return False
            return True

        def read():
            if ticket.is_set() or directory_scope(Settings.load(self.panel.directory)) != scope:
                raise ValueError('会话已变更，请刷新目录')
            rows = load_directory(self.panel.directory, cancelled=ticket)
            if ticket.is_set() or directory_scope(Settings.load(self.panel.directory)) != scope:
                raise ValueError('读取期间会话已变更，请刷新目录')
            return rows

        def done(rows):
            if live():
                self.loading = False
                self.rows = rows
                self.title_counts = Counter(visual_name_key(row.name) for row in rows)
                valid={(r.account,r.id):r for r in rows if self._eligible(r)}
                if any(valid.get(k)!=r for k,r in self.checked.items()):
                    self.checked.clear()
                    self.error='联系人身份已更新，请重新勾选会话'
                self.render()

        def failed(error):
            if live():
                self.loading, self.error = False, error
                self.render()

        self.panel.work(read, done, failed, mutates=False)

    def render(self):
        if self.search_job:
            self.after_cancel(self.search_job)
            self.search_job=None
        if self.query.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=0, y=0, relheight=1)
        selected_key = self.kind.get()
        for name, b in self.filter_buttons.items():
            b.configure(bg=PEACH if name == selected_key else '#FAFBFA',
                        font=(FONT,10,'bold' if name==selected_key else 'normal'))
        self.visible = search_directory(self.rows, self.query.get(), self.FILTERS[selected_key])
        self.list.delete(*self.list.get_children())
        self.hovered=None
        self.list.configure(cursor='')
        for idx, row in enumerate(self.visible):
            allowed = self._eligible(row)
            status = '可监听' if allowed else '系统服务' if row.kind == 'service' else '无法选择'
            mark='☑' if (row.account,row.id) in self.checked else '☐' if allowed else '—'
            self.list.insert('', 'end', iid=str(idx), values=(mark, KINDS.get(row.kind, '其他'), row.name,
                            row.nickname, status), tags=() if allowed else ('disabled',))
            if allowed and (row.account,row.id,row.kind,row.name)==self.selected_key:
                self.list.selection_set(str(idx))
                self.list.focus(str(idx))
                self.list.see(str(idx))
        if self.loading:
            self.note.set('正在读取本机联系人目录…')
        elif self.error:
            self.note.set(self.error)
        elif not self.visible and self.rows:
            self.note.set('没有匹配的会话，可以更换分类或搜索词')
        else:
            self.note.set(f'显示 {len(self.visible)} / {len(self.rows)} 个联系人')
        if self.visible:
            self.empty_note.place_forget()
        else:
            empty='正在读取联系人…' if self.loading else '目录暂时无法读取' if self.error else '没有匹配的会话'
            self.empty_note.configure(text=empty)
            self.empty_note.place(relx=.5,rely=.53,anchor='center')
        self.marked = None
        self._selection()

    def _eligible(self, row):
        return row.selectable and row.kind in ('private', 'group') and self.title_counts[visual_name_key(row.name)] == 1

    def _selected(self):
        selection = self.list.selection()
        if not selection:
            return None
        index = int(selection[0])
        return self.visible[index] if 0 <= index < len(self.visible) else None

    def _selection(self):
        row = self._selected()
        enabled = self.active and row and self._eligible(row)
        selected = self.list.selection()
        iid = selected[0] if selected else None
        if iid and not enabled:
            self.list.selection_remove(iid)
            iid = None
            self.selected_key=None
        if iid and row:
            self.list.set(iid,'pick','☑' if (row.account,row.id) in self.checked else '☐')
        count=len(self.checked)
        self.batch_info.set('已勾选 '+str(count)+' / 8　'+ '、'.join(r.name for r in self.checked.values()) if count
                            else '点击会话行可多选群聊和个人，搜索或切换分类会保留勾选')
        self.add.configure(text=f'加入监听列表（{count}）' if count else '加入监听列表',
                           state='normal' if self.active and not self.loading and (enabled or count) else 'disabled')
        self.choose.configure(state='normal' if enabled and (not count or
                              count==1 and (row.account,row.id) in self.checked) else 'disabled')
        if row and not enabled:
            self.note.set(row.reason or '该联系人身份存在歧义，不能设为回复对象')
        elif row:
            self.selected_key=(row.account,row.id,row.kind,row.name)
            self.note.set(f'{row.name} · {KINDS[row.kind]}　｜　空格勾选，Enter 加入列表')

    def _checked(self):
        if getattr(self.panel, 'switch_busy', False):
            self.note.set('正在切换会话，请稍后重试')
            return None
        if not self.active or self.scope != directory_scope(self.panel.settings):
            self.refresh_scope()
            return None
        if self.search_job:
            self.render()
        row = self._selected()
        if not row or not self._eligible(row):
            return None
        return row

    def stage(self):
        if not self.active or self.loading or getattr(self.panel,'switch_busy',False):return
        if self.scope!=directory_scope(self.panel.settings):
            self.refresh_scope()
            return
        row=self._checked()
        contacts=list(self.checked.values()) or ([row] if row else [])
        if not contacts:return
        sessions=self.panel.view.pages.get('sessions')
        together=bool(self.checked)
        if sessions is None:
            self.panel.view.select('sessions')
            sessions=self.panel.view.pages['sessions']
        if sessions.add_contacts(contacts,together=together):
            self.panel.view.select('sessions')
        else:self.note.set(sessions.note.get())

    def select(self):
        row = self._checked()
        if row and (not self.checked or len(self.checked)==1 and (row.account,row.id) in self.checked) and not getattr(self.panel,'switch_busy',False):
            self.panel.select_contact(row)

    def use_current(self):
        if self.active and not getattr(self.panel, 'switch_busy', False):
            self.panel.use_current_group()
