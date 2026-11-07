"""Reusable compact native schedule list/editor; persistence belongs to its host."""
from copy import deepcopy
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from .routines import LOCAL, MAX_ROUTINES, routine_preset, validate_routines
from .workspace_ui import button, card, label


INK, BG, PAPER = '#1B3044', '#F5F2EB', '#FFFFFF'
PEACH, MUTED, LINE = '#F6B18C', '#647482', '#DBE0E1'
FONT = 'Microsoft YaHei UI'
STATUS = {'planned': '等待到点', 'generating': '正在生成', 'queued': '等待安全发送',
          'sending': '等待发送回执', 'verified': '已发送并核对', 'partial': '部分确认，未重发',
          'unknown': '结果未知，未重发', 'missed': '已过时，未补发'}


class RoutinesEditor(tk.Frame):
    def __init__(self, parent, *, on_save=None):
        super().__init__(parent, bg=BG)
        self.on_save = on_save
        self.on_widgets = None
        self._rows, self._summaries = [], {}
        self._selected_id = ''
        self._new_draft = None
        self._usable = True
        self._row_actions = []
        self._row_variables = []
        self._row_titles = []
        self._row_title_width = 170
        self.switch_images=[]
        for enabled in (False,True):
            art=Image.new('RGBA',(84,44))
            pen=ImageDraw.Draw(art)
            pen.rounded_rectangle((0,0,83,43),radius=21,fill='#F39369' if enabled else '#A6ADB0')
            x=43 if enabled else 4
            pen.ellipse((x,4,x+35,39),fill=PAPER)
            self.switch_images.append(ImageTk.PhotoImage(art.resize((42,22),Image.Resampling.LANCZOS),master=self))
        self.actions, self.form_controls = [], []
        self.time = tk.StringVar(self, '09:00')
        self.intent = tk.StringVar(self)
        self.enabled = tk.BooleanVar(self, False)
        self.prefer_sticker = tk.BooleanVar(self, True)
        self.weekdays = [tk.BooleanVar(self, True) for _ in range(7)]
        self.notice = tk.StringVar(self, '日程仅用于当前会话，新增预设默认暂停。')
        self.next_time = tk.StringVar(self, '尚未启用日程')
        self.next_hint = tk.StringVar(self, '启用日程并保存后，由本机后台安排。')
        self.left, self.right = tk.Frame(self, bg=BG), tk.Frame(self, bg=BG)
        self._build_list()
        self._build_editor()
        self.bind('<Configure>', lambda event: self.arrange(event.width) if event.widget is self else None)
        self.arrange(900)
        self.set_values([])

    def _build_list(self):
        daily = card(self.left, pad=12)
        daily.pack(fill='x')
        header = tk.Frame(daily, bg=PAPER)
        header.pack(fill='x', pady=(0, 9))
        label(header, '每日关怀', size=15, bold=True).pack(side='left')
        add = button(header, '添加日程', self.new, primary=True, compact=True, icon_name='add')
        add.pack(side='right')
        self.actions.append(add)
        presets = tk.Frame(daily, bg=PAPER)
        presets.pack(fill='x', pady=(0, 8))
        label(presets, '快速添加', color=MUTED, size=9).pack(side='left', padx=(0, 6))
        for key, text in [('morning', '早安'), ('noon', '午间'), ('afternoon', '午后'), ('evening', '晚间')]:
            item = button(presets, text, lambda name=key: self.add_preset(name), compact=True)
            item.pack(side='left', padx=(0, 4))
            self.actions.append(item)
        self.rows_frame = tk.Frame(daily, bg=PAPER)
        self.rows_frame.pack(fill='x')
        hint = tk.Frame(daily, bg='#EDF5F8', padx=9, pady=8)
        hint.pack(fill='x', pady=(8, 0))
        self.background_hint = label(hint, '日程保存在本机后台，关闭面板继续执行。', color=INK, size=9)
        self.background_hint.pack(fill='x')
        recent = card(self.left, pad=12)
        recent.pack(fill='x', pady=(9, 0))
        label(recent, '最近执行', size=13, bold=True).pack(anchor='w', pady=(0, 7))
        self.history_view = ttk.Treeview(recent, columns=('when', 'status', 'intent'), show='headings', height=3)
        for key, text, width in [('when', '执行时间', 105), ('status', '发送状态', 135), ('intent', '关怀意图', 170)]:
            self.history_view.heading(key, text=text)
            self.history_view.column(key, width=width, minwidth=65, stretch=key == 'intent')
        self.history_view.pack(fill='x')
        self.history_empty = label(recent, '暂无执行记录', color=MUTED, size=9)
        self.history_empty.pack(anchor='w', pady=(5, 0))

    def _build_editor(self):
        form = card(self.right, pad=12)
        form.pack(fill='x')
        label(form, '编辑日程', size=15, bold=True).pack(anchor='w', pady=(0, 10))
        row = tk.Frame(form, bg=PAPER)
        row.pack(fill='x', pady=3)
        label(row, '时间', bold=True).pack(side='left', padx=(0, 10))
        clock = ttk.Entry(row, textvariable=self.time, width=7, font=(FONT, 14, 'bold'))
        clock.pack(side='left', ipady=2)
        self.form_controls.append(clock)
        label(row, '上海时间', color=MUTED, size=9).pack(side='left', padx=7)
        label(form, '重复', bold=True).pack(anchor='w', pady=(10, 5))
        days = tk.Frame(form, bg=PAPER)
        days.pack(fill='x')
        for index, (text, variable) in enumerate(zip('一二三四五六日', self.weekdays)):
            chip = tk.Checkbutton(days, text=text, variable=variable, indicatoron=False,
                                  bg=BG, fg=INK, activebackground=PEACH, selectcolor=PEACH,
                                  relief='flat', bd=0, padx=5, pady=4, font=(FONT, 10),
                                  highlightthickness=1, highlightbackground=LINE, cursor='hand2')
            chip.grid(row=0, column=index, sticky='ew', padx=(0, 3))
            days.columnconfigure(index, weight=1)
            self.form_controls.append(chip)
        label(form, '主题 / 关怀意图', bold=True).pack(anchor='w', pady=(10, 5))
        self.intent_box = tk.Text(form, height=4, width=1, wrap='word', bg=PAPER, fg=INK,
                                  font=(FONT, 10), relief='flat', padx=7, pady=6,
                                  insertbackground=INK, highlightthickness=1, highlightbackground=LINE)
        self.intent_box.pack(fill='x')
        self.form_controls.append(self.intent_box)
        self.intent_box.bind('<<Modified>>', self._intent_modified)
        self.intent.trace_add('write', self._intent_changed)
        for text, variable in [('启用此日程', self.enabled), ('配一张合适的大肥鱼表情', self.prefer_sticker)]:
            check = tk.Checkbutton(form, text=text, variable=variable, bg=PAPER, fg=INK,
                                   activebackground=PAPER, selectcolor=PEACH, bd=0, padx=0,
                                   font=(FONT, 10), anchor='w', cursor='hand2')
            check.pack(fill='x', pady=(8, 0))
            self.form_controls.append(check)
        row = tk.Frame(form, bg=PAPER)
        row.pack(fill='x', pady=(10, 5))
        apply = button(row, '应用到列表', self.apply_edit, compact=True)
        cancel = button(row, '取消编辑', self.cancel_edit, compact=True)
        remove = button(row, '删除', self.remove_selected, compact=True)
        for item in (apply, cancel, remove):
            item.pack(side='left', padx=(0, 4))
        self.actions.extend((apply, cancel, remove))
        self.save_button = button(form, '保存此会话日程', self._save, primary=True, icon_name='save')
        self.save_button.pack(fill='x', pady=(5, 0))
        self.actions.append(self.save_button)
        self.notice_label = label(form, textvariable=self.notice, size=9, color=MUTED)
        self.notice_label.pack(fill='x', pady=(8, 0))
        upcoming = card(self.right, pad=12)
        upcoming.pack(fill='x', pady=(9, 0))
        label(upcoming, '下次执行', size=13, bold=True).pack(anchor='w', pady=(0, 7))
        detail = tk.Frame(upcoming, bg='#FFF0E7', padx=10, pady=10)
        detail.pack(fill='x')
        label(detail, textvariable=self.next_time, size=17, bold=True).pack(anchor='w')
        self.next_hint_label = label(detail, textvariable=self.next_hint, size=9, color=MUTED)
        self.next_hint_label.pack(fill='x', pady=(5, 0))

    def arrange(self, width):
        wide = width >= 760
        self.columnconfigure(0, weight=2 if wide else 1, uniform='routines' if wide else '')
        self.columnconfigure(1, weight=1 if wide else 0, uniform='routines' if wide else '')
        self.left.grid(row=0, column=0, sticky='nsew', padx=(0, 9 if wide else 0))
        self.right.grid(row=0 if wide else 1, column=1 if wide else 0, sticky='nsew', pady=(0 if wide else 9, 0))
        right_width = max(215, width // 3 - 40) if wide else max(215, width - 46)
        self.notice_label.configure(wraplength=right_width)
        self.next_hint_label.configure(wraplength=max(195, right_width-18))
        self.background_hint.configure(wraplength=max(220, int(width * .64)-45 if wide else width-46))
        left_width=int((width-9)*2/3) if wide else width
        self._row_title_width=max(85,left_width-310)
        for title in self._row_titles:title.configure(wraplength=self._row_title_width)
        self.history_view.column('when',width=90)
        self.history_view.column('status',width=120)
        self.history_view.column('intent',width=max(90,left_width-250))

    def _intent_changed(self, *_):
        value = self.intent.get()
        if self.intent_box.get('1.0', 'end-1c') != value:
            prior = str(self.intent_box.cget('state'))
            self.intent_box.configure(state='normal')
            self.intent_box.delete('1.0', 'end')
            self.intent_box.insert('1.0', value)
            self.intent_box.edit_modified(False)
            self.intent_box.configure(state=prior)

    def _intent_modified(self, _=None):
        if self.intent_box.edit_modified():
            self.intent.set(self.intent_box.get('1.0', 'end-1c'))
            self.intent_box.edit_modified(False)

    def set_values(self, rows):
        validate_routines(rows)
        self._rows = deepcopy(rows)
        self._new_draft = None
        self._selected_id = self._rows[0]['id'] if self._rows else ''
        self._summaries = {}
        self.set_status([], [])
        self.cancel_edit()

    def _load_form(self, row):
        self.time.set(row['time'])
        self.intent.set(row['intent'])
        self.enabled.set(row['enabled'])
        self.prefer_sticker.set(row['prefer_sticker'])
        for index, variable in enumerate(self.weekdays):
            variable.set(index in row['weekdays'])
        self.set_enabled(self._usable)

    def edit(self, identity):
        if self._new_draft and self._new_draft['id']==identity:
            row=self._new_draft
        else:
            self._new_draft = None
            row = next((row for row in self._rows if row['id'] == identity), None)
        if row:
            self._selected_id = identity
            self._load_form(row)
            self._render_rows()

    def new(self):
        self._new(routine_preset('morning') | {'intent': '', 'prefer_sticker': False})

    def add_preset(self, name):
        self._new(routine_preset(name))

    def _new(self, row):
        if len(self._rows) >= MAX_ROUTINES:
            self.notice.set('每个会话最多设置 8 条定时关怀。')
            return
        self._new_draft = row
        self._selected_id = row['id']
        self._load_form(row)
        self.notice.set('新增日程默认暂停；保存后才会应用到本会话。')
        self._render_rows()

    def values(self):
        rows = deepcopy(self._rows)
        if self._selected_id:
            edited = dict(id=self._selected_id, time=self.time.get().strip(),
                          weekdays=[day for day, variable in enumerate(self.weekdays) if variable.get()],
                          enabled=bool(self.enabled.get()), intent=self.intent.get().strip(),
                          prefer_sticker=bool(self.prefer_sticker.get()))
            if self._new_draft is not None:
                rows.append(edited)
            else:
                rows = [edited if row['id'] == self._selected_id else row for row in rows]
        validate_routines(rows)
        return rows

    def apply_edit(self):
        try:
            self._rows = self.values()
        except ValueError as error:
            self.notice.set(str(error))
            return
        self._new_draft = None
        self._render_rows()
        self.notice.set('已应用到列表；点击“保存此会话日程”保存到后台。')

    def cancel_edit(self):
        self._new_draft = None
        if not any(row['id'] == self._selected_id for row in self._rows):
            self._selected_id = self._rows[0]['id'] if self._rows else ''
        row = next((row for row in self._rows if row['id'] == self._selected_id), None)
        self._load_form(row or routine_preset('morning') | {'intent': ''})
        self.notice.set('修改后保存，仅作用于当前会话。')
        self._render_rows()

    def remove_selected(self):
        self._rows = [row for row in self._rows if row['id'] != self._selected_id]
        self._selected_id = ''
        self.cancel_edit()
        self.notice.set('已从列表移除；保存后生效。')

    def _toggle(self, identity, value):
        if self._new_draft and self._new_draft['id'] == identity:
            self._new_draft['enabled'] = value
            self.enabled.set(value)
        else:
            for row in self._rows:
                if row['id'] == identity:
                    row['enabled'] = value
            if identity == self._selected_id:
                self.enabled.set(value)
        self.notice.set('日程开关已更改，保存后生效。')
        self._render_rows()

    def _render_rows(self):
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._row_actions, self._row_variables, self._row_titles = [], [], []
        rows = self._rows + ([self._new_draft] if self._new_draft else [])
        if not rows:
            label(self.rows_frame, '暂无日程\n从早安、午间、午后或晚间预设开始。', color=MUTED).pack(anchor='w', pady=15)
        for row in rows:
            active = row['id'] == self._selected_id
            surface = '#FFF2E8' if active else PAPER
            item = card(self.rows_frame, pad=9, bg=surface, border='#F1CDB8' if active else LINE)
            item.pack(fill='x', pady=(0, 7))
            item.columnconfigure(2, weight=1)
            hour = int(row['time'].split(':')[0])
            glyph = '☀' if hour < 11 else '☕' if hour < 15 else '☾' if hour >= 19 else '♡'
            label(item, glyph, size=19, color='#A86B44').grid(row=0, column=0, rowspan=2, padx=(0, 8))
            label(item, row['time'], size=13, bold=True).grid(row=0, column=1, rowspan=2, padx=(0, 10))
            title = row['intent'] or '新日程（待编辑）'
            heading=label(item, title[:18] + ('…' if len(title) > 18 else ''), bold=True,wraplength=self._row_title_width)
            heading.grid(row=0, column=2, sticky='w')
            self._row_titles.append(heading)
            days = '每天' if len(row['weekdays']) == 7 else '周' + '、'.join('一二三四五六日'[d] for d in row['weekdays'])
            label(item, days, color=MUTED, size=9).grid(row=1, column=2, sticky='w')
            variable = tk.BooleanVar(self, row['enabled'])
            self._row_variables.append(variable)
            toggle = tk.Checkbutton(item, text='已开启' if row['enabled'] else '已暂停', variable=variable,
                                    command=lambda identity=row['id'], var=variable: self._toggle(identity, var.get()),
                                    bg=surface, fg=INK, selectcolor=PEACH, activebackground=surface,
                                    font=(FONT, 9), padx=0, bd=0, cursor='hand2')
            toggle.configure(indicatoron=False,image=self.switch_images[0],selectimage=self.switch_images[1],
                             compound='left',selectcolor=surface,highlightthickness=0)
            toggle.grid(row=0, column=3, rowspan=2, padx=(6, 6))
            edit = button(item, '编辑', lambda identity=row['id']: self.edit(identity), compact=True)
            edit.grid(row=0, column=4, rowspan=2)
            self._row_actions.extend((toggle, edit))
        self.set_enabled(self._usable)
        if self.on_widgets:
            self.on_widgets(self.rows_frame)

    def set_status(self, summaries, history):
        self._summaries = {row['id']: row for row in summaries}
        due = min((row['next_due_at'] for row in summaries if row.get('enabled') and row.get('next_due_at')), default=0)
        self.next_time.set(datetime.fromtimestamp(due, LOCAL).strftime('%m月%d日 %H:%M') if due else '尚未安排下一次')
        self.next_hint.set('文字与表情分别核对发送结果；暂停和免打扰期间不发送。' if due else '启用日程并开启本会话主动聊天后生效。')
        for child in self.history_view.get_children():
            self.history_view.delete(child)
        records = list(reversed(history[-5:]))
        for row in records:
            when = datetime.fromtimestamp(row['due_at'], LOCAL).strftime('%m-%d %H:%M')
            self.history_view.insert('', 'end', values=(when, STATUS.get(row['status'], row['status']), row.get('definition', {}).get('intent', '')))
        self.history_empty.configure(text='' if records else '暂无执行记录')

    def set_enabled(self, usable):
        self._usable = usable
        for item in self.actions + self._row_actions:
            item.configure(state='normal' if usable else 'disabled')
        for item in self.form_controls:
            item.configure(state='normal' if usable and self._selected_id else 'disabled')

    def _save(self):
        if self.on_save:
            self.on_save()
