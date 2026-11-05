"""Native calibration editor. The captured frame stays in this window's memory."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from .companion_ui import BG, FONT, INK, LINE, MUTED, PAPER, button, label
from .config import Settings
from .vision import Vision


class CalibrationDialog:
    NAMES = {'header': '会话标题', 'messages': '消息区域', 'input': '输入区域', 'send': '发送按钮'}
    BLUE = '#246AE0'

    def __init__(self, panel, image, calibration):
        self.panel, self.image = panel, image
        self.calibration = deepcopy(calibration)
        self.scope = panel.settings.memory_scope
        self.open_group = panel.settings.group
        self.validating = False
        self.start = None
        self.undo_stack = []
        self.drag_snapshot = None
        self.preview = None
        self.base_image = None
        self.assignment = {key: idx for idx, key in enumerate(self.NAMES, 1)}
        self.render_job = None
        self.window = tk.Toplevel(panel.root)
        self.window.title('校准微信窗口')
        self.window.configure(bg=BG)
        self.window.transient(panel.root)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda _: self.close())
        sw, sh = self.window.winfo_screenwidth(), self.window.winfo_screenheight()
        width, height = min(985, max(300, sw - 64)), min(770, max(360, sh - 80))
        x = max(0, min(sw - width, panel.root.winfo_rootx() + 55))
        y = max(0, min(sh - height - 40, panel.root.winfo_rooty() + 30))
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        self.window.minsize(min(width, 510), min(height, 430))
        self.chosen = tk.StringVar(self.window, 'header')
        self.confirmed = tk.BooleanVar(self.window, False)
        self.message = tk.StringVar(self.window, '按 1–4 选择区域，在截图上拖动重画。Ctrl+Z 可撤销。')
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        head = tk.Frame(self.window, bg=INK)
        head.grid(row=0, column=0, sticky='ew')
        label(head, '校准微信窗口', size=17, color=PAPER, bold=True).pack(side='left', padx=16, pady=8)
        tk.Button(head, text='×', font=(FONT, 16), command=self.close,
                  bg=INK, fg=PAPER, bd=0, highlightthickness=0, cursor='hand2',
                  activebackground=MUTED, activeforeground=PAPER).pack(side='right', padx=(2, 10))
        self.target_label = label(head, '当前目标　' + (calibration.target_group or calibration.header_label),
                                  color='#D9E4EA', size=10)
        self.target_label.pack(side='right', padx=(10, 0))
        toolbar = tk.Frame(self.window, bg=BG)
        toolbar.grid(row=2, column=0, sticky='ew', padx=18, pady=(2, 7))
        self.region_labels, self.pick_regions = {}, {}
        options = [f'矩形区域 {i}' for i in range(1, 5)]
        for col, (name, title) in enumerate(self.NAMES.items()):
            toolbar.columnconfigure(col, weight=1, uniform='region')
            title_label = label(toolbar, title, size=11, bold=True, cursor='hand2')
            title_label.grid(row=0, column=col, sticky='w', padx=2, pady=(0, 3))
            title_label.bind('<Button-1>', lambda _, key=name: self.choose(key))
            self.region_labels[name] = title_label
            picker = ttk.Combobox(toolbar, values=options, width=8, state='readonly', font=(FONT, 10))
            picker.current(col)
            picker.grid(row=1, column=col, sticky='ew', padx=(0, 8 if col < 3 else 0), ipady=3)
            picker.bind('<ButtonPress-1>', lambda _, key=name: self.choose(key), add='+')
            picker.bind('<FocusIn>', lambda _, key=name: self.choose(key), add='+')
            picker.bind('<<ComboboxSelected>>', lambda _, key=name: self.assign(key))
            self.pick_regions[name] = picker
        self.canvas = tk.Canvas(self.window, bg='#E4E8E8', bd=0, highlightthickness=1,
                                highlightbackground=LINE, cursor='crosshair', height=1, width=1)
        self.canvas.grid(row=1, column=0, sticky='nsew', padx=18, pady=(12, 8))
        self.canvas.bind('<Configure>', self.resize)
        self.canvas.bind('<ButtonPress-1>', self.begin)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.end)
        self.window.bind('<Control-z>',self.undo)
        for number, key in enumerate(self.NAMES,1):
            self.window.bind(f'<KeyPress-{number}>',lambda e,k=key:self._choose_shortcut(e,k))
        footer = tk.Frame(self.window, bg=PAPER)
        footer.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 12))
        self.check = tk.Checkbutton(footer, text='已确认目标会话唯一、聊天位于底部，并且输入框没有草稿',
                              variable=self.confirmed, bg=PAPER, fg=INK, selectcolor=BG,
                              activebackground=PAPER, activeforeground=INK, anchor='w',
                              justify='left', font=(FONT, 10), padx=4, pady=5, bd=0)
        self.check.pack(fill='x')
        self.help_label = label(footer, textvariable=self.message, color=MUTED)
        self.help_label.pack(fill='x', padx=8, pady=(0, 4))
        actions = tk.Frame(footer, bg=PAPER)
        actions.pack(fill='x', padx=8, pady=(1, 6))
        self.undo_button=button(actions,'撤销调整',self.undo,icon_name='back',compact=True)
        self.undo_button.pack(side='left')
        self.undo_button.configure(state='disabled')
        self.save_button = button(actions, '保存校准', self.save, primary=True,icon_name='save')
        self.save_button.pack(side='right')
        button(actions, '取消', self.close).pack(side='right', padx=6)
        self.choose('header')

    def _choose_shortcut(self,event,key):
        if isinstance(event.widget,ttk.Combobox):return
        self.choose(key)
        return 'break'

    def _snapshot(self):
        return deepcopy(self.calibration.regions),self.assignment.copy()

    def _remember(self,snapshot):
        self.undo_stack=(self.undo_stack+[snapshot])[-16:]
        self.undo_button.configure(state='normal')

    def undo(self,_event=None):
        if not self.validating and self.undo_stack:
            self.calibration.regions,self.assignment=self.undo_stack.pop()
            self.start=self.drag_snapshot=None
            for key,number in self.assignment.items():
                self.pick_regions[key].current(number-1)
            self.confirmed.set(False)
            self.undo_button.configure(state='normal' if self.undo_stack else 'disabled')
            self.message.set('已撤销上次调整，请核对选区后保存。')
            if self.preview:self.draw()
        return 'break'

    def resize(self, event):
        if self.render_job is not None:
            self.window.after_cancel(self.render_job)
        self.render_job = self.window.after_idle(self._render)
        self.help_label.configure(wraplength=max(120, event.width - 18))
        self.check.configure(wraplength=max(120, event.width - 40))
        self.target_label.configure(wraplength=max(100, event.width // 2))

    def _render(self):
        self.render_job = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        scale = min((width-12) / self.image.width, (height-12) / self.image.height, 1)
        if scale <= 0:
            return
        size = (max(1, round(self.image.width * scale)), max(1, round(self.image.height * scale)))
        if self.preview is None or size != (self.preview.width(), self.preview.height()):
            self.base_image = self.image.convert('RGB').resize(size, Image.Resampling.LANCZOS)
            self.preview = ImageTk.PhotoImage(self.base_image, master=self.window)
        self.origin = ((width - size[0]) // 2, (height - size[1]) // 2)
        self.draw()

    def choose(self, name):
        if self.validating:
            return
        self.chosen.set(name)
        for key, item in self.region_labels.items():
            item.configure(fg=self.BLUE if key == name else INK)
        if self.preview:
            self.draw()

    def assign(self, name):
        if self.validating:
            return
        number = self.pick_regions[name].current()+1
        other = next((key for key, value in self.assignment.items() if value == number), name)
        if other != name:
            self._remember(self._snapshot())
            self.assignment[name], self.assignment[other] = self.assignment[other], self.assignment[name]
            regions = self.calibration.regions
            regions[name], regions[other] = regions[other], regions[name]
            self.pick_regions[other].current(self.assignment[other]-1)
            self.confirmed.set(False)
        self.choose(name)

    def draw(self):
        self.canvas.delete('all')
        left, top = self.origin
        width, height = self.preview.width(), self.preview.height()
        surface = self.base_image.copy()
        pen = ImageDraw.Draw(surface, 'RGBA')
        for key, ratios in self.calibration.regions.items():
            if key not in self.NAMES:
                continue
            x1, y1, x2, y2 = ratios
            pen.rectangle((x1*width, y1*height, x2*width, y2*height),
                          fill=(60, 132, 241, 35 if key == self.chosen.get() else 22))
        self.preview.paste(surface)
        self.canvas.create_image(left, top, image=self.preview, anchor='nw')
        for key, ratios in self.calibration.regions.items():
            if key not in self.NAMES:
                continue
            x1, y1, x2, y2 = ratios
            coords = (left + x1*width, top + y1*height, left + x2*width, top + y2*height)
            selected=key==self.chosen.get()
            stroke=self.BLUE if selected else '#5E768D'
            self.canvas.create_rectangle(*coords,outline=stroke,dash=() if selected else (5,3),
                                         width=2 if selected else 1)
            self.canvas.create_text(coords[2]-5, coords[1]+2, anchor='ne',
                                    text=f'{self.assignment[key]} · {self.NAMES[key]}' if selected else str(self.assignment[key]), fill=stroke,
                                    font=(FONT, 8, 'bold'))
            for x in (coords[0], coords[2]) if selected else ():
                for y in (coords[1], coords[3]):
                    self.canvas.create_rectangle(x-3, y-3, x+3, y+3, outline=self.BLUE, fill=PAPER)

    def point(self, event, *, clamp):
        if not self.preview:
            return None
        x, y = event.x - self.origin[0], event.y - self.origin[1]
        width, height = self.preview.width(), self.preview.height()
        if not clamp and (x < 0 or y < 0 or x > width or y > height):
            return None
        return (min(width, max(0, x))/width, min(height, max(0, y))/height)

    def begin(self, event):
        if not self.validating:
            self.start = self.point(event, clamp=False)
            self.drag_snapshot=self._snapshot() if self.start is not None else None

    def drag(self, event):
        if self.validating or self.start is None:
            return
        pos = self.point(event, clamp=True)
        x1, x2 = sorted([self.start[0], pos[0]])
        y1, y2 = sorted([self.start[1], pos[1]])
        if (x2-x1)*self.preview.width() <= 4 or (y2-y1)*self.preview.height() <= 4:
            return
        if self.drag_snapshot is not None:
            self._remember(self.drag_snapshot)
            self.drag_snapshot=None
        self.calibration.regions[self.chosen.get()] = [x1, y1, x2, y2]
        self.confirmed.set(False)
        self.draw()

    def end(self, event):
        self.drag(event)
        self.start = None
        self.drag_snapshot=None

    def alive(self):
        return bool(self.window.winfo_exists())

    def close(self):
        if self.render_job is not None:
            self.window.after_cancel(self.render_job)
            self.render_job = None
        self.window.destroy()

    def save(self):
        if self.validating:
            return
        if not self.confirmed.get():
            self.message.set('请先确认目标、聊天底部和空白输入框。')
            return
        frozen = deepcopy(self.calibration)
        frozen.bottom_confirmed = True
        try:
            frozen.validate()
        except ValueError as error:
            self.message.set(str(error))
            return
        self.validating = True
        self.undo_button.configure(state='disabled')
        self.save_button.configure(state='disabled')
        self.check.configure(state='disabled')
        for picker in self.pick_regions.values():
            picker.configure(state='disabled')
        self.message.set('正在本机核对目标、聊天底部和草稿…')

        def validate():
            cfg = Settings.load(self.panel.directory)
            if (cfg.group, cfg.memory_scope) != (self.open_group, self.scope):
                raise ValueError('校准期间会话已切换，请重新打开校准')
            database = cfg.receive_mode == 'database'
            frame = Vision().read(self.image, frozen, controls_only=database)
            if not frame.safe or not frame.at_bottom:
                raise ValueError(frame.reason or '无法确认聊天底部')
            if not frame.draft_empty:
                raise ValueError('输入区域非空或选区包含图标，请调整')
            if not database and not frame.messages:
                raise ValueError('请选择包含可识别消息气泡的区域')
            return frozen

        def valid(value):
            if not self.alive():
                return
            if (self.panel.settings.memory_scope != self.scope or
                self.panel.settings.group != self.open_group or getattr(self.panel, 'switch_busy', False)):
                failed('校准期间会话已切换，请重新打开校准')
                return
            self.close()
            self.panel.save_calibration(value)

        def failed(error):
            if self.alive():
                self.validating = False
                self.undo_button.configure(state='normal' if self.undo_stack else 'disabled')
                self.check.configure(state='normal')
                self.save_button.configure(state='normal')
                for picker in self.pick_regions.values():
                    picker.configure(state='readonly')
                self.message.set(error)

        self.panel.work(validate, valid, failed)
