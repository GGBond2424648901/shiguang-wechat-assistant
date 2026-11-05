"""Conversation-scoped companion controls embedded in the main window."""
from dataclasses import asdict
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk

from .companion import CompanionPreferences
from .companion_store import CompanionStore
from .config import Settings
from .memory import ConversationMemory
from .ui_labels import member_label, memory_text
from .workspace_ui import button, card


INK, BG, PAPER = '#1B3044', '#F5F2EB', '#FFFFFF'
PEACH, MUTED, LINE = '#F6B18C', '#647482', '#DBE0E1'
FONT = 'Microsoft YaHei UI'


def label(parent, text='', *, size=10, color=INK, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
                    font=(FONT, size, 'bold' if bold else 'normal'),
                    anchor='w', justify='left', **kwargs)


def textbox(parent, height):
    return tk.Text(parent, height=height, width=1, bg=PAPER, fg=INK,
                   insertbackground=INK, selectbackground=PEACH, wrap='word',
                   relief='flat', highlightthickness=1, highlightcolor=MUTED,
                   highlightbackground=LINE, padx=9, pady=6, font=(FONT, 10))


def entry(parent, variable, width=14):
    return tk.Entry(parent, textvariable=variable, width=width, bg=PAPER, fg=INK,
                    insertbackground=INK, relief='flat', highlightthickness=1,
                    highlightbackground=LINE, highlightcolor=MUTED, font=(FONT, 10))


def memory_details(cfg):
    notes = CompanionStore(cfg.runtime, cfg.memory_scope)
    memory = ConversationMemory(cfg.runtime, cfg.memory_scope)
    people = {}
    facts = []
    for fact in notes.data['facts'][-30:]:
        speaker = member_label(fact['speaker'], people, '共同话题')
        facts.append(f'{speaker}：{fact["quote"]}')
    diaries = [day['day'] + '\n' + memory_text(day['text'], people)
               for day in reversed(notes.data['diary'][-7:])]
    return (len(memory.messages), len(notes.data['facts']), len(notes.data['diary']),
            memory_text(memory.summary, people), facts, diaries)


class CompanionPage(tk.Frame):
    """Mount as an ordinary Frame; the host calls on_show/on_hide on navigation."""

    def __init__(self, parent, panel):
        super().__init__(parent, bg=BG)
        self.panel = panel
        self.cfg = None
        self.active = self.ready = self.busy = False
        self.tab = 'policy'
        self.cancel = threading.Event()
        self.poll_job = None
        self.baseline_state = None
        self.pending_load = None
        self.feedback = tk.StringVar(self)
        self.live = tk.StringVar(self)
        self.preview_text = tk.StringVar(self, '尚未生成预览')
        self.queue_size = tk.StringVar(self, '—')
        self.scope_name = tk.StringVar(self)
        self.enabled = tk.BooleanVar(self)
        self.followup = tk.BooleanVar(self)
        self.idle = tk.BooleanVar(self)
        self.limit = tk.StringVar(self)
        self.start = tk.StringVar(self)
        self.end = tk.StringVar(self)
        self.interests = tk.StringVar(self)
        self.metrics = [tk.StringVar(self, '—') for _ in range(3)]
        self.action_buttons = []

        banner = tk.Frame(self, bg=INK)
        banner.pack(fill='x')
        label(banner, '当前会话', color=PAPER, bold=True).pack(side='left', padx=(18, 12), pady=10)
        self.scope_label = label(banner, textvariable=self.scope_name, size=14, bold=True, color=PAPER)
        self.scope_label.pack(side='left', fill='x', expand=True, pady=10)
        wave = tk.Canvas(banner, bg=INK, width=130, height=35, highlightthickness=0)
        wave.pack(side='right', padx=(2, 12))
        for shift, color in ((0, '#52718A'), (5, '#7896A7'), (9, '#426078')):
            wave.create_line(1, 28+shift, 23, 20+shift, 50, 30+shift, 79, 20+shift,
                             103, 3+shift, 130, 4+shift, smooth=True, fill=color)
        top = tk.Frame(self, bg=BG)
        top.pack(fill='x', pady=(11, 11))
        self.tabs = {}
        for key, name in [('policy', '陪伴策略'), ('memory', '会话记忆')]:
            tab = button(top, name, lambda k=key: self.select(k),
                         icon_name='companion' if key=='policy' else 'list')
            tab.pack(side='left', padx=(0, 8))
            self.tabs[key] = tab
        holder = tk.Frame(self, bg=BG)
        holder.pack(fill='both', expand=True)
        scroll = ttk.Scrollbar(holder)
        def update_scroll(first, last):
            scroll.set(first, last)
            if float(first) > .001 or float(last) < .999:
                if not scroll.winfo_manager():
                    scroll.pack(side='right', fill='y')
            else:
                scroll.pack_forget()
        self.canvas = tk.Canvas(holder, bg=BG, bd=0, highlightthickness=0,
                                yscrollcommand=update_scroll)
        self.canvas.pack(side='left', fill='both', expand=True)
        scroll.configure(command=self.canvas.yview)
        self.content = tk.Frame(self.canvas, bg=BG)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.canvas.bind('<Configure>', self._resize)
        self.content.bind('<Configure>', lambda _: self.canvas.configure(
            scrollregion=self.canvas.bbox('all')))
        self.policy = tk.Frame(self.content, bg=BG)
        self.memory = card(self.content,pad=12)
        self._build_policy()
        self._build_memory()
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill='x', pady=(6, 0))
        tk.Frame(footer, bg=LINE, height=1).pack(fill='x')
        self.message = label(footer, textvariable=self.feedback, color=MUTED)
        self.message.pack(fill='x', pady=4)
        self.wheel_tag = f'CompanionScroll{self.winfo_id()}'
        self.bind_class(self.wheel_tag, '<MouseWheel>', self._wheel)
        self._bind_wheel(self)
        self.bind('<Destroy>', self._destroyed, add='+')
        self.select('policy')

    def _resize(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)
        width = max(100, event.width - 16)
        self.scope_label.configure(wraplength=max(100, width-260))
        self.message.configure(wraplength=width)
        side_by_side = width >= 635
        self.policy.columnconfigure(0, weight=3 if side_by_side else 1, uniform='cards' if side_by_side else '')
        self.policy.columnconfigure(1, weight=2 if side_by_side else 0, uniform='cards' if side_by_side else '')
        self.policy.rowconfigure(0, minsize=max(0, event.height-3) if side_by_side else 0)
        self.policy_main.grid(row=0, column=0, sticky='nsew', padx=(0, 9 if side_by_side else 0))
        self.state_card.grid(row=0 if side_by_side else 1, column=1 if side_by_side else 0,
                             sticky='nsew', pady=(0 if side_by_side else 8, 0))
        right_width = int(width * .38)-32 if side_by_side else width-36
        self.live_label.configure(wraplength=max(130, right_width))
        self.preview_label.configure(wraplength=max(130, right_width))
        self.backup_hint.configure(wraplength=max(100, width-36))
        line_height = tkfont.Font(self, font=(FONT, 10)).metrics('linespace')
        self.notes_view.configure(height=max(6, min(24, (event.height-208)//line_height)))
        self._arrange_backup(width)

    def _arrange_backup(self, width):
        buttons = self.backup_actions
        ncols = len(buttons) if sum(b.winfo_reqwidth()+8 for b in buttons) <= width-20 else 2
        for idx, b in enumerate(buttons):
            b.grid(row=idx//ncols, column=idx%ncols, sticky='ew', padx=(0, 4), pady=2)

    def _bind_wheel(self, widget):
        widget.bindtags((self.wheel_tag, *widget.bindtags()))
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _wheel(self, event):
        if not self.active or not event.delta:
            return
        direction = -1 if event.delta > 0 else 1
        if isinstance(event.widget, tk.Text):
            top, bottom = event.widget.yview()
            if (direction < 0 and top > 0) or (direction > 0 and bottom < 1):
                return
        self.canvas.yview_scroll(direction * max(1, abs(event.delta) // 120) * 3, 'units')
        return 'break'

    def _destroyed(self, event):
        if event.widget is self:
            self.on_hide()
            self.unbind_class(self.wheel_tag, '<MouseWheel>')

    def _check(self, parent, text, var):
        check = tk.Checkbutton(parent, text=text, variable=var, onvalue=True,
                               offvalue=False, fg=INK, bg=PAPER, selectcolor=BG,
                               activebackground=PAPER, activeforeground=INK,
                               anchor='w', font=(FONT, 10), bd=0, cursor='hand2',
                               highlightthickness=1, highlightcolor=MUTED,
                               highlightbackground=PAPER, padx=0)
        check.pack(anchor='w', pady=2)
        return check

    def _heading(self, parent, text):
        label(parent, text, size=12, bold=True).pack(anchor='w', pady=(14, 5))

    def _build_policy(self):
        self.policy_main, self.state_card = card(self.policy,pad=14), card(self.policy,pad=14)
        main = self.policy_main
        header = tk.Frame(main, bg=PAPER)
        header.pack(fill='x', pady=(0, 5))
        label(header, '主动聊天', size=16, bold=True).pack(side='left')
        self.switch_images = []
        for enabled in (False, True):
            art = Image.new('RGB', (84, 44), PAPER)
            pen = ImageDraw.Draw(art)
            pen.rounded_rectangle((0, 0, 83, 43), radius=21, fill='#268749' if enabled else '#86949F')
            x = 43 if enabled else 4
            pen.ellipse((x, 4, x+35, 39), fill=PAPER)
            self.switch_images.append(ImageTk.PhotoImage(art.resize((42, 22), Image.Resampling.LANCZOS), master=self))
        self.enable_toggle = tk.Checkbutton(header, variable=self.enabled, text='启用主动聊天',
                      indicatoron=False, image=self.switch_images[0], selectimage=self.switch_images[1],
                      bg=PAPER, activebackground=PAPER, selectcolor=PAPER,
                      cursor='hand2', highlightcolor=INK, highlightbackground=PAPER,
                      highlightthickness=1, relief='flat', borderwidth=0)
        self.enable_toggle.pack(side='right')
        def field(title):
            tk.Frame(main, bg=LINE, height=1).pack(fill='x')
            row = tk.Frame(main, bg=PAPER)
            row.pack(fill='x', pady=9)
            label(row, title, bold=True).pack(side='left', padx=(0, 8))
            group = tk.Frame(row, bg=PAPER)
            group.pack(side='right', fill='x', expand=True)
            return group
        quiet = field('安静时段')
        entry(quiet, self.start, 6).pack(side='left', ipady=3)
        label(quiet, '至', color=MUTED).pack(side='left', padx=5)
        entry(quiet, self.end, 6).pack(side='left', ipady=3)
        daily = field('每日上限')
        ttk.Spinbox(daily, from_=1, to=3, textvariable=self.limit, width=4,
                    font=(FONT, 10)).pack(side='left', ipady=3)
        label(daily, '次', color=MUTED).pack(side='left', padx=5)
        interests = field('兴趣偏好')
        entry(interests, self.interests).pack(fill='x', ipady=3)
        tk.Frame(main, bg=LINE, height=1).pack(fill='x')
        self.advanced_button = button(main, '更多策略与会话偏好  ▾', self.toggle_advanced)
        self.advanced_button.pack(fill='x', pady=8)
        self.advanced = tk.Frame(main, bg=PAPER)
        self._check(self.advanced, '聊完 2–5 分钟后，合适时补一句', self.followup)
        self._check(self.advanced, '安静至少 3 小时后，偶尔自然搭话', self.idle)
        label(self.advanced, '人格补充', color=MUTED).pack(anchor='w', pady=(7, 2))
        self.style = textbox(self.advanced, 3)
        self.style.pack(fill='x')
        label(self.advanced, '重要日期（MM-DD 名称，最多 8 个）', color=MUTED).pack(anchor='w', pady=(7, 2))
        self.dates = textbox(self.advanced, 2)
        self.dates.pack(fill='x')
        self.save_button = button(main, '保存策略', self.save, primary=True,icon_name='save')
        self.save_button.pack(side='bottom', fill='x', pady=(8, 0))
        right = self.state_card
        label(right, '运行状态', size=16, bold=True).pack(anchor='w', pady=(0, 5))
        tk.Frame(right, bg=LINE, height=1).pack(fill='x')
        queued = tk.Frame(right, bg=PAPER)
        queued.pack(fill='x', pady=10)
        label(queued, '当前待发送', bold=True).pack(side='left')
        label(queued, textvariable=self.queue_size, size=11).pack(side='right')
        tk.Frame(right, bg=LINE, height=1).pack(fill='x')
        self.live_label = label(right, textvariable=self.live, color=MUTED)
        self.live_label.pack(fill='x', pady=8)
        tk.Frame(right, bg=LINE, height=1).pack(fill='x')
        label(right, '预览开场', size=11, bold=True).pack(anchor='w', pady=(10, 3))
        self.preview_label = tk.Label(right, textvariable=self.preview_text, fg=INK, bg='#F0F3F4',
                           anchor='w', justify='left', padx=8, pady=6, font=(FONT, 10))
        self.preview_label.pack(fill='x')
        preview = button(right, '生成预览（不发送）', self.preview)
        preview.pack(fill='x', pady=6)
        once = button(right, '30 秒后主动聊一句', self.send_once, primary=True)
        once.pack(side='bottom', fill='x', pady=(6, 0))
        self.policy_actions = [self.save_button, preview, once]
        self.action_buttons.extend(self.policy_actions)

    def toggle_advanced(self):
        if self.advanced.winfo_manager():
            self.advanced.pack_forget()
            self.advanced_button.configure(text='更多策略与会话偏好  ▾')
        else:
            self.advanced.pack(fill='x', pady=(0, 6), before=self.save_button)
            self.advanced_button.configure(text='收起会话偏好  ▴')

    def _build_memory(self):
        heading = tk.Frame(self.memory, bg=PAPER)
        heading.pack(fill='x', pady=(1, 7))
        label(heading, '会话记忆', size=15, bold=True).pack(side='left')
        info = tk.Frame(heading, bg=PAPER)
        info.pack(side='right')
        for name, var in zip(('近期', '记忆', '日记'), self.metrics):
            label(info, name, color=MUTED, size=9).pack(side='left', padx=(5, 0))
            label(info, textvariable=var, size=9, bold=True).pack(side='left', padx=3)
        text_holder = tk.Frame(self.memory, bg=PAPER)
        text_holder.pack(fill='both', expand=True)
        self.notes_view = textbox(text_holder, 9)
        self.notes_view.configure(highlightthickness=0, padx=5, pady=2, spacing3=3)
        bar = ttk.Scrollbar(text_holder, command=self.notes_view.yview)
        self.notes_view.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.notes_view.pack(fill='both', expand=True)
        self.notes_view.tag_configure('section', font=(FONT, 11, 'bold'), background='#F0F2F3',
                                     foreground=INK, spacing1=5, spacing3=5, lmargin1=6, lmargin2=6)
        self.notes_view.tag_configure('item', font=(FONT, 10), spacing1=4, spacing3=5,
                                     lmargin1=10, lmargin2=10)
        self.notes_view.configure(state='disabled')
        tk.Frame(self.memory, bg=LINE, height=1).pack(fill='x', pady=(8, 2))
        actions = tk.Frame(self.memory, bg=PAPER)
        actions.pack(anchor='e', pady=4)
        self.backup_actions = [button(actions, '刷新', self.refresh_notes),
                               button(actions, '加密导出', self.export),
                               button(actions, '导入备份', self.import_preview, primary=True)]
        self.action_buttons.extend(self.backup_actions[1:])
        self.backup_hint = label(self.memory,
            '每日保留最近 7 份本机加密快照。导入先核对会话；合并保留较新内容并关闭主动聊天。',
            size=9, color=MUTED)
        self.backup_hint.pack(fill='x', pady=(0, 2))

    def _controls(self):
        usable = self.active and self.ready and not self.busy
        for b in self.action_buttons:
            b.configure(state='normal' if usable else 'disabled')
        bound = self.cfg and self.cfg.group in self.cfg.conversation_targets
        self.enable_toggle.configure(state='normal' if usable and bound else 'disabled')

    def select(self, tab):
        self.tab = tab
        for key, page in [('policy', self.policy), ('memory', self.memory)]:
            self.tabs[key].configure(bg=PEACH if key == tab else PAPER,fg=INK,
                                     font=(FONT,10,'bold' if key==tab else 'normal'))
            page.pack_forget()
        (self.policy if tab == 'policy' else self.memory).pack(fill='x', padx=(0, 6))
        self.canvas.yview_moveto(0)
        if tab == 'memory' and self.active:
            self.refresh_notes()

    def on_show(self):
        if self.active:
            self.refresh_scope()
            return
        self.active = True
        self.cancel = threading.Event()
        self.refresh_scope()
        if self.ready and self.tab == 'memory':
            self.refresh_notes()
        self._status_tick()

    def on_hide(self):
        self.active = False
        self.cancel.set()
        self.pending_load = None
        if self.poll_job is not None:
            self.after_cancel(self.poll_job)
            self.poll_job = None
        if self.busy:
            self.ready = False
        self.busy = False

    def _clear_memory(self):
        for var in self.metrics:
            var.set('—')
        self._notes_text('')

    def refresh_scope(self, *, feedback=''):
        """Immediately invalidate old callbacks and clear their visible content."""
        cfg = self.panel.settings
        if self.cfg and (self.cfg.group, self.cfg.memory_scope) == (cfg.group, cfg.memory_scope):
            if self.ready or not self.active or self.pending_load:
                return
        self.cancel.set()
        self.cancel = threading.Event()
        self.cfg = cfg
        self.ready = self.busy = False
        self.pending_load = None
        self.baseline_state = getattr(self.panel.view, 'status', None)
        kind = cfg.conversation_targets.get(cfg.group, {}).get('kind')
        suffix = {'group': ' · 群聊', 'private': ' · 私聊'}.get(kind, '')
        self.scope_name.set(cfg.group + suffix)
        self.queue_size.set('—')
        self.preview_text.set('尚未生成预览')
        self.live.set('等待读取本会话状态')
        self.feedback.set('正在读取本会话设置…')
        self._clear_memory()
        self._apply_prefs(CompanionPreferences())
        self._controls()
        if not self.active:
            return
        ticket = self.cancel
        self.pending_load = ticket

        def read():
            current = self._current(cfg, ticket)
            return CompanionPreferences(**CompanionStore(current.runtime, current.memory_scope).data['preferences'])

        def loaded(prefs):
            if self._display_current(cfg, ticket):
                self.pending_load = None
                self.ready = True
                self._apply_prefs(prefs)
                bound = cfg.group in cfg.conversation_targets
                self.feedback.set(feedback or ('' if bound else '请先在会话选择器中绑定回复对象，再开启主动聊天。'))
                self._controls()
                if self.tab == 'memory':
                    self.refresh_notes()

        def failed(error):
            if self._display_current(cfg, ticket):
                self.pending_load = None
                self.feedback.set(error)

        self.panel.work(read, loaded, failed, mutates=False)

    def _apply_prefs(self, prefs):
        self.enabled.set(bool(self.cfg.group in self.cfg.conversation_targets and prefs.enabled))
        self.followup.set(prefs.followup)
        self.idle.set(prefs.idle)
        self.limit.set(str(prefs.daily_max))
        self.start.set(f'{prefs.quiet_start//60:02}:{prefs.quiet_start%60:02}')
        self.end.set(f'{prefs.quiet_end//60:02}:{prefs.quiet_end%60:02}')
        self.interests.set(prefs.interests)
        for widget, value in [(self.style, prefs.style),
                              (self.dates, '\n'.join(d['date']+' '+d['label'] for d in prefs.dates))]:
            widget.delete('1.0', 'end')
            widget.insert('1.0', value)

    def _status_tick(self):
        self.poll_job = None
        if not self.active:
            return
        if (self.cfg.group, self.cfg.memory_scope) != (self.panel.settings.group, self.panel.settings.memory_scope):
            self.refresh_scope()
        state = getattr(self.panel.view, 'status', {}) or {}
        if state is not self.baseline_state and state.get('group') == self.cfg.group:
            companion = state.get('companion') or {}
            lines = [companion.get('reason') or '当前没有陪伴状态']
            pending = next((g.get('pending_count') for g in state.get('groups', [])
                            if g.get('name') == self.cfg.group), state.get('pending_count'))
            self.queue_size.set('—' if pending is None else f'{pending} 条')
            self.preview_text.set(companion.get('preview') or '尚未生成预览')
            for key, empty in [('once_state', '尚未排程'), ('preview_state', '尚未生成')]:
                value = companion.get(key)
                if value and value != empty:
                    lines.append(value)
            self.live.set('\n'.join(lines))
        elif state.get('running') is False:
            self.live.set('未连接本地助手')
            self.queue_size.set('—')
            self.preview_text.set('尚未取得预览')
        self.poll_job = self.after(1200, self._status_tick)

    def _current(self, cfg, ticket):
        current = Settings.load(self.panel.directory)
        if ticket.is_set() or (current.group, current.memory_scope) != (cfg.group, cfg.memory_scope):
            raise ValueError('会话或页面已切换，本次操作已取消')
        return current

    def _display_current(self, cfg, ticket):
        if ticket is not self.cancel or ticket.is_set() or not self.active:
            return False
        current = self.panel.settings
        if (current.group, current.memory_scope) != (cfg.group, cfg.memory_scope):
            self.refresh_scope()
            return False
        return True

    def _run(self, action, done, *, message='', failed=None):
        if not self.ready or self.busy or not self.active:
            return
        if getattr(self.panel, 'switch_busy', False):
            self.feedback.set('正在切换会话，请稍后重试')
            return
        cfg, ticket = self.cfg, self.cancel
        self.busy = True
        self._controls()
        self.feedback.set(message)

        def work():
            return action(self._current(cfg, ticket), ticket)

        def finish(callback, value):
            if self._display_current(cfg, ticket):
                self.busy = False
                self._controls()
                callback(value)

        self.panel.work(work, lambda value: finish(done, value),
                        lambda error: finish(failed or self.feedback.set, error))

    def _write(self, action, message, *, reload=False):
        def work(cfg, ticket):
            from .selection import stop_confirmed
            state = self.panel.client.status()
            mode = state.get('mode')
            self._current(cfg, ticket)
            stop_confirmed(self.panel.directory, self.panel.client)
            try:
                action(self._current(cfg, ticket))
            finally:
                result = self.panel.client.start(initial_mode=mode if mode in ('auto', 'paused', 'observe') else 'observe')
            return result

        def show_result(text):
            if reload:
                self.ready = False
                self.refresh_scope(feedback=text)
            else:
                self.feedback.set(text)

        def saved(state):
            self.panel.show_status(state)
            show_result(message)

        self._run(work, saved, message='正在安全保存本会话…', failed=show_result)

    def values(self):
        def minutes(value):
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
                raise ValueError('免打扰时间请使用 HH:MM 格式')
            h, m = map(int, value.split(':'))
            return h * 60 + m

        dates = []
        for line in self.dates.get('1.0', 'end-1c').strip().splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) != 2:
                raise ValueError('每个重要日期需填写 MM-DD 和名称')
            dates.append(dict(date=parts[0], label=parts[1]))
        try:
            limit = int(self.limit.get())
        except ValueError:
            raise ValueError('每日上限请填 1 至 3') from None
        return CompanionPreferences(enabled=self.enabled.get(), followup=self.followup.get(),
                                    idle=self.idle.get(), daily_max=limit,
                                    quiet_start=minutes(self.start.get()), quiet_end=minutes(self.end.get()),
                                    style=self.style.get('1.0', 'end-1c').strip(),
                                    interests=self.interests.get().strip(), dates=dates)

    def save(self):
        try:
            prefs = self.values()
        except ValueError as error:
            self.feedback.set(str(error))
            return

        def write(cfg):
            if prefs.enabled and cfg.group not in cfg.conversation_targets:
                raise ValueError('请先绑定当前会话')
            notes = CompanionStore(cfg.runtime, cfg.memory_scope)
            notes.data['preferences'] = asdict(prefs)
            notes.save()

        self._write(write, '本会话策略已保存')

    def _command(self, operation, message):
        def work(cfg, ticket):
            state = self.panel.client.status()
            if state.get('group') != cfg.group:
                raise ValueError('后台回复对象尚未同步，请稍后重试')
            self._current(cfg, ticket)
            return operation()

        def done(state):
            self.panel.show_status(state)
            self.feedback.set(message)

        self._run(work, done, message='正在联系本地助手…')

    def preview(self):
        self._command(self.panel.client.companion_preview, '正在生成预览，结果将显示在陪伴状态中')

    def send_once(self):
        self._command(self.panel.client.companion_once, '主动聊天已排程，暂停助手可取消')

    def _notes_text(self, text):
        self.notes_view.configure(state='normal')
        self.notes_view.delete('1.0', 'end')
        self.notes_view.insert('1.0', text)
        self.notes_view.configure(state='disabled')

    def refresh_notes(self):
        if not self.ready or not self.active:
            return
        cfg, ticket = self.cfg, self.cancel
        request = self.note_request = object()

        def current():
            return (self.note_request is request and self.tab == 'memory' and
                    self._display_current(cfg, ticket))

        def loaded(result):
            if not current():
                return
            count, facts, days, summary, sourced, diary = result
            for var, value in zip(self.metrics, (count, facts, days)):
                var.set(str(value))
            self.notes_view.configure(state='normal')
            self.notes_view.delete('1.0', 'end')
            for title, rows in [('最近会话摘要', [summary] if summary else []),
                                ('有来源的记忆', sourced), ('本机日记', diary)]:
                self.notes_view.insert('end', title+'\n', 'section')
                for text in rows or ['暂无记录']:
                    self.notes_view.insert('end', text+'\n', 'item')
            self.notes_view.configure(state='disabled')

        self.panel.work(lambda: memory_details(self._current(cfg, ticket)), loaded,
                        lambda error: self.feedback.set(error) if current() else None, mutates=False)

    def export(self):
        if not self.active or self.busy or not self.ready:
            return
        ticket = self.cancel
        path = filedialog.asksaveasfilename(parent=self.winfo_toplevel(), title='导出加密会话备份',
                   defaultextension='.xlc', filetypes=[('小玲加密备份', '*.xlc')])
        if not path or ticket is not self.cancel or ticket.is_set():
            return
        password = simpledialog.askstring('备份口令', '设置至少 8 个字符的独立口令，遗失后无法恢复。',
                                          show='●', parent=self.winfo_toplevel())
        if not password or ticket is not self.cancel or ticket.is_set():
            return

        def work(cfg, ticket):
            from .companion_backup import export_backup
            notes = CompanionStore(cfg.runtime, cfg.memory_scope)
            memory = ConversationMemory(cfg.runtime, cfg.memory_scope)
            self._current(cfg, ticket)
            export_backup(path, password, memory, notes)

        self._run(work, lambda _: self.feedback.set('本会话加密备份已导出'), message='正在生成加密文件…')

    def import_preview(self):
        if not self.active or self.busy or not self.ready:
            return
        ticket = self.cancel
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), title='预览会话备份',
                                          filetypes=[('小玲加密备份', '*.xlc')])
        if not path or ticket is not self.cancel or ticket.is_set():
            return
        password = simpledialog.askstring('备份口令', '输入此备份的独立口令',
                                          show='●', parent=self.winfo_toplevel())
        if not password or ticket is not self.cancel or ticket.is_set():
            return

        def work(cfg, ticket):
            from .companion_backup import preview_backup
            return preview_backup(path, password, cfg.memory_scope)

        def reviewed(info):
            if ticket is not self.cancel or ticket.is_set():
                return
            prompt = (f'会话「{self.cfg.group}」的身份匹配。原文 {info["messages"]} 条，'
                      f'记忆 {info["facts"]} 条，日记 {info["diaries"]} 篇。\n\n'
                      '合并时保留本机较新内容，并关闭主动聊天。现在合并吗？')
            approved = messagebox.askyesno('导入预览', prompt, parent=self.winfo_toplevel())
            if ticket is not self.cancel or ticket.is_set():
                return
            if not approved:
                self.feedback.set('已取消导入')
                return

            def restore(cfg):
                from .companion_backup import restore_backup
                restore_backup(path, password, ConversationMemory(cfg.runtime, cfg.memory_scope),
                               CompanionStore(cfg.runtime, cfg.memory_scope))

            self._write(restore, '备份已合并，主动聊天已关闭', reload=True)

        self._run(work, reviewed, message='正在核对会话身份与备份完整性…')
